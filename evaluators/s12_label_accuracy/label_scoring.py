"""Algorithm: compare one triage output with its golden labels, and aggregate many comparisons.

Standard library only. Used by lambda_function.py (one trace) and batch_score.py (a whole run).

Scoring rules (see data/golden/LABELING_GUIDE.md):
  - important / urgent: exact match 是/否.
  - decision: correct if the predicted value is in the golden `acceptable_decisions`
    (ambiguous cases list more than one acceptable answer; default = [expected decision]).
  - level (only when scheme="level"): |rank(pred) - rank(gold)| <= tolerance (P0 < P1 < P2).
  - CriticalFail = red line (红线, one-vote veto): predicted 拒绝 for an item whose golden
    `can_auto_reject` is false (data loss / security / compliance / contract / defect). The item fails
    as a whole: every scored label counts as wrong (value 0), whichever labels are scored (also the
    single-label evaluators). The per-field `raw_correct` keeps what the agent actually got right.
Run-level verdict (red line / gate / observation): see pillars.py.
"""
from __future__ import annotations

from collections import Counter, OrderedDict
from typing import Any, Dict, Iterable, Optional

from golden_store import gold_from_golden_item, load_golden  # noqa: F401  (re-exported)

BINARY_FIELDS = ("important", "urgent", "decision")
LEVEL_FIELDS = ("level", "decision")
FIELD_CN = {"important": "是否重要", "urgent": "是否紧急", "decision": "处理建议", "level": "优先级"}
LEVEL_RANK = {"P0": 0, "P1": 1, "P2": 2}


def fields_for(scheme: str, score_field: Optional[str] = None) -> tuple:
    if score_field and score_field != "all":
        return (score_field,)
    return LEVEL_FIELDS if scheme == "level" else BINARY_FIELDS


def score_record(pred: Dict[str, Any], gold: Dict[str, Any], scheme: str = "binary",
                 level_tolerance: int = 0, score_field: Optional[str] = None) -> Dict[str, Any]:
    """Score one normalised prediction (span_adapter.normalize_output) against one gold record."""
    fields = fields_for(scheme, score_field)
    per: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    for f in fields:
        p = pred.get(f)
        g = gold.get(f)
        if f == "decision":
            acceptable = gold.get("acceptable_decisions") or ([g] if g else [])
            ok = p is not None and p in acceptable
            per[f] = {"expected": g, "acceptable": acceptable, "predicted": p, "correct": ok}
        elif f == "level":
            if p in LEVEL_RANK and g in LEVEL_RANK:
                ok = abs(LEVEL_RANK[p] - LEVEL_RANK[g]) <= int(level_tolerance)
            else:
                ok = False
            per[f] = {"expected": g, "predicted": p, "correct": ok}
        else:
            per[f] = {"expected": g, "predicted": p, "correct": p is not None and p == g}
    n_raw = sum(1 for v in per.values() if v["correct"])
    critical = pred.get("decision") == "拒绝" and not gold.get("can_auto_reject", True)
    for v in per.values():
        v["raw_correct"] = v["correct"]
        if critical:
            v["correct"] = False  # red line: the whole item fails
    n_ok = 0 if critical else n_raw
    value = round(n_ok / len(fields), 4) if fields else 0.0
    if critical:
        label = "CriticalFail"
    elif n_ok == len(fields):
        label = "Pass"
    else:
        label = "Fail"
    return {"feedback_id": gold.get("feedback_id"), "golden_id": gold.get("golden_id"), "fields": per,
            "n_correct": n_ok, "n_correct_raw": n_raw, "n_fields": len(fields), "value": value, "label": label,
            "critical": critical}


def _mark(ok: bool) -> str:
    return "对" if ok else "错"


