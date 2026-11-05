"""Shared helpers for evaluator tests (named uniquely so data/tests can run in the same pytest session)."""
import importlib
import json
import os
import sys

EV = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(EV)
GOLDEN = os.path.join(REPO, "data", "golden", "golden_set.jsonl")


def load_pkg(pkg: str, module: str = "lambda_function"):
    """Import a Lambda package's module in isolation (each package has its own copies of shared files)."""
    d = os.path.join(EV, pkg)
    for m in ("lambda_function", "span_adapter", "golden_store", "label_scoring", "judge", "rubric", "batch_score"):
        sys.modules.pop(m, None)
    sys.path.insert(0, d)
    try:
        return importlib.import_module(module)
    finally:
        sys.path.remove(d)


def triage_span(out: dict, trace="a" * 32, fid=None, start=10, version="v1"):
    attrs = {"session.id": "sess-1", "s12lab.agent_version": version, "s12lab.triage_output": json.dumps(out, ensure_ascii=False)}
    if fid:
        attrs["s12lab.feedback_id"] = fid
    return {"traceId": trace, "spanId": "s1", "name": "s12lab.triage", "scope": {"name": "s12lab.triage"},
            "startTimeUnixNano": start, "attributes": attrs}


def event(spans, trace="a" * 32, name="s12lab_test_label_accuracy", refs=None, level="TRACE"):
    ev = {"schemaVersion": "1.0", "evaluatorId": "x-1", "evaluatorName": name, "evaluationLevel": level,
          "evaluationInput": {"sessionSpans": spans}, "evaluationReferenceInputs": refs or []}
    if level != "SESSION":
        ev["evaluationTarget"] = {"traceIds": [trace]}
    return ev


def output_from_gold(g: dict, **over) -> dict:
    e = g["expected"]
    out = {"feedback_id": g["feedback_id"], "important": e["important"], "urgent": e["urgent"], "decision": e["decision"],
           "level": e.get("level"), "reasoning": "依据能力目录", "reject_reason": "", "customer_reply": ""}
    if e["decision"] == "拒绝":
        out["reject_reason"] = "；".join(g.get("reject_points") or ["理由"])
        out["customer_reply"] = "；".join(g.get("reply_points") or ["话术"])
    out.update(over)
    return out
