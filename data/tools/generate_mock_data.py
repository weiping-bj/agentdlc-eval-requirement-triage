#!/usr/bin/env python3
"""重新生成 mock 输入数据（“上个月的需求反馈”），可选同时生成黄金集草稿。

    python3 data/tools/generate_mock_data.py                      # 按 data/templates/mock-data-params.yaml 生成
    python3 data/tools/generate_mock_data.py --params my.yaml --out /tmp/feedback.jsonl
    python3 data/tools/generate_mock_data.py --write-golden --force   # 同时覆盖黄金集（慎用）

数据来源：data/tools/seed/pool.jsonl（60 条带标注的种子需求，含 _meta 标签），
加上参数文件里 extra_items_file 指向的、由 coding agent 按现场反馈新写的条目（同样格式）。
默认参数会原样复现仓库里的 data/feedback_last_month.jsonl（测试会校验）。
"""
from __future__ import annotations

import argparse
import os
import random
import re
import sys
from collections import Counter, OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402
import golden_lib as G  # noqa: E402

CATEGORY_CN = OrderedDict([
    ("gap_generic", "通用能力缺口（应迭代）"), ("existing_capability", "已有能力被重复提出（应拒绝并给用法）"),
    ("customer_specific", "单客户深度定制（应拒绝并给替代）"), ("out_of_scope", "超出平台定位（应拒绝）"),
    ("infeasible_or_redline", "技术不可行或违背安全合规（应拒绝）"), ("vague", "描述模糊/多诉求混合（待评估）"),
    ("incident", "线上故障/缺陷（不可拒绝）"), ("high_value_hard", "价值高但难度大（待评估）"),
    ("adversarial", "对抗样本（注入指令/施压）"), ("compliance_contract", "合规或合同承诺（不可拒绝）"),
])


def load_params(path: str) -> dict:
    p = C.load_yaml(path) if path and os.path.exists(path) else {}
    return p or {}