def explain(scored: Dict[str, Any], pred: Dict[str, Any], extra: str = "") -> str:
    parts = []
    for f, v in scored["fields"].items():
        exp = "/".join(v.get("acceptable") or [str(v.get("expected"))]) if f == "decision" else str(v.get("expected"))
        parts.append(f"{FIELD_CN.get(f, f)} {_mark(v.get('raw_correct', v['correct']))}（期望 {exp}，实际 {v.get('predicted')}）")
    head = f"需求 {scored.get('feedback_id')}（{scored.get('golden_id') or '-'}）："
    if scored.get("critical"):
        head += (f"红线一票否决，整条记 0/{scored['n_fields']}"
                 f"（各项原本 {scored.get('n_correct_raw', 0)}/{scored['n_fields']} 项正确）。")
    else:
        head += f"{scored['n_correct']}/{scored['n_fields']} 项正确。"
    txt = head + "；".join(parts) + "。"
    if scored.get("critical"):
        txt += "严重错误（红线）：该需求属于不可自动拒绝（数据/安全/合规/合同/缺陷类），Agent 却判为拒绝，整条判错。"
    if pred.get("_issues"):
        txt += "输出问题：" + "；".join(pred["_issues"]) + "。"
    if extra:
        txt += extra
    return txt[:2000]


def aggregate(scored_list: Iterable[Dict[str, Any]], target: float = 0.70, scheme: str = "binary",
              missing: Iterable[str] = ()) -> Dict[str, Any]:
    """Per-field accuracy (red-line items count as wrong) / per-class recall and confusion (raw agent
    labels, for diagnosis) / label gates vs target (each field >= target)."""
    scored_list = list(scored_list)
    missing = list(missing)
    fields = fields_for(scheme)
    n = len(scored_list) + len(missing)
    acc: Dict[str, Any] = {}
    confusion: Dict[str, Dict[str, Counter]] = {f: {} for f in fields}
    for f in fields:
        ok = sum(1 for s in scored_list if s["fields"].get(f, {}).get("correct"))
        acc[f] = round(ok / n, 4) if n else 0.0
        for s in scored_list:
            v = s["fields"].get(f)
            if not v:
                continue
            g = str(v.get("expected"))
            confusion[f].setdefault(g, Counter())[str(v.get("predicted"))] += 1
        for fid in missing:
            confusion[f].setdefault("?", Counter())["(无输出)"] += 1
    recall: Dict[str, Dict[str, float]] = {}
    for f in fields:
        recall[f] = {}
        for g, row in confusion[f].items():
            if g == "?":
                continue
            tot = sum(row.values())
            hit = 0
            for s in scored_list:
                v = s["fields"].get(f)
                if v and str(v.get("expected")) == g and v.get("raw_correct", v.get("correct")):
                    hit += 1
            recall[f][g] = round(hit / tot, 4) if tot else 0.0
    overall = round(sum(acc.values()) / len(acc), 4) if acc else 0.0
    all_correct = sum(1 for s in scored_list if s["label"] == "Pass")
    critical = [s["feedback_id"] for s in scored_list if s["critical"]]
    label_gates = all(acc[f] >= target for f in fields) and n > 0
    return {
        "n_items": n, "n_scored": len(scored_list), "n_missing_output": len(missing), "missing_output": missing,
        "scheme": scheme, "target": target, "accuracy": acc, "overall_accuracy": overall,
        "all_three_correct_rate": round(all_correct / n, 4) if n else 0.0,
        "per_class_recall": recall,
        "confusion": {f: {g: dict(c) for g, c in rows.items()} for f, rows in confusion.items()},
        "critical_misrejects": critical, "label_gates_passed": label_gates,
        "passed": label_gates and not critical,  # batch_score.py replaces this with the three-pillar verdict
    }


def distribution(preds: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Scenario output view: decision counts + importance/urgency quadrants + auto-rejected list."""
    preds = list(preds)
    quad = Counter()
    for p in preds:
        quad[f"重要{p.get('important') or '?'}·紧急{p.get('urgent') or '?'}"] += 1
    return {
        "n": len(preds),
        "decision": dict(Counter(p.get("decision") or "?" for p in preds)),
        "quadrant": dict(quad),
        "level": dict(Counter(p.get("level") or "-" for p in preds)),
        "rejected": [{"feedback_id": p.get("feedback_id"), "reject_reason": p.get("reject_reason", ""),
                      "customer_reply": p.get("customer_reply", "")} for p in preds if p.get("decision") == "拒绝"],
    }
