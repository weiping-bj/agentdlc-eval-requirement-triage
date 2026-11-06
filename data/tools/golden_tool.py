#!/usr/bin/env python3
"""黄金集工具：校验 / 统计 / 导出 / 按讨论反馈修改 / 冻结版本 / 同步到评估器。

    python3 data/tools/golden_tool.py validate                # 校验格式与分布（有错误时退出码 1）
    python3 data/tools/golden_tool.py stats                   # 打印标签分布
    python3 data/tools/golden_tool.py export                  # 生成 golden_set.csv 与 agentcore_dataset.json
    python3 data/tools/golden_tool.py feedback-template       # 用当前黄金集预填 data/templates/golden-set-feedback.csv
    python3 data/tools/golden_tool.py apply-feedback [--feedback CSV] [--version v2]   # 按模板修改黄金集
    python3 data/tools/golden_tool.py fill-levels             # 为缺少优先级的条目按“重要/紧急”推导 P0/P1/P2
    python3 data/tools/golden_tool.py freeze [--version v2]   # 校验 + 导出 + 写 MANIFEST.json + 同步到评估器
    python3 data/tools/golden_tool.py sync-evaluators         # 把黄金集/能力目录打包进 evaluators/*/

修改前的黄金集会自动备份到 data/golden/history/。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402
import golden_lib as G  # noqa: E402


def _load(path):
    return C.read_jsonl(path) if os.path.exists(path) else []


def _catalog_ids():
    if not os.path.exists(C.CATALOG_PATH):
        return None
    with open(C.CATALOG_PATH, encoding="utf-8") as f:
        doc = json.load(f)
    caps = doc.get("capabilities", []) if isinstance(doc, dict) else doc
    return {c.get("id") for c in caps}


def _backup(path):
    if not os.path.exists(path):
        return None
    hist = os.path.join(os.path.dirname(path), "history")
    os.makedirs(hist, exist_ok=True)
    stem = datetime.now().strftime("golden_set.%Y%m%d-%H%M%S-%f")
    dst = os.path.join(hist, stem + ".jsonl")
    k = 1
    while os.path.exists(dst):  # never overwrite an earlier backup
        dst = os.path.join(hist, f"{stem}-{k}.jsonl")
        k += 1
    shutil.copyfile(path, dst)
    return dst


def _sha(path) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _applied_log(golden_path) -> str:
    return os.path.join(os.path.dirname(golden_path), "history", "applied-feedback.json")


def cmd_validate(a) -> int:
    rows = _load(a.golden)
    if not rows:
        print(f"错误：黄金集为空或不存在：{a.golden}")
        return 1
    errors, warns = G.validate(rows, a.scheme, _catalog_ids())
    print(G.stats_markdown(rows))
    for w in warns:
        print("警告：" + w)
    for e in errors:
        print("错误：" + e)
    print("校验通过" if not errors else f"校验失败：{len(errors)} 个错误")
    return 1 if errors else 0


def cmd_stats(a) -> int:
    rows = _load(a.golden)
    print(G.stats_markdown(rows))
    if a.json:
        print(json.dumps(G.stats(rows), ensure_ascii=False, indent=1))
    return 0


def export(golden_path: str) -> None:
    rows = _load(golden_path)
    d = os.path.dirname(golden_path)
    C.write_csv(os.path.join(d, "golden_set.csv"), G.to_csv_rows(rows), G.GOLDEN_CSV_FIELDS)
    C.write_json(os.path.join(d, "agentcore_dataset.json"), G.to_agentcore_dataset(rows))


def cmd_export(a) -> int:
    export(a.golden)
    print("已导出 golden_set.csv 与 agentcore_dataset.json")
    return 0


def cmd_feedback_template(a) -> int:
    rows = _load(a.golden)
    out = a.out or os.path.join(C.templates_dir(), "golden-set-feedback.csv")
    if os.path.exists(out) and not a.force:
        existing = C.read_csv(out)
        fresh = {r["golden_id"]: r for r in G.to_feedback_rows(rows)}

        def edited(r):
            if (r.get("操作") or "保留") != "保留" or (r.get("备注") or "").strip():  # a note is an edit too
                return True
            cur = fresh.get(r.get("golden_id"))
            return cur is None or any((r.get(k) or "").strip() != ("" if cur.get(k) is None else str(cur.get(k))).strip()
                                      for k in G.FEEDBACK_CSV_FIELDS if k not in ("操作", "备注"))
        if any(edited(r) for r in existing):
            print(f"{out} 里已有修改内容，未覆盖（加 --force 覆盖）")
            return 1
    C.write_csv(out, G.to_feedback_rows(rows), G.FEEDBACK_CSV_FIELDS)
    print(f"已预填 {len(rows)} 行 → {os.path.relpath(out, C.REPO)}（在“操作”列填 保留/修改/删除/新增）")
    return 0


def cmd_apply(a) -> int:
    rows = _load(a.golden)
    fb_path = a.feedback or os.path.join(C.templates_dir(), "golden-set-feedback.csv")
    fb = C.read_csv(fb_path)
    fb_sha = _sha(fb_path)
    applied_path = _applied_log(a.golden)
    applied = json.load(open(applied_path, encoding="utf-8")) if os.path.exists(applied_path) else {}
    if fb_sha in applied and not a.force and not a.dry_run:
        print(f"这份反馈表已在 {applied[fb_sha]} 应用过，未重复应用（内容有变化会自动识别；确需重复应用加 --force）")
        return 1
    pool = {r["id"]: r for r in _load(C.FEEDBACK_PATH)}
    version = a.version or _bump(rows)
    new_rows, log = G.apply_feedback(rows, fb, version, pool)
    errors, warns = G.validate(new_rows, a.scheme, _catalog_ids())
    bad_ops = G.unknown_ops(fb)
    for line in log:
        print(line)

    def unknown_summary():
        if not bad_ops:
            return
        print("")
        print("!" * 66)
        print(f"❌ 有 {len(bad_ops)} 行的“操作”无法识别，这些行的修改【没有生效】：")
        for n, key, raw in bad_ops:
            print(f"   - CSV 第 {n} 行 {key or '(无编号)'}：操作 = {raw!r}")
        print(f"   “操作”列只能填：{G.OP_CHOICES}（也接受 修改标签 / 新增样例 等写法）")
        print("!" * 66)

    acc_warns = [line for line in log if G.ACC_WARN in line]

    def acceptable_summary():
        if not acc_warns:
            return
        print("")
        print("!" * 66)
        print(f"⚠️ 有 {len(acc_warns)} 行的“可接受建议”没有包含“处理建议”，会被改成只接受处理建议：")
        for line in acc_warns:
            print("   - " + line.replace("警告：", "", 1))
        print("   确实只要一个答案可以不管；否则在可接受建议里加上处理建议（用 / 分隔）后重跑")
        print("!" * 66)

    if (errors or bad_ops) and not a.allow_errors:
        for e in errors:
            print("错误：" + e)
        unknown_summary()
        acceptable_summary()
        print("未写入：请修正模板后重试（或加 --allow-errors 先写入草稿）")
        return 1
    if a.dry_run:
        unknown_summary()
        print("（dry-run，未写入）")
        print(G.stats_markdown(new_rows))
        for w in warns:
            print("警告：" + w)
        acceptable_summary()
        return 0
    bk = _backup(a.golden)
    C.write_jsonl(a.golden, new_rows)
    export(a.golden)
    applied[fb_sha] = datetime.now().isoformat(timespec="seconds") + f" → {version}"
    C.write_json(applied_path, applied)
    print(f"已写入 {len(new_rows)} 条（版本 {version}），备份：{os.path.relpath(bk, C.REPO) if bk else '无'}")
    unknown_summary()
    print(G.stats_markdown(new_rows))
    for w in warns:
        print("警告：" + w)
    acceptable_summary()
    print("下一步：python3 data/tools/golden_tool.py freeze")
    return 0


def _bump(rows) -> str:
    vs = [r.get("version") or "v1" for r in rows]
    nums = [int(v[1:]) for v in vs if v.startswith("v") and v[1:].isdigit()]
    return f"v{(max(nums) if nums else 1) + 1}"


def cmd_fill_levels(a) -> int:
    rows = _load(a.golden)
    n = 0
    for r in rows:
        e = r.setdefault("expected", {})
        if e.get("level") not in C.LEVELS:
            e["level"] = C.derive_level(e.get("important"), e.get("urgent"))
            n += 1
    _backup(a.golden)
    C.write_jsonl(a.golden, rows)
    export(a.golden)
    print(f"已为 {n} 条补充优先级（重要且紧急=P0，重要不紧急=P1，其它=P2），请人工复核")
    return 0


def sync_evaluators(golden_path: str = C.GOLDEN_PATH) -> int:
    script = os.path.join(C.REPO, "evaluators", "sync_shared.py")
    if not os.path.exists(script):
        print("未找到 evaluators/sync_shared.py，跳过同步")
        return 0
    return subprocess.call([sys.executable, script, "--golden", os.path.abspath(golden_path)])


def cmd_freeze(a) -> int:
    rows = _load(a.golden)
    if a.version:
        rows = [dict(r, version=a.version) for r in rows]
    errors, warns = G.validate(rows, a.scheme, _catalog_ids())
    if errors:
        for e in errors:
            print("错误：" + e)
        print("校验失败，未冻结（文件未改动）")
        return 1
    if a.version:
        _backup(a.golden)
        C.write_jsonl(a.golden, rows)
    export(a.golden)
    sha = _sha(a.golden)
    versions = sorted({r.get("version") or "v1" for r in rows})
    rel = os.path.relpath(os.path.abspath(a.golden), C.REPO)
    manifest = {"file": rel if not rel.startswith("..") else os.path.abspath(a.golden), "sha256": sha, "count": len(rows), "versions": versions,
                "label_scheme": a.scheme, "frozen_at": datetime.now().isoformat(timespec="seconds"),
                "stats": G.stats(rows), "warnings": warns}
    C.write_json(os.path.join(os.path.dirname(a.golden), "MANIFEST.json"), manifest)
    print(G.stats_markdown(rows))
    print(f"已冻结：{len(rows)} 条，sha256={sha[:12]}…，版本 {','.join(versions)}")
    rc = sync_evaluators(a.golden)
    print("下一步：重新部署评估器 Lambda，让新黄金集生效（见 evaluators/README.md）")
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--golden", default=C.GOLDEN_PATH)
    ap.add_argument("--scheme", choices=["binary", "level"], default="binary", help="标签口径：二元（默认）或分级")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    s = sub.add_parser("stats")
    s.add_argument("--json", action="store_true")
    sub.add_parser("export")
    s = sub.add_parser("feedback-template")
    s.add_argument("--out")
    s.add_argument("--force", action="store_true")
    s = sub.add_parser("apply-feedback")
    s.add_argument("--feedback")
    s.add_argument("--version")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--allow-errors", action="store_true")
    s.add_argument("--force", action="store_true", help="允许重复应用同一份反馈表")
    sub.add_parser("fill-levels")
    s = sub.add_parser("freeze")
    s.add_argument("--version")
    sub.add_parser("sync-evaluators")
    a = ap.parse_args(argv)
    return {"validate": cmd_validate, "stats": cmd_stats, "export": cmd_export, "feedback-template": cmd_feedback_template,
            "apply-feedback": cmd_apply, "fill-levels": cmd_fill_levels, "freeze": cmd_freeze,
            "sync-evaluators": lambda _a: sync_evaluators(_a.golden)}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