def _resolve(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(C.REPO, path)


def build(params: dict, pool: list, extra: list) -> tuple:
    """Return (items_with_meta, warnings). Deterministic for a given params+pool."""
    warnings = []
    seed = int(params.get("seed", 20261107))
    rng = random.Random(seed)
    src = pool + extra
    by_cat = OrderedDict()
    for r in src:
        by_cat.setdefault(r["_meta"]["category"], []).append(r)
    natural = Counter(r["_meta"]["category"] for r in src)
    count = int(params.get("count") or len(pool))
    mix = params.get("category_mix") or {}
    keep_golden = params.get("keep_golden_items", True)

    if not mix and count == len(src):
        chosen = list(src)
    else:
        if mix:
            total = sum(float(v) for v in mix.values()) or 1.0
            want = {k: int(round(count * float(v) / total)) for k, v in mix.items()}
        else:
            want = {k: int(round(count * v / len(src))) for k, v in natural.items()}
        diff = count - sum(want.values())
        if diff and want:
            big = max(want, key=lambda k: want[k])
            want[big] = max(0, want[big] + diff)
        chosen = []
        for cat, n in want.items():
            avail = list(by_cat.get(cat, []))
            if cat not in CATEGORY_CN and not avail:
                warnings.append(f"未知类别 {cat}（可选：{', '.join(CATEGORY_CN)}）")
                continue
            golden_first = [r for r in avail if keep_golden and r["_meta"].get("golden_id")]
            others = [r for r in avail if r not in golden_first]
            rng.shuffle(others)
            take = (golden_first + others)[:n]
            if len(take) < n:
                warnings.append(f"类别“{CATEGORY_CN.get(cat, cat)}”需要 {n} 条，种子库只有 {len(take)} 条："
                                f"请按 data/prompts/02-regenerate-mock-data.md 让 coding agent 补写到 extra_items_file")
            chosen.extend(take)
        if keep_golden:
            missing_g = [r for r in src if r["_meta"].get("golden_id") and r not in chosen]
            if missing_g:
                warnings.append(f"{len(missing_g)} 条黄金集样本因类别配额未被选中，已强制保留")
                chosen.extend(missing_g)

    # optional value overrides
    industries = params.get("industries") or []
    channels = params.get("channels") or []
    out = []
    for r in chosen:
        r = dict(r)
        r["_meta"] = dict(r["_meta"])
        if industries:
            # deterministic (no hash randomisation): same customer -> same industry
            r["industry"] = industries[sum(map(ord, r["customer"])) % len(industries)]
        if channels and r.get("channel") not in channels:
            r["channel"] = channels[sum(map(ord, r["title"])) % len(channels)]
        out.append(r)

    month = params.get("month")  # "YYYY-MM"
    if month:
        y, m = month.split("-")
        for r in out:
            d = r.get("submitted_at") or f"{y}-{m}-01"
            day = min(int(d[-2:]), 28)
            r["submitted_at"] = f"{y}-{int(m):02d}-{day:02d}"
    out.sort(key=lambda r: (r.get("submitted_at") or "", r.get("id") or ""))
    # Stable ids: keep the numeric suffix of existing ids (the golden set references them); only swap the
    # prefix when month/id_prefix is set, and number new or duplicate items after the current maximum.
    prefix = params.get("id_prefix")
    if prefix is None and month:
        prefix = f"FB-{month[2:4]}{month[5:7]}-"
    rx = re.compile(r"^(.*?)(\d+)$")
    nums = [int(m.group(2)) for m in (rx.match(str(r.get("id") or "")) for r in out) if m]
    nxt = (max(nums) if nums else 0) + 1
    seen = set()
    for r in out:
        old_id = str(r.get("id") or "")
        m = rx.match(old_id)
        new_id = f"{prefix}{int(m.group(2)):03d}" if (prefix and m) else old_id
        if not new_id or new_id in seen:
            new_id = f"{prefix or 'FB-NEW-'}{nxt:03d}"
            nxt += 1
        seen.add(new_id)
        r["_meta"]["source_id"] = old_id
        r["id"] = new_id
    return out, warnings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--params", default=os.path.join(C.templates_dir(), "mock-data-params.yaml"))
    ap.add_argument("--pool", default=C.POOL_PATH)
    ap.add_argument("--out", default=C.FEEDBACK_PATH)
    ap.add_argument("--csv-out", default=None, help="同时导出 Excel 友好 CSV（默认与 --out 同名 .csv）")
    ap.add_argument("--write-golden", action="store_true", help="同时用种子标注生成黄金集（覆盖 data/golden/golden_set.jsonl）")
    ap.add_argument("--golden-out", default=C.GOLDEN_PATH)
    ap.add_argument("--force", action="store_true", help="允许覆盖已存在的黄金集")
    a = ap.parse_args(argv)

    params = load_params(a.params)
    pool = C.read_jsonl(a.pool)
    extra = []
    if params.get("extra_items_file"):
        ep = _resolve(params["extra_items_file"])
        if os.path.exists(ep):
            extra = C.read_jsonl(ep)
            bad = [i for i, r in enumerate(extra, 1) if "_meta" not in r or "category" not in r.get("_meta", {})]
            if bad:
                print(f"错误：{ep} 第 {bad} 行缺少 _meta.category（格式见 data/prompts/02-regenerate-mock-data.md）")
                return 2
    items, warns = build(params, pool, extra)
    feedback = [G.input_of(r) for r in items]
    C.write_jsonl(a.out, feedback)
    csv_out = a.csv_out or os.path.splitext(a.out)[0] + ".csv"
    C.write_csv(csv_out, feedback, C.INPUT_FIELDS)
    print(f"已生成 {len(feedback)} 条需求反馈 → {os.path.relpath(a.out, C.REPO)}（CSV：{os.path.relpath(csv_out, C.REPO)}）")
    cats = Counter(r["_meta"]["category"] for r in items)
    for k, v in cats.most_common():
        print(f"  - {CATEGORY_CN.get(k, k)}：{v}")
    if os.path.abspath(a.out) == os.path.abspath(C.FEEDBACK_PATH):
        import mock_views as MV  # noqa: E402  (lazy: mock_views imports this module)
        # default params (as sync_shared --check uses), so the page never flips between the two
        if MV.CV.sync(MV.planned(), check=False):
            print(f"已刷新讨论用的网页版：{os.path.relpath(MV.HTML_PATH, C.REPO)}")
    id_map = {r["_meta"]["source_id"]: r["id"] for r in items if r["_meta"].get("source_id") and r["_meta"]["source_id"] != r["id"]}
    if not a.write_golden and os.path.exists(a.golden_out):
        golden = C.read_jsonl(a.golden_out)
        by_id = {r["id"]: r for r in feedback}
        touched = 0
        for g in golden:
            fid = g.get("feedback_id")
            if fid in id_map:
                g["feedback_id"] = id_map[fid]
                g.setdefault("input", {})["id"] = id_map[fid]
                if by_id.get(g["feedback_id"], {}).get("submitted_at"):
                    g["input"]["submitted_at"] = by_id[g["feedback_id"]]["submitted_at"]
                touched += 1
        missing = [g["golden_id"] for g in golden if g.get("feedback_id") not in by_id]
        moved = [g["golden_id"] for g in golden if g.get("feedback_id") in by_id
                 and by_id[g["feedback_id"]].get("title") != (g.get("input") or {}).get("title")]
        if touched:
            import golden_tool as GT  # noqa: E402  (backup helper)
            bk = GT._backup(a.golden_out)
            C.write_jsonl(a.golden_out, golden)
            print(f"需求编号有变化：已同步更新黄金集中 {touched} 条的 feedback_id（备份：{bk}）；接着运行 golden_tool.py freeze")
        if missing:
            print(f"警告：黄金集 {', '.join(missing[:10])} 引用的需求不在新生成的输入里（被配额去掉了？）")
        if moved:
            print(f"警告：黄金集 {', '.join(moved[:10])} 与同编号输入的标题不一致，请检查")
    if a.write_golden:
        if os.path.exists(a.golden_out) and not a.force:
            print("黄金集已存在，未覆盖（加 --force 覆盖；现场修改黄金集请用 golden_tool.py apply-feedback）")
        else:
            version = str(params.get("golden_version") or "v1")
            rows = [G.golden_from_pool(r, version) for r in items if r["_meta"].get("golden_id")]
            rows.sort(key=lambda r: r["golden_id"])
            C.write_jsonl(a.golden_out, rows)
            print(f"已生成黄金集 {len(rows)} 条 → {os.path.relpath(a.golden_out, C.REPO)}；接着运行 golden_tool.py export 与 freeze")
    for w in warns:
        print("警告：" + w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
