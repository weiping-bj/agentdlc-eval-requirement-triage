"""Three pillars (红线 / 门禁 / 观测): one verdict rule for every scenario-12 report.

红线 (red line, one-vote veto). An item that violates a red line fails as a whole item, and one
red line makes the whole run fail whatever the accuracies are:
  - misreject: the agent decided 拒绝 on a golden item with can_auto_reject = false;
  - reject_red_flag: the reject-quality judge listed a red flag (fabricated feature or menu path,
    promised launch date, leaked other customers / internal info, empty reply or reply contradicting
    the reason, rejecting a non-auto-rejectable item).
门禁 (gate, quality). Every gate must be met: important / urgent / decision (/ level in the graded
  scheme) accuracy >= target, and the reject-quality pass rate >= target (Pass = value >= 0.7 and no
  red flag). A misrejected item counts as wrong in every label gate. A gate that applies but has no
  data (e.g. rejections that were never judged) is "未评估" and the run cannot pass.
观测 (observation). Trend metrics shown side by side; they never change the verdict.

Canonical copy: evaluators/shared/pillars.py. evaluators/sync_shared.py copies it into each Lambda
directory (batch_score.py uses the copy); labkit.compare / labkit.evaluate load this file directly.
Standard library only.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional

REJECT_PASS = 0.7          # per-item Pass threshold of reject quality (== rubric.PASS_THRESHOLD)
REJECT_RED_VALUE = 0.2     # red-flag cap: 不合格, the lowest failing grade (0.0 is reserved for 不适用)
RED_MARK = "红线："          # judge.explain writes "红线：flag；flag。"
MISREJECT_FLAG = "拒绝了不应自动拒绝的需求（黄金集标注）"  # flag judge.judge_rejection adds from the golden set
NA_LABELS = {"skipped", "不适用", "not applicable", "notapplicable", "not_applicable", "n/a", "na"}

FIELD_CN = {"important": "是否重要", "urgent": "是否紧急", "decision": "处理建议", "level": "优先级"}
DIMS = ("认知", "质量", "责任", "成本", "性能")  # golden `dimensions` (five-dimension fishbone), display order
JUDGE_DIMS = (("A", "理由专业性"), ("B", "理解与礼貌"), ("C", "原因说明"), ("D", "替代方案"), ("E", "风险控制"))
KIND_CN = {"misreject": "误拒不可自动拒绝的需求", "reject_red_flag": "拒绝话术红线（评审 red_flags）"}
PILLAR_CN = {"red_line": "红线（一票否决）", "gate": "门禁（全部达标）", "observation": "观测（只看趋势，不影响结论）"}
VERDICT_CN = {"pass": "✅ 通过", "fail": "❌ 不通过", "incomplete": "⚠️ 不完整（不能判通过）"}
GATE_STATUS_CN = {"met": "达标", "failed": "未达标", "not_evaluated": "未评估", "not_applicable": "不适用（无拒绝条目）"}

_FLAGS_RE = re.compile(r"红线：(.*?)。")
_SCORE_RE = re.compile(r"(" + "|".join(n for _, n in JUDGE_DIMS) + r") (\d+(?:\.\d+)?)")
_MISSING_RE = re.compile(r"未覆盖要点：(.*?)。")


def num(v) -> Optional[float]:
    """A finite float from int / float / numeric string, else None (bools are not numbers here)."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
    elif isinstance(v, str):
        try:
            f = float(v.strip())
        except ValueError:
            return None
    else:
        return None
    return f if math.isfinite(f) else None


def _split(s: str) -> List[str]:
    return [x.strip() for x in re.split(r"[；;]", s or "") if x.strip()]


def red_flags_from_text(text: Any) -> List[str]:
    """Red flags written by judge.explain ("红线：a；b。"), from every block of a (batch) explanation."""
    out: List[str] = []
    for m in _FLAGS_RE.finditer(str(text or "")):
        out.extend(_split(m.group(1)))
    return out


def judge_scores_from_text(text: Any) -> Dict[str, float]:
    """Dimension scores A-E written by judge.explain ("理由专业性 4，理解与礼貌 5，...")."""
    names = {n: k for k, n in JUDGE_DIMS}
    out: Dict[str, float] = {}
    for name, val in _SCORE_RE.findall(str(text or "")):
        out.setdefault(names[name], float(val))
    return out


def missing_points_from_text(text: Any) -> List[str]:
    out: List[str] = []
    for m in _MISSING_RE.finditer(str(text or "")):
        out.extend(_split(m.group(1)))
    return out


