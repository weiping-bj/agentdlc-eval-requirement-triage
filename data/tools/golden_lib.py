"""Golden-set model: build from seed pool, validate, stats, export (CSV / AgentCore dataset), apply feedback CSV."""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

import common as C

GOLDEN_CSV_FIELDS = ["golden_id", "feedback_id", "case_type", "dimensions", "important", "urgent", "decision",
                     "acceptable_decisions", "level", "can_auto_reject", "title", "content", "customer", "customer_tier",
                     "module", "feedback_type", "reject_points", "reply_points", "rationale", "version"]

# Fill-in template columns (Chinese headers, edited by customers in Excel)
FEEDBACK_CSV_FIELDS = ["golden_id", "feedback_id", "操作", "是否重要", "是否紧急", "处理建议", "可接受建议", "优先级",
                       "可自动拒绝", "五维", "样例类型", "拒绝要点", "话术要点", "标注理由", "标题", "需求描述", "客户",
                       "客户等级", "相关模块", "反馈类型", "合同承诺", "备注"]
OPS = {"保留": "keep", "keep": "keep", "": "keep", "修改": "modify", "modify": "modify", "删除": "delete",
       "delete": "delete", "新增": "add", "add": "add"}


def input_of(item: Dict[str, Any]) -> Dict[str, Any]:
    return {k: item.get(k) for k in C.INPUT_FIELDS if item.get(k) not in (None, "")}


def golden_from_pool(rec: Dict[str, Any], version: str = "v1") -> Dict[str, Any]:
    m = rec["_meta"]
    return {
        "golden_id": m["golden_id"], "feedback_id": rec["id"], "input": input_of(rec),
        "expected": {"important": m["important"], "urgent": m["urgent"], "decision": m["decision"], "level": m.get("level")},
        "acceptable_decisions": m.get("acceptable_decisions") or [m["decision"]],
        "can_auto_reject": bool(m.get("can_auto_reject", True)),
        "reject_points": m.get("reject_points") or [], "reply_points": m.get("reply_points") or [],
        "rationale": m.get("rationale", ""), "dimensions": m.get("dimensions") or [], "case_type": m.get("case_type"),
        "matched_capabilities": m.get("matched_capabilities") or [], "version": version, "source": "synthetic",
    }


# ---------------------------------------------------------------- validation
def validate(rows: List[Dict[str, Any]], scheme: str = "binary", catalog_ids: Optional[set] = None) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warns: List[str] = []
    seen_g, seen_f = set(), set()
    for i, r in enumerate(rows, 1):
        tag = f"第 {i} 条（{r.get('golden_id') or '?'}）"
        gid, fid = r.get("golden_id"), r.get("feedback_id")
        if not gid:
            errors.append(f"{tag}: 缺少 golden_id")
        elif gid in seen_g:
            errors.append(f"{tag}: golden_id 重复")
        seen_g.add(gid)
        if not fid:
            errors.append(f"{tag}: 缺少 feedback_id")
        elif fid in seen_f:
            errors.append(f"{tag}: feedback_id 重复（同一条需求只能标一次）")
        seen_f.add(fid)
        inp = r.get("input") or {}
        if (inp.get("id") or fid) != fid:
            errors.append(f"{tag}: input.id 与 feedback_id 不一致")
        if not (inp.get("title") or inp.get("content")):
            errors.append(f"{tag}: input 缺少标题和需求描述（黄金集必须自包含需求原文）")
        elif len(str(inp.get("content") or "")) < 4:
            warns.append(f"{tag}: 需求描述过短")
        exp = r.get("expected") or {}
        for f in ("important", "urgent"):
            if exp.get(f) not in C.YES_NO:
                errors.append(f"{tag}: expected.{f} 必须是 是/否，当前 {exp.get(f)!r}")
        dec = exp.get("decision")
        if dec not in C.DECISIONS:
            errors.append(f"{tag}: expected.decision 必须是 迭代/拒绝/待评估，当前 {dec!r}")
        acc = r.get("acceptable_decisions") or []
        if acc and dec not in acc:
            errors.append(f"{tag}: acceptable_decisions 必须包含 expected.decision")
        if any(a not in C.DECISIONS for a in acc):
            errors.append(f"{tag}: acceptable_decisions 含非法值 {acc}")
        lv = exp.get("level")
        if lv not in (None, "") and lv not in C.LEVELS:
            errors.append(f"{tag}: expected.level 必须是 P0/P1/P2 或空，当前 {lv!r}")
        if scheme == "level" and lv not in C.LEVELS:
            errors.append(f"{tag}: 分级模式下每条都必须有 expected.level（可用 golden_tool.py fill-levels 自动补）")
        if not r.get("can_auto_reject", True) and "拒绝" in (acc or [dec]):
            errors.append(f"{tag}: 标为“不可自动拒绝”，但期望/可接受建议里有“拒绝”")
        if dec == "拒绝":
            if not r.get("reject_points"):
                errors.append(f"{tag}: 期望拒绝，但没有 reject_points（拒绝理由要点）")
            if not r.get("reply_points"):
                warns.append(f"{tag}: 期望拒绝，但没有 reply_points（话术要点），LLM 评审将缺少参考")
        dims = r.get("dimensions") or []
        if not dims:
            warns.append(f"{tag}: 没有标五维（认知/质量/责任/成本/性能）")
        bad = [d for d in dims if d not in C.DIMENSIONS]
        if bad:
            errors.append(f"{tag}: 五维取值非法 {bad}")
        if r.get("case_type") not in C.CASE_TYPES:
            warns.append(f"{tag}: case_type 应为 {'/'.join(C.CASE_TYPES)}，当前 {r.get('case_type')!r}")
        if catalog_ids is not None:
            miss = [c for c in r.get("matched_capabilities") or [] if c not in catalog_ids]
            if miss:
                warns.append(f"{tag}: 引用了能力目录里不存在的能力 {miss}")
    st = stats(rows)
    n = len(rows)
    if n < 20:
        warns.append(f"黄金集只有 {n} 条，建议至少 20~30 条，70% 准确率的统计意义才够")
    for d in C.DECISIONS:
        if st["decision"].get(d, 0) < 3:
            warns.append(f"处理建议“{d}”只有 {st['decision'].get(d, 0)} 条，建议每类至少 3 条")
    for f in ("important", "urgent"):
        for v in C.YES_NO:
            if st[f].get(v, 0) < 3:
                warns.append(f"{f}={v} 只有 {st[f].get(v, 0)} 条，建议至少 3 条")
    if n and st["urgent"].get("否", 0) / n > 0.8:
        warns.append("“紧急=否”占比超过 80%：全判“否”也能拿高分，请同时看“紧急=是”的召回率")
    for d in C.DIMENSIONS:
        if st["dimensions"].get(d, 0) < 2:
            warns.append(f"五维“{d}”只覆盖 {st['dimensions'].get(d, 0)} 条，建议至少 2 条")
    return errors, warns


def stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    c = {k: Counter() for k in ("important", "urgent", "decision", "level", "case_type", "dimensions")}
    for r in rows:
        e = r.get("expected") or {}
        for k in ("important", "urgent", "decision", "level"):
            c[k][e.get(k) or "-"] += 1
        c["case_type"][r.get("case_type") or "-"] += 1
        for d in r.get("dimensions") or []:
            c["dimensions"][d] += 1
    out = {k: dict(v) for k, v in c.items()}
    out["n"] = len(rows)
    out["not_auto_rejectable"] = sum(1 for r in rows if not r.get("can_auto_reject", True))
    out["ambiguous_multi_answer"] = sum(1 for r in rows if len(r.get("acceptable_decisions") or []) > 1)
    return out


def stats_markdown(rows: List[Dict[str, Any]]) -> str:
    s = stats(rows)

    def fmt(d):
        return "，".join(f"{k} {v}" for k, v in sorted(d.items()))
    return "\n".join([
        f"黄金集共 {s['n']} 条",
        f"- 是否重要：{fmt(s['important'])}",
        f"- 是否紧急：{fmt(s['urgent'])}",
        f"- 处理建议：{fmt(s['decision'])}（多答案可接受 {s['ambiguous_multi_answer']} 条）",
        f"- 优先级：{fmt(s['level'])}",
        f"- 样例类型：{fmt(s['case_type'])}",
        f"- 五维覆盖：{fmt(s['dimensions'])}",
        f"- 不可自动拒绝：{s['not_auto_rejectable']} 条",
    ])


# ---------------------------------------------------------------- exports
def to_csv_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        inp, e = r.get("input") or {}, r.get("expected") or {}
        out.append({
            "golden_id": r.get("golden_id"), "feedback_id": r.get("feedback_id"), "case_type": r.get("case_type"),
            "dimensions": "/".join(r.get("dimensions") or []), "important": e.get("important"), "urgent": e.get("urgent"),
            "decision": e.get("decision"), "acceptable_decisions": "/".join(r.get("acceptable_decisions") or []),
            "level": e.get("level"), "can_auto_reject": "是" if r.get("can_auto_reject", True) else "否",
            "title": inp.get("title"), "content": inp.get("content"), "customer": inp.get("customer"),
            "customer_tier": inp.get("customer_tier"), "module": inp.get("module"), "feedback_type": inp.get("feedback_type"),
            "reject_points": "；".join(r.get("reject_points") or []), "reply_points": "；".join(r.get("reply_points") or []),
            "rationale": r.get("rationale"), "version": r.get("version")})
    return out


