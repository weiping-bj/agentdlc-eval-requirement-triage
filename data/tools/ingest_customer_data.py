#!/usr/bin/env python3
"""客户自带数据：校验是否够用 + 标准化成实验统一格式。

    python3 data/tools/ingest_customer_data.py --in 客户需求导出.xlsx
    python3 data/tools/ingest_customer_data.py --in feedback.csv --mapping data/templates/column-mapping.example.json
    python3 data/tools/ingest_customer_data.py --in feedback.json --out-dir data/customer --use-as-input

支持 .csv / .xlsx / .json / .jsonl。列名自动识别（中英文常见叫法），识别不了的用 --mapping 指定。
输出（默认 data/customer/，请勿提交到公开仓库）：
  feedback.normalized.jsonl   统一格式的需求反馈（字段同 data/feedback_last_month.jsonl）
  golden_candidates.jsonl     如果客户数据里带有历史判断（是否重要/是否紧急/处理建议），可作为黄金集候选
  report.md                   数据充分性报告（中文）：字段覆盖、文本质量、个人信息、能否支撑三项判断
--use-as-input 会在 data/customer/agent_data/ 下生成 Agent 可直接读取的数据目录
（feedback_last_month.jsonl + capability_catalog.json），再用 S12_DATA_DIR 指向它；不会改动仓库里被跟踪的 mock 文件。
默认对所有文本字段（含历史拒绝理由/回复）中的手机号、座机、邮箱、身份证号打码（--no-mask 关闭）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

SYNONYMS = {
    "id": ["id", "需求编号", "需求id", "编号", "反馈编号", "工单号", "工单编号", "需求号", "feedback_id", "requirement_id", "ticket_id", "序号"],
    "title": ["title", "标题", "需求标题", "需求名称", "主题", "summary", "名称", "需求概述"],
    "content": ["content", "description", "需求描述", "描述", "详情", "需求详情", "内容", "需求内容", "问题描述", "detail", "body", "反馈内容"],
    "customer": ["customer", "customer_name", "客户", "客户名称", "租户", "租户名称", "企业名称", "tenant", "account"],
    "industry": ["industry", "行业", "客户行业", "所属行业"],
    "customer_tier": ["customer_tier", "客户等级", "客户级别", "客户分级", "客户类型", "tier", "level_of_customer"],
    "channel": ["channel", "来源", "来源渠道", "渠道", "提出渠道", "source"],
    "module": ["module", "模块", "产品模块", "相关模块", "功能模块", "所属模块", "产品线", "category"],
    "submitted_at": ["submitted_at", "提交时间", "创建时间", "提出时间", "反馈时间", "日期", "created_at", "date", "提交日期"],
    "feedback_type": ["feedback_type", "反馈类型", "需求类型", "类型", "问题类型", "type"],
    "contract_commitment": ["contract_commitment", "合同承诺", "是否合同承诺", "合同约定"],
    # optional historic labels -> golden candidates
    "label_important": ["是否重要", "重要", "important", "is_important", "重要性"],
    "label_urgent": ["是否紧急", "紧急", "urgent", "is_urgent", "紧急程度", "紧急度"],
    "label_decision": ["处理建议", "处理结果", "处理结论", "决策", "decision", "action"],  # generic 状态 columns need --mapping
    "label_level": ["优先级", "priority", "等级", "level"],
    "label_reject_reason": ["拒绝理由", "驳回原因", "拒绝原因", "reject_reason"],
    "label_reply": ["回复话术", "客户回复", "回复客户", "话术", "customer_reply"],
}
TIER_MAP = {"战略": "战略", "s": "战略", "strategic": "战略", "ka": "KA", "重点": "KA", "大客户": "KA", "a": "KA",
            "腰部": "腰部", "b": "腰部", "中型": "腰部", "普通": "腰部", "长尾": "长尾", "c": "长尾", "小客户": "长尾", "smb": "长尾"}
TYPE_RULES = [("功能缺陷", ("缺陷", "bug", "故障", "报错", "问题单", "异常")), ("体验优化", ("优化", "体验", "易用")),
              ("咨询", ("咨询", "疑问", "如何", "question")), ("新需求", ("需求", "新功能", "feature", "功能"))]
PII = {
    "邮箱": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "身份证号": re.compile(r"(?<!\d)(?:\d{17}[\dXx]|\d{15})(?![\dXx])"),
    "手机号": re.compile(r"(?<![\d+])(?:\+?86[-\s]?)?1[3-9]\d(?:[-\s]?\d{4}){2}(?!\d)"),
    "座机": re.compile(r"(?<!\d)(?:\(?0\d{2,3}\)?[-\s]?)\d{7,8}(?!\d)"),
}
NO_MASK_FIELDS = ("id", "submitted_at")
INCIDENT_WORDS = ("故障", "报错", "丢失", "无法", "失败", "宕机", "打不开", "泄露", "漏洞", "错误", "异常")


def _key(s: str) -> str:
    return re.sub(r"[\s_\-（）()：:]", "", str(s or "")).lower()


def auto_map(columns, explicit=None):
    mapping, used = {}, set()
    explicit = explicit or {}
    for col, target in explicit.items():
        if col in columns:
            mapping[col] = target
            used.add(target)
    syn = {t: {_key(x) for x in names} for t, names in SYNONYMS.items()}
    for col in columns:
        if col in mapping:
            continue
        k = _key(col)
        for target, names in syn.items():
            if target in used:
                continue
            if k in names:
                mapping[col] = target
                used.add(target)
                break
    return mapping


def mask(text: str, hits: Counter) -> str:
    def rep(kind):
        def f(m):
            hits[kind] += 1
            s = m.group(0)
            return s[:3] + "****" + s[-2:] if kind != "邮箱" else "***@" + s.split("@", 1)[1]
        return f
    for kind, rx in PII.items():
        text = rx.sub(rep(kind), text)
    return text


def norm_tier(v):
    s = str(v or "").strip()
    return TIER_MAP.get(s.lower(), s) if s else None


def norm_type(v):
    """Map free-form type labels to 新需求/功能缺陷/体验优化/咨询; unknown labels are kept as-is."""
    s = str(v or "").strip().lower()
    if not s:
        return None
    for canon, words in TYPE_RULES:
        if s == canon or any(w in s for w in words):
            return canon
    return str(v).strip()


def normalize(rows, mapping, do_mask=True):
    out, cands, pii = [], [], Counter()
    seen = Counter()
    for i, raw in enumerate(rows, 1):
        r = {mapping[k]: v for k, v in raw.items() if k in mapping and v not in (None, "")}
        extra = {k: v for k, v in raw.items() if k not in mapping and v not in (None, "")}
        item = {}
        for f in C.INPUT_FIELDS:
            v = r.get(f)
            if v is None:
                continue
            v = str(v).strip()
            if do_mask and f not in NO_MASK_FIELDS:
                v = mask(v, pii)
            item[f] = v
        if not item.get("id"):
            item["id"] = f"CUST-{i:04d}"
        seen[item["id"]] += 1
        if seen[item["id"]] > 1:
            item["id"] = f"{item['id']}-{seen[item['id']]}"
        if not item.get("title") and item.get("content"):
            item["title"] = item["content"][:30]
        if not item.get("content") and item.get("title"):
            item["content"] = item["title"]
        if "submitted_at" in item:
            item["submitted_at"] = C.norm_date(item["submitted_at"]) or item["submitted_at"]
        if "customer_tier" in item:
            item["customer_tier"] = norm_tier(item["customer_tier"])
        if "feedback_type" in item:
            item["feedback_type"] = norm_type(item["feedback_type"]) or item["feedback_type"]
        if "contract_commitment" in item:
            item["contract_commitment"] = C.norm_yes_no(item["contract_commitment"]) or item["contract_commitment"]
        if extra:
            item["extra"] = {k: (mask(str(v), pii) if do_mask else v) for k, v in extra.items()}
        out.append(item)
        lab = {"important": C.norm_yes_no(r.get("label_important")), "urgent": C.norm_yes_no(r.get("label_urgent")),
               "decision": C.norm_decision(r.get("label_decision")), "level": C.norm_level(r.get("label_level"))}
        if any(lab[k] for k in ("important", "urgent", "decision")):
            cands.append({"feedback_id": item["id"], "input": {k: v for k, v in item.items() if k != "extra"},
                          "expected": lab, "acceptable_decisions": [lab["decision"]] if lab["decision"] else [],
                          "reject_points": [mask(x, pii) if do_mask else x for x in C.split_list(r.get("label_reject_reason"))],
                          "reply_points": [mask(x, pii) if do_mask else x for x in C.split_list(r.get("label_reply"))],
                          "complete": all(lab[k] for k in ("important", "urgent", "decision")), "source": "customer"})
    return out, cands, pii


def assess(items, mapping, cands, pii, n_raw):
    n = len(items) or 1
    cov = {f: sum(1 for it in items if it.get(f)) / n for f in C.INPUT_FIELDS}
    lens = [len(it.get("content") or "") for it in items]
    short = sum(1 for x in lens if x < 15)
    dup_text = sum(v - 1 for v in Counter((it.get("title"), it.get("content")) for it in items).values() if v > 1)
    incident_like = sum(1 for it in items if any(w in (it.get("content") or "") for w in INCIDENT_WORDS))
    checks = []

    def chk(name, ok, need, fix):
        checks.append((name, ok, need, fix))
    chk("需求原文", cov["content"] >= 0.95 and short / n <= 0.3, "≥95% 有描述，且过短（<15 字）不超过 30%",
        "补充需求描述；过短的条目 Agent 只能判“待评估”")
    chk("是否重要 的依据", (cov["customer_tier"] >= 0.8 or cov["customer"] >= 0.8) and cov["module"] >= 0.5,
        "客户或客户等级 ≥80%，模块 ≥50%", "补充客户等级（战略/KA/腰部/长尾）与模块；通用性靠“不同客户数”判断，需要客户名")
    chk("是否紧急 的依据", cov["feedback_type"] >= 0.6 or incident_like >= 1, "反馈类型 ≥60%（区分缺陷/新需求），或描述里有故障信息",
        "补充反馈类型；有合同/监管截止日的请写进描述")
    chk("处理建议 的依据", cov["content"] >= 0.95, "需求原文齐全 + 平台能力目录", "能力目录可先用 mock 的 data/capability_catalog.json，现场补充")
    chk("时间范围", cov["submitted_at"] >= 0.8, "提交时间 ≥80%（确认是“上个月”的数据）", "补充提交时间")
    chk("数量", len(items) >= 30, "≥30 条（黄金集 30 条 + 留出评估样本）", "数量不足时与 mock 数据合并使用")
    complete = [c for c in cands if c["complete"]]
    golden_ready = len(complete) >= 20
    ok_core = all(ok for name, ok, _, _ in checks[:4])
    verdict = "充足：可直接用于实验" if ok_core and len(items) >= 30 else (
        "基本可用：建议补充下列缺项，或与 mock 数据合并" if checks[0][1] and checks[3][1] else "不足：建议改用 mock 数据（data/feedback_last_month.jsonl），客户数据作参考")
    return {"n_raw": n_raw, "n": len(items), "coverage": cov, "content_len_median": statistics.median(lens) if lens else 0,
            "short": short, "dup_text": dup_text, "pii": dict(pii), "checks": checks, "verdict": verdict,
            "label_candidates": len(cands), "label_complete": len(complete), "golden_ready": golden_ready,
            "label_dist": {k: dict(Counter(c["expected"].get(k) or "-" for c in cands)) for k in ("important", "urgent", "decision")},
            "mapping": mapping}


def report(a, src) -> str:
    L = [f"# 客户数据充分性报告", "", f"- 来源文件：`{os.path.basename(src)}`（{a['n_raw']} 行 → 有效 {a['n']} 条）",
         f"- 生成时间：{datetime.now().isoformat(timespec='minutes')}", "", f"## 结论：{a['verdict']}", "",
         "| 检查项 | 结果 | 要求 | 不满足时怎么办 |", "|---|---|---|---|"]
    for name, ok, need, fix in a["checks"]:
        L.append(f"| {name} | {'✅' if ok else '⚠️'} | {need} | {'' if ok else fix} |")
    L += ["", "## 字段识别与覆盖率", "", "| 源列名 | 映射到 |", "|---|---|"]
    for k, v in a["mapping"].items():
        L.append(f"| {k} | {v} |")
    L += ["", "| 字段 | 覆盖率 | 级别 |", "|---|---|---|"]
    for f in C.INPUT_FIELDS:
        lvl = "必需" if f in C.REQUIRED_FIELDS else ("推荐" if f in C.RECOMMENDED_FIELDS else "可选")
        L.append(f"| {f} | {a['coverage'][f] * 100:.0f}% | {lvl} |")
    L += ["", "## 文本质量", f"- 需求描述长度中位数：{a['content_len_median']} 字；过短（<15 字）：{a['short']} 条；标题+描述完全重复：{a['dup_text']} 条",
          f"- 个人信息命中：{a['pii'] or '无'}" + ("（已在所有文本字段与历史拒绝理由/回复中打码；客户名、模块等短字段也已检查）" if a.get("masked") else "（未打码：使用了 --no-mask）"),
          "- 打码只覆盖常见格式（手机号、座机、邮箱、身份证号），人名、地址不会识别：请勿把输出提交到公开仓库", "", "## 能否直接做黄金集"]
    if a["label_candidates"]:
        L.append(f"- 客户数据带有历史判断 {a['label_candidates']} 条，其中三项齐全 {a['label_complete']} 条 → "
                 + ("**可以**作为黄金集候选（见 golden_candidates.jsonl），仍需按 data/golden/LABELING_GUIDE.md 复核并补五维/拒绝要点"
                    if a["golden_ready"] else "不足 20 条，建议与 mock 黄金集合并，或现场补标"))
        L.append(f"- 历史标签分布：{json.dumps(a['label_dist'], ensure_ascii=False)}")
    else:
        L.append("- 客户数据不带历史判断：黄金集需要现场标注（按 LABELING_GUIDE.md），或沿用 mock 黄金集")
    L += ["", "> 提醒：客户真实数据不要提交到公开仓库（默认输出在 data/customer/）。"]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--sheet")
    ap.add_argument("--mapping", help="JSON：{\"源列名\": \"目标字段\"}，目标字段见 data/schema/feedback_item.schema.json")
    ap.add_argument("--out-dir", default=os.path.join(C.DATA, "customer"))
    ap.add_argument("--no-mask", action="store_true")
    ap.add_argument("--use-as-input", action="store_true", help="把结果复制为 data/feedback_last_month.jsonl（先备份）")
    ap.add_argument("--strict", action="store_true", help="结论不是“充足”时退出码 1")
    a = ap.parse_args(argv)
    rows = C.read_any(a.src, a.sheet)
    if not rows:
        print("错误：文件里没有读到数据行")
        return 2
    explicit = {}
    if a.mapping:
        with open(a.mapping, encoding="utf-8-sig") as f:
            explicit = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    cols = list({k: None for r in rows for k in r})
    mapping = auto_map(cols, explicit)
    if not any(v in ("title", "content") for v in mapping.values()):
        print("错误：找不到“标题/需求描述”列。请用 --mapping 指定，例如 {\"反馈正文\": \"content\"}。识别到的列：" + ", ".join(cols))
        return 2
    items, cands, pii = normalize(rows, mapping, not a.no_mask)
    res = assess(items, mapping, cands, pii, len(rows))
    res["masked"] = not a.no_mask
    os.makedirs(a.out_dir, exist_ok=True)
    C.write_jsonl(os.path.join(a.out_dir, "feedback.normalized.jsonl"), items)
    C.write_csv(os.path.join(a.out_dir, "feedback.normalized.csv"), items, C.INPUT_FIELDS)
    if cands:
        C.write_jsonl(os.path.join(a.out_dir, "golden_candidates.jsonl"), cands)
    md = report(res, a.src)
    with open(os.path.join(a.out_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)
    print(f"输出目录：{a.out_dir}")
    if a.use_as_input:
        agent_dir = os.path.join(a.out_dir, "agent_data")
        os.makedirs(agent_dir, exist_ok=True)
        C.write_jsonl(os.path.join(agent_dir, "feedback_last_month.jsonl"), [{k: v for k, v in it.items() if k != "extra"} for it in items])
        shutil.copyfile(C.CATALOG_PATH, os.path.join(agent_dir, "capability_catalog.json"))
        print(f"已生成 Agent 数据目录：{agent_dir}（客户数据，不进 git）")
        print(f"让 Agent 读取它：export S12_DATA_DIR={os.path.abspath(agent_dir)}（仓库里的 mock 文件保持不变）")
    return 1 if (a.strict and not res["verdict"].startswith("充足")) else 0


if __name__ == "__main__":
    sys.exit(main())
