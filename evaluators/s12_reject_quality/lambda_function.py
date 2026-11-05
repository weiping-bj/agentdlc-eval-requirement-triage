"""AgentCore Evaluations code-based evaluator (TRACE level): rejection reason + customer reply quality.

Plain Lambda handler; standard library + boto3 (bundled in the Lambda Python runtime).
Handler: lambda_function.handler. Package = this whole directory (span_adapter.py, golden_store.py,
rubric.py, judge.py, golden_set.json, capability_catalog.json next to this file).

Only traces whose triage output has decision == 拒绝 are judged.
Return:
  {"label": "Pass"|"Fail", "value": 0..1, "explanation"}     rejection judged by the LLM
  {"label": "不适用", "explanation"}                           decision is 迭代 / 待评估 (no value)
  {"label": "FormatError", "value": 0.0, ...}                 answer present but no triage JSON
  {"label": "Skipped", ...}                                   no triage output in the trace
  {"errorCode", "errorMessage"}                               judge model call failed
Env: JUDGE_MODEL_ID, FALLBACK_MODEL_ID, JUDGE_REGION/AWS_REGION, S12_GOLDEN_PATH, S12_CATALOG_PATH.
The Lambda role needs bedrock:InvokeModel / bedrock:Converse on the judge model(s).
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import golden_store  # noqa: E402
import judge  # noqa: E402
import span_adapter as sa  # noqa: E402

_GOLDEN = None
_CATALOG = None
_CLIENT = None


def _golden():
    global _GOLDEN
    if _GOLDEN is None:
        p = os.environ.get("S12_GOLDEN_PATH") or os.path.join(HERE, "golden_set.json")
        _GOLDEN = golden_store.load_golden(p) if os.path.exists(p) else {}
    return _GOLDEN


def _catalog():
    global _CATALOG
    if _CATALOG is None:
        p = os.environ.get("S12_CATALOG_PATH") or os.path.join(HERE, "capability_catalog.json")
        try:
            with open(p, encoding="utf-8") as f:
                doc = json.load(f)
            _CATALOG = doc.get("capabilities", []) if isinstance(doc, dict) else list(doc)
        except (OSError, ValueError):
            _CATALOG = []
    return _CATALOG


def _bigrams(s: str) -> set:
    s = "".join(ch for ch in (s or "") if not ch.isspace())
    return {s[i:i + 2] for i in range(len(s) - 1)}


def capability_hints(text: str, k: int = 5) -> str:
    q = _bigrams(text)
    if not q:
        return ""
    scored = []
    for c in _catalog():
        blob = " ".join(str(c.get(f, "")) for f in ("name", "description", "limits", "workaround")) + " " + " ".join(c.get("keywords") or [])
        overlap = len(q & _bigrams(blob))
        if overlap:
            scored.append((overlap, c))
    scored.sort(key=lambda x: -x[0])
    lines = []
    for _, c in scored[:k]:
        lines.append(f"- {c.get('id')} {c.get('name')}（{c.get('status')}）：{c.get('description')} 限制：{c.get('limits') or '无'} 替代：{c.get('workaround') or '无'}")
    return "\n".join(lines)


def _client():
    global _CLIENT
    if _CLIENT is None:
        m = judge.resolve_models()
        _CLIENT = judge.BedrockJudgeClient(m["model"], m["fallback"], m["region"])
    return _CLIENT


def evaluate(event: dict, client=None) -> dict:
    level = (event.get("evaluationLevel") or "TRACE").upper()
    spans = ((event.get("evaluationInput") or {}).get("sessionSpans")) or []
    target = event.get("evaluationTarget") or {}
    tids = target.get("traceIds") or []
    ex = sa.extract_records(spans, tids[0] if (tids and level != "SESSION") else None)
    print(json.dumps({"s12": "reject_eval", "keys": sorted(event.keys()), "evaluatorName": event.get("evaluatorName"),
                      "evaluatorId": event.get("evaluatorId"), "level": level, "n_spans": len(spans),
                      "n_records": len(ex["records"]), "source": ex["records"][0]["source"] if ex["records"] else None},
                     ensure_ascii=False))
    if not ex["trace_matched"]:
        return {"label": "Skipped", "explanation": "session 中没有目标 trace 的 span（可能尚未写入），跳过。"}
    if not ex["records"]:
        if ex["has_assistant_text"]:
            return {"label": "FormatError", "value": 0.0, "explanation": "trace 中有 Agent 回复，但解析不出约定的 JSON。"}
        return {"label": "Skipped", "explanation": "trace 中没有 Agent 的分诊输出，跳过。"}
    rejected = [r for r in ex["records"] if judge.is_rejection(r["output"])]
    if not rejected:
        decs = ", ".join(f"{r['feedback_id']}={r['output'].get('decision')}" for r in ex["records"][:5])
        return {"label": "不适用", "explanation": f"处理建议不是“拒绝”（{decs}），拒绝话术评估不适用。"}
    client = client or _client()
    outs = []
    for rec in rejected[:2]:  # bounded: each judge call can take ~20-40 s and the Lambda timeout is ~120 s
        gold = _golden().get(rec["feedback_id"])
        req = judge.requirement_text(ex.get("user_text") if len(ex["records"]) == 1 else "", gold)
        if req.startswith("（") and gold:
            req = gold.get("title") or req
        hints = capability_hints(req + " " + rec["output"].get("reject_reason", ""))
        r = judge.judge_rejection(client, req, rec["output"], gold, hints)
        outs.append((rec, gold, r))
    if len(outs) == 1:
        rec, gold, r = outs[0]
        return {"label": r["result"]["label"], "value": r["result"]["value"],
                "explanation": judge.explain(r["result"], r["verdict"], rec["feedback_id"], r["model"],
                                             (gold or {}).get("golden_id"))}
    vals = [r["result"]["value"] for _, _, r in outs]
    value = round(sum(vals) / len(vals), 4)
    label = "Pass" if all(r["result"]["label"] == "Pass" for _, _, r in outs) else "Fail"
    expl = "\n".join(judge.explain(r["result"], r["verdict"], rec["feedback_id"], r["model"])[:380] for rec, _, r in outs)
    return {"label": label, "value": value, "explanation": f"批量拒绝 {len(outs)} 条，平均 {value}。\n{expl}"[:2000]}


def handler(event, context=None):
    try:
        if isinstance(event, str):
            event = json.loads(event)
        return evaluate(event)
    except Exception as e:
        print("[s12_reject_quality] EXCEPTION", repr(e))
        return {"errorCode": "S12_REJECT_JUDGE_FAILED", "errorMessage": str(e)[:500]}