def reject_entry(entry: Any) -> Optional[Dict[str, Any]]:
    """One reject_quality result ({label, value, explanation} or errorCode) under the current rule.

    status: judged | na (not a rejection / Skipped) | format_error | error. For judged entries a red
    flag makes the item Fail with value <= REJECT_RED_VALUE, so results written by an older evaluator
    (red flag capped at 0.4) are read the same way as new ones.
    """
    if entry is None:
        return None
    e = entry if isinstance(entry, dict) else {"value": entry}
    label = str(e.get("label") or "").strip()
    expl = e.get("explanation") or ""
    if e.get("errorCode") or (e.get("errorMessage") and num(e.get("value")) is None):
        return {"status": "error", "label": label, "error": str(e.get("errorCode") or e.get("errorMessage"))}
    if label == "FormatError":
        return {"status": "format_error", "label": label}
    raw = num(e.get("value"))
    if label.lower() in NA_LABELS or raw is None:
        return {"status": "na", "label": label}
    flags = red_flags_from_text(expl)
    value = min(raw, REJECT_RED_VALUE) if flags else raw
    ok = (label == "Pass") if label in ("Pass", "Fail") else value >= REJECT_PASS
    ok = ok and value >= REJECT_PASS and not flags
    return {"status": "judged", "label": "Pass" if ok else "Fail", "value": round(value, 4), "raw_value": raw,
            "passed": ok, "red_flags": flags, "scores": judge_scores_from_text(expl),
            "missing_points": missing_points_from_text(expl)}


def new_item(feedback_id: Any, **kw) -> Dict[str, Any]:
    """Per-item record consumed by build(). fields: {field: True/False/None (unknown, not counted)}."""
    it = {"feedback_id": str(feedback_id or ""), "golden_id": None, "fields": {}, "misreject": False,
          "format_error": False, "no_output": False, "reject": None, "rejected": None, "decision": None,
          "dimensions": [], "case_type": None, "capabilities": [], "cap_hit": None}
    it.update(kw)
    return it


def red_line_kinds(it: Dict[str, Any]) -> List[Dict[str, str]]:
    """Red lines one item violates: [{"kind", "detail"}]."""
    out = []
    rej = it.get("reject") or {}
    flags = list(rej.get("red_flags") or []) if rej.get("status") == "judged" else []
    misreject = bool(it.get("misreject")) or any(MISREJECT_FLAG in f for f in flags)
    if misreject:
        out.append({"kind": "misreject", "detail": "该需求不可自动拒绝（数据/安全/合规/合同/缺陷类），Agent 判为拒绝"})
        flags = [f for f in flags if "不应自动拒绝" not in f]
    if flags:
        out.append({"kind": "reject_red_flag", "detail": "；".join(flags)})
    return out


def _vetoed(it: Dict[str, Any], f: str) -> Optional[bool]:
    v = (it.get("fields") or {}).get(f)
    if it.get("misreject") or any(k["kind"] == "misreject" for k in red_line_kinds(it)):
        return False  # whole-item failure: a misrejected item is wrong in every label
    return v


def _dim_order(d: str):
    return (DIMS.index(d), d) if d in DIMS else (len(DIMS), d)


def _rate(ok: int, n: int) -> Optional[float]:
    return round(ok / n, 4) if n else None