def to_feedback_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pre-filled fill-in template: one row per golden item, 操作=保留."""
    out = []
    for r in rows:
        inp, e = r.get("input") or {}, r.get("expected") or {}
        out.append({"golden_id": r.get("golden_id"), "feedback_id": r.get("feedback_id"), "操作": "保留",
                    "是否重要": e.get("important"), "是否紧急": e.get("urgent"), "处理建议": e.get("decision"),
                    "可接受建议": "/".join(r.get("acceptable_decisions") or []), "优先级": e.get("level") or "",
                    "可自动拒绝": "是" if r.get("can_auto_reject", True) else "否",
                    "五维": "/".join(r.get("dimensions") or []), "样例类型": r.get("case_type") or "",
                    "拒绝要点": "；".join(r.get("reject_points") or []), "话术要点": "；".join(r.get("reply_points") or []),
                    "标注理由": r.get("rationale") or "", "标题": inp.get("title") or "", "需求描述": inp.get("content") or "",
                    "客户": inp.get("customer") or "", "客户等级": inp.get("customer_tier") or "", "相关模块": inp.get("module") or "",
                    "反馈类型": inp.get("feedback_type") or "", "合同承诺": inp.get("contract_commitment") or "", "备注": ""})
    return out


def render_user_message(inp: Dict[str, Any]) -> str:
    """Same rendering as agent/s12agent/core.py format_input (line 1 = 需求编号)."""
    labels = [("title", "标题"), ("content", "需求描述"), ("customer", "客户"), ("industry", "行业"),
              ("customer_tier", "客户等级"), ("channel", "来源渠道"), ("module", "相关模块"), ("feedback_type", "反馈类型"),
              ("submitted_at", "提交时间"), ("contract_commitment", "合同承诺")]
    lines = [f"需求编号: {inp.get('id', '')}"]
    for k, lab in labels:
        if inp.get(k) not in (None, ""):
            lines.append(f"{lab}: {inp[k]}")
    return "\n".join(lines)


def to_agentcore_dataset(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Projection to the AgentCore dataset shape. expected_response = JSON of expected labels
    (the s12_label_accuracy evaluator parses it when passed as evaluationReferenceInputs)."""
    scen = []
    for r in rows:
        e = r.get("expected") or {}
        exp = {"feedback_id": r["feedback_id"], "important": e.get("important"), "urgent": e.get("urgent"),
               "decision": e.get("decision"), "level": e.get("level"),
               "acceptable_decisions": r.get("acceptable_decisions") or [e.get("decision")],
               "can_auto_reject": r.get("can_auto_reject", True), "golden_id": r.get("golden_id")}
        assertions = [f"是否重要应为“{e.get('important')}”，是否紧急应为“{e.get('urgent')}”，"
                      f"处理建议应为“{'或'.join(exp['acceptable_decisions'])}”"]
        if e.get("decision") == "拒绝":
            assertions += [f"拒绝理由应包含：{p}" for p in r.get("reject_points") or []]
            assertions += [f"客户话术应包含：{p}" for p in r.get("reply_points") or []]
        if not r.get("can_auto_reject", True):
            assertions.append("该需求不可被自动拒绝")
        scen.append({
            "scenario_id": r["golden_id"], "scenario_description": (r.get("input") or {}).get("title", ""),
            "turns": [{"input": render_user_message(r.get("input") or {}),
                       "expected_response": json.dumps(exp, ensure_ascii=False)}],
            "assertions": assertions,
            "metadata": {"feedback_id": r["feedback_id"], "case_type": r.get("case_type"),
                         "dimensions": r.get("dimensions") or [], "version": r.get("version"), "source": "synthetic"}})
    return {"name": "s12-golden", "description": "场景 12 需求价值打分黄金集（合成数据）", "scenarios": scen}


