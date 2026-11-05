"""AgentCore Evaluations code-based evaluator (TRACE level): scenario-12 three-label accuracy.

Plain Lambda handler, standard library only (no bedrock-agentcore SDK needed).
Handler: lambda_function.handler. Package = this whole directory (span_adapter.py,
label_scoring.py, golden_set.json must sit next to this file).

Event (schema 1.0): {evaluationLevel, evaluationInput.sessionSpans, evaluationTarget.traceIds,
                     evaluationReferenceInputs, evaluatorName, ...}
Return: {"label", "value", "explanation"} or {"errorCode", "errorMessage"}.

value  = fraction of scored labels that are correct (3 labels by default -> 0 / 0.33 / 0.67 / 1).
label  = Pass (all correct) | Fail | CriticalFail (rejected a must-not-reject item)
         | FormatError (assistant answered but no parsable triage JSON; value 0)
         | Skipped (no answer in trace, or no ground truth for this feedback id; value omitted).

Which labels are scored (first match wins):
  env S12_SCORE_FIELD = all | important | urgent | decision | level
  evaluatorName / evaluatorId / evaluatorArn (id tail "-XXXXXXXXXX" stripped) ending in
  _important | _urgent | _decision | _level  -> that single label (value 0/1)
  otherwise all (binary scheme: important+urgent+decision; level scheme: level+decision)
Other env: S12_LABEL_SCHEME=binary|level (default binary), S12_LEVEL_TOLERANCE=0|1 (default 0),
           S12_GOLDEN_PATH (default golden_set.json next to this file).
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import label_scoring as ls  # noqa: E402
import span_adapter as sa  # noqa: E402

_GOLDEN = None
SUFFIX_FIELDS = (("_important", "important"), ("_urgent", "urgent"), ("_decision", "decision"), ("_level", "level"))


def _golden():
    global _GOLDEN
    if _GOLDEN is None:
        path = os.environ.get("S12_GOLDEN_PATH") or os.path.join(HERE, "golden_set.json")
        _GOLDEN = ls.load_golden(path) if os.path.exists(path) else {}
    return _GOLDEN


_ID_TAIL = re.compile(r"-[A-Za-z0-9]{10}$")
_FIELD_TOKEN = re.compile(r"(?:^|_)(important|urgent|decision|level)(?:_|$)")


def _name_candidates(event_or_name) -> list:
    """evaluatorName / evaluatorId / evaluatorArn, normalised: last ARN segment, '-XXXXXXXXXX' id tail stripped."""
    if isinstance(event_or_name, dict):
        raw = [event_or_name.get(k) for k in ("evaluatorName", "evaluatorId", "evaluatorArn", "evaluator_name", "evaluator_id")]
    else:
        raw = [event_or_name]
    out = []
    for v in raw:
        if not v:
            continue
        v = str(v).strip().split("/")[-1].split(":")[-1].lower()
        out.append(_ID_TAIL.sub("", v))
    return out


def resolve_score_field(event_or_name) -> str:
    """S12_SCORE_FIELD env > evaluator name/id/arn suffix (_important/_urgent/_decision/_level) > token match > all."""
    env = (os.environ.get("S12_SCORE_FIELD") or "").strip().lower()
    if env:
        return env
    names = _name_candidates(event_or_name)
    for name in names:
        for suf, field in SUFFIX_FIELDS:
            if name.endswith(suf):
                return field
    for name in names:
        m = _FIELD_TOKEN.search(name)
        if m and not name.endswith("_accuracy"):
            return m.group(1)
    return "all"


def gold_from_reference_inputs(refs, feedback_id: str | None):
    """Ground truth passed by Evaluations (expectedResponse.text = JSON of expected labels)."""
    cands = []
    for r in refs or []:
        if not isinstance(r, dict):
            continue
        er = r.get("expectedResponse") or r.get("expected_response") or {}
        text = er.get("text") if isinstance(er, dict) else (er if isinstance(er, str) else None)
        if not text:
            continue
        for v in sa.json_values_in_text(text):
            if not isinstance(v, dict):
                continue
            objs = sa.triage_objects(v)
            src = objs[0] if objs else None
            if src is None:
                continue
            norm = sa.normalize_output(src)
            acc = src.get("acceptable_decisions") or ([norm["decision"]] if norm["decision"] else [])
            cands.append({
                "feedback_id": norm.get("feedback_id") or feedback_id, "golden_id": src.get("golden_id"),
                "important": norm["important"], "urgent": norm["urgent"], "decision": norm["decision"],
                "level": norm["level"], "acceptable_decisions": acc,
                "can_auto_reject": bool(src.get("can_auto_reject", True)),
            })
    if not cands:
        return None
    for c in cands:
        if feedback_id and c.get("feedback_id") == feedback_id:
            return c
    return cands[0]


def evaluate(event: dict) -> dict:
    level = (event.get("evaluationLevel") or "TRACE").upper()
    spans = ((event.get("evaluationInput") or {}).get("sessionSpans")) or []
    target = event.get("evaluationTarget") or {}
    trace_ids = target.get("traceIds") or []
    trace_id = trace_ids[0] if (trace_ids and level != "SESSION") else None
    scheme = (os.environ.get("S12_LABEL_SCHEME") or "binary").strip().lower()
    tol = int(os.environ.get("S12_LEVEL_TOLERANCE") or "0")
    field = resolve_score_field(event)
    print(json.dumps({"s12": "label_eval", "keys": sorted(event.keys()), "evaluatorName": event.get("evaluatorName"),
                      "evaluatorId": event.get("evaluatorId"), "level": level, "n_spans": len(spans),
                      "trace": trace_id, "field": field, "scheme": scheme}, ensure_ascii=False))
    if field not in ("all",) + ls.BINARY_FIELDS + ("level",):
        return {"errorCode": "VALIDATION_FAILED", "errorMessage": f"unknown S12_SCORE_FIELD {field!r}"}

    ex = sa.extract_records(spans, trace_id)
    note = "" if ex["trace_matched"] else "（未找到目标 trace 的 span，已改用整个 session）"
    if not ex["records"]:
        if ex["has_assistant_text"]:
            return {"label": "FormatError", "value": 0.0,
                    "explanation": f"trace 中有 Agent 回复，但解析不出约定的 JSON（feedback_id/important/urgent/decision）。{note}"}
        return {"label": "Skipped",
                "explanation": f"trace 中没有 Agent 的分诊输出（span 数 {ex['span_count']}），跳过。{note}"}

    refs = event.get("evaluationReferenceInputs") or []
    results, missing_gold = [], []
    for rec in ex["records"]:
        fid = rec["feedback_id"]
        gold = gold_from_reference_inputs(refs, fid) or _golden().get(fid)
        if not gold:
            missing_gold.append(fid)
            continue
        results.append((ls.score_record(rec["output"], gold, scheme, tol, None if field == "all" else field), rec))
    if not results:
        return {"label": "Skipped",
                "explanation": f"黄金集里没有需求 {', '.join(missing_gold)} 的标注，无法打分（不是黄金集样本）。{note}"}

    src = ex["records"][0]["source"]
    if len(results) == 1:
        scored, rec = results[0]
        extra = f"[来源 {src}，版本 {ex.get('agent_version') or '-'}]{note}"
        return {"label": scored["label"], "value": scored["value"], "explanation": ls.explain(scored, rec["output"], extra)}
    value = round(sum(s["value"] for s, _ in results) / len(results), 4)
    crit = [s for s, _ in results if s["critical"]]
    label = "CriticalFail" if crit else ("Pass" if all(s["label"] == "Pass" for s, _ in results) else "Fail")
    lines = [f"批量输出 {len(results)} 条，平均 {value}。"] + [ls.explain(s, r["output"])[:300] for s, r in results[:6]]
    if missing_gold:
        lines.append(f"无黄金集标注：{', '.join(missing_gold[:10])}")
    return {"label": label, "value": value, "explanation": "\n".join(lines)[:2000]}


def handler(event, context=None):
    try:
        if isinstance(event, str):
            event = json.loads(event)
        return evaluate(event)
    except Exception as e:  # never let the evaluation job see an unhandled exception
        print("[s12_label_accuracy] EXCEPTION", repr(e))
        return {"errorCode": "S12_LABEL_EVAL_FAILED", "errorMessage": str(e)[:500]}