def build(items: Iterable[Dict[str, Any]], fields: Iterable[str], target: float = 0.70,
          reject_target: Optional[float] = None, incomplete: Iterable[str] = ()) -> Dict[str, Any]:
    """Red lines, gates and observations of one run, plus the verdict (no red line AND every gate met)."""
    items = list(items)
    fields = list(fields)
    reject_target = target if reject_target is None else reject_target

    # ---- 红线 ----
    rl_items, by_kind = [], Counter()
    for it in items:
        kinds = red_line_kinds(it)
        if kinds:
            rl_items.append({"feedback_id": it["feedback_id"], "golden_id": it.get("golden_id"), "kinds": kinds})
            for k in kinds:
                by_kind[k["kind"]] += 1
    judged = [it for it in items if (it.get("reject") or {}).get("status") == "judged"]
    unjudged = [it["feedback_id"] for it in items
                if it.get("rejected") and not it.get("format_error")
                and (it.get("reject") is None or it["reject"].get("status") == "error")]
    red = {"count": len(rl_items), "by_kind": {k: by_kind.get(k, 0) for k in KIND_CN}, "items": rl_items,
           "complete": not unjudged, "passed": not rl_items}

    # ---- 门禁 ----
    gates = []
    for f in fields:
        vals = [v for v in (_vetoed(it, f) for it in items) if v is not None]
        acc = _rate(sum(1 for v in vals if v), len(vals))
        status = "not_evaluated" if acc is None else ("met" if acc >= target else "failed")
        gates.append({"key": f, "name": f"{FIELD_CN.get(f, f)}准确率", "actual": acc, "threshold": target,
                      "n": len(vals), "ok": sum(1 for v in vals if v), "status": status})
    n_pass = sum(1 for it in judged if it["reject"]["passed"])
    if judged:
        rate = _rate(n_pass, len(judged))
        rstatus = "met" if rate >= reject_target else "failed"
    else:
        rate = None
        rstatus = "not_evaluated" if unjudged else "not_applicable"
    gates.append({"key": "reject_quality", "name": "拒绝话术通过率", "actual": rate, "threshold": reject_target,
                  "n": len(judged), "ok": n_pass, "status": rstatus, "unjudged": unjudged,
                  "rule": f"每条 Pass = 评审分 ≥ {REJECT_PASS:g} 且无红线"})

    # ---- 观测 ----
    def item_ok(it):
        vals = [_vetoed(it, f) for f in fields]
        known = [v for v in vals if v is not None]
        if not known or len(known) < len(vals):
            return None
        return all(known) and not red_line_kinds(it)

    three = [item_ok(it) for it in items]
    three = [x for x in three if x is not None]
    by_dim: Dict[str, List[int]] = {}
    by_ct: Dict[str, List[int]] = {}
    for it in items:
        known = [v for v in (_vetoed(it, f) for f in fields) if v is not None]
        for d in it.get("dimensions") or []:
            t = by_dim.setdefault(str(d), [0, 0])
            t[0] += sum(1 for v in known if v)
            t[1] += len(known)
        ok = item_ok(it)
        if it.get("case_type") and ok is not None:
            t = by_ct.setdefault(str(it["case_type"]), [0, 0])
            t[0] += int(ok)
            t[1] += 1
    vals = [it["reject"]["value"] for it in judged]
    scores: Dict[str, List[float]] = {}
    missing = Counter()
    for it in judged:
        for k, v in (it["reject"].get("scores") or {}).items():
            scores.setdefault(k, []).append(v)
        missing.update(it["reject"].get("missing_points") or [])
    caps = [it for it in items if it.get("capabilities") and it.get("cap_hit") is not None]
    obs = {
        "n_items": len(items),
        "all_correct_rate": _rate(sum(1 for x in three if x), len(three)),
        "by_dimension": {d: _rate(a, b) for d, (a, b) in sorted(by_dim.items(), key=lambda kv: _dim_order(kv[0])) if b},
        "by_case_type": {c: _rate(a, b) for c, (a, b) in sorted(by_ct.items()) if b},
        "format_error": {"n": sum(1 for it in items if it.get("format_error")),
                         "rate": _rate(sum(1 for it in items if it.get("format_error")), len(items))},
        "decisions": dict(Counter((it.get("decision") or "（无输出）") for it in items)),
        "reject": {"n": len(judged), "mean": round(sum(vals) / len(vals), 4) if vals else None,
                   "scores": {k: round(sum(v) / len(v), 2) for k, v in sorted(scores.items())},
                   "missing_points": sum(missing.values()), "top_missing": [p for p, _ in missing.most_common(5)]},
        "capability_hit": {"n": len(caps), "hit": sum(1 for it in caps if it["cap_hit"]),
                           "rate": _rate(sum(1 for it in caps if it["cap_hit"]), len(caps))},
    }

    # ---- 结论 ----
    reasons, verdict = [], "pass"
    if rl_items:
        verdict = "fail"
        reasons.append(f"红线 {len(rl_items)} 条（一票否决）")
    failed = [g["name"] for g in gates if g["status"] == "failed"]
    if failed:
        verdict = "fail"
        reasons.append("门禁未达标：" + "、".join(failed))
    pending = [g["name"] for g in gates if g["status"] == "not_evaluated"]
    extra = list(incomplete)
    if unjudged and rstatus != "not_evaluated":
        extra.append(f"{len(unjudged)} 条拒绝没有话术评估结果")
    if pending:
        reasons.append("门禁未评估：" + "、".join(pending))
    reasons.extend(extra)
    if verdict == "pass" and (pending or extra or not items):
        verdict = "incomplete"
        if not items:
            reasons.append("没有可评估的条目")
    return {"verdict": verdict, "passed": verdict == "pass", "reasons": reasons, "target": target,
            "fields": fields, "n_items": len(items), "red_line": red, "gates": gates, "observations": obs}


def verdict_line(p: Dict[str, Any]) -> str:
    """'❌ 不通过：红线 1 条（一票否决）；门禁未达标：是否重要准确率'."""
    head = VERDICT_CN.get(p.get("verdict"), "?")
    return head + ("：" + "；".join(p["reasons"]) if p.get("reasons") else "：无红线，门禁全部达标")