# ---------------------------------------------------------------- apply feedback CSV
def _apply_fields(row: Dict[str, str], g: Dict[str, Any]) -> None:
    e = g.setdefault("expected", {})
    if row.get("是否重要"):
        e["important"] = C.norm_yes_no(row["是否重要"]) or row["是否重要"]
    if row.get("是否紧急"):
        e["urgent"] = C.norm_yes_no(row["是否紧急"]) or row["是否紧急"]
    if row.get("处理建议"):
        e["decision"] = C.norm_decision(row["处理建议"]) or row["处理建议"]
    if "优先级" in row and row.get("优先级") is not None:
        lv = row.get("优先级", "").strip()
        e["level"] = C.norm_level(lv) if lv else e.get("level")
    acc = [C.norm_decision(a) or a for a in C.split_list(row.get("可接受建议"), C.TAG_SEPS)]
    dec = e.get("decision")
    current = g.get("acceptable_decisions") or []
    if acc and dec in acc:
        g["acceptable_decisions"] = acc
    elif dec and (acc or dec not in current):
        # stale / missing 可接受建议 (e.g. pre-filled list from the old decision) -> only the new decision
        g["acceptable_decisions"] = [dec]
    if row.get("可自动拒绝"):
        g["can_auto_reject"] = C.norm_yes_no(row["可自动拒绝"]) != "否"
    if row.get("五维"):
        g["dimensions"] = C.split_list(row["五维"], C.TAG_SEPS)
    if row.get("样例类型"):
        g["case_type"] = row["样例类型"].strip()
    if row.get("拒绝要点"):
        g["reject_points"] = C.split_list(row["拒绝要点"])
    if row.get("话术要点"):
        g["reply_points"] = C.split_list(row["话术要点"])
    if row.get("标注理由"):
        g["rationale"] = row["标注理由"].strip()
    inp = g.setdefault("input", {})
    for col, key in (("标题", "title"), ("需求描述", "content"), ("客户", "customer"), ("客户等级", "customer_tier"),
                     ("相关模块", "module"), ("反馈类型", "feedback_type"), ("合同承诺", "contract_commitment")):
        if row.get(col):
            inp[key] = row[col].strip()


def apply_feedback(rows: List[Dict[str, Any]], fb_rows: List[Dict[str, str]], version: str,
                   feedback_pool: Optional[Dict[str, Dict[str, Any]]] = None) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Apply the golden-set-feedback.csv operations. Returns (new_rows, log lines)."""
    by_gid = {r["golden_id"]: r for r in rows}
    by_fid = {r["feedback_id"]: r for r in rows}
    log: List[str] = []
    deleted = set()
    new_rows = [json.loads(json.dumps(r, ensure_ascii=False)) for r in rows]
    idx = {r["golden_id"]: r for r in new_rows}
    used_g = set(by_gid)
    for n, row in enumerate(fb_rows, 2):
        op = OPS.get((row.get("操作") or "").strip().lower(), OPS.get((row.get("操作") or "").strip()))
        if op is None:
            log.append(f"CSV 第 {n} 行：未知操作 {row.get('操作')!r}，已跳过（可选：保留/修改/删除/新增）")
            continue
        gid = (row.get("golden_id") or "").strip()
        fid = (row.get("feedback_id") or "").strip()
        target = idx.get(gid) or (idx.get(by_fid[fid]["golden_id"]) if fid in by_fid else None)
        if op == "keep":
            continue
        if op == "delete":
            if not target:
                log.append(f"CSV 第 {n} 行：要删除的 {gid or fid} 不存在")
                continue
            deleted.add(target["golden_id"])
            log.append(f"删除 {target['golden_id']}")
            continue
        if op == "modify":
            if not target:
                log.append(f"CSV 第 {n} 行：要修改的 {gid or fid} 不存在，如需新增请把操作写成“新增”")
                continue
            _apply_fields(row, target)
            target["version"] = version
            log.append(f"修改 {target['golden_id']}")
            continue
        # add
        if not gid or gid in used_g:
            k = len(used_g) + 1
            while f"G{k:02d}" in used_g:
                k += 1
            gid = f"G{k:02d}"
        used_g.add(gid)
        base: Dict[str, Any] = {"golden_id": gid, "feedback_id": fid, "input": {}, "expected": {},
                                "acceptable_decisions": [], "can_auto_reject": True, "reject_points": [],
                                "reply_points": [], "rationale": "", "dimensions": [], "case_type": "模糊",
                                "matched_capabilities": [], "version": version, "source": "workshop"}
        if fid and feedback_pool and fid in feedback_pool:
            base["input"] = input_of(feedback_pool[fid])
        if not fid:
            k = 901
            existing = {r["feedback_id"] for r in new_rows}
            while f"FB-NEW-{k:03d}" in existing:
                k += 1
            fid = f"FB-NEW-{k:03d}"
            base["feedback_id"] = fid
        base["input"]["id"] = fid
        _apply_fields(row, base)
        if base["expected"].get("decision") and not base["acceptable_decisions"]:
            base["acceptable_decisions"] = [base["expected"]["decision"]]
        new_rows.append(base)
        idx[gid] = base
        log.append(f"新增 {gid}（{fid}）")
    out = [r for r in new_rows if r["golden_id"] not in deleted]
    return out, log
