"""Fetch the OTel span documents of one session from CloudWatch Logs (Logs Insights).

Split telemetry mode: spans live in `aws/spans`, model input/output event records in the
agent's own log group. Both are returned (this is what AgentCore Evaluations expects in
`evaluationInput.sessionSpans`).
"""
from __future__ import annotations

import json
import time

from . import aws

QUERY = ('fields @timestamp, @message | filter ispresent(scope.name) and ispresent(traceId) '
         '| filter attributes.session.id = "{sid}" | sort @timestamp asc | limit 10000')


def _query(groups: list, sid: str, start: int, end: int) -> list:
    logs = aws.client("logs")
    existing = []
    for g in groups:
        if not g:
            continue
        r = logs.describe_log_groups(logGroupNamePrefix=g)
        if any(x["logGroupName"] == g for x in r.get("logGroups", [])):
            existing.append(g)
    if not existing:
        return []
    qid = logs.start_query(logGroupNames=existing, startTime=start, endTime=end,
                           queryString=QUERY.format(sid=sid.replace('"', "")))["queryId"]
    for _ in range(120):
        r = logs.get_query_results(queryId=qid)
        if r["status"] in ("Complete", "Failed", "Cancelled", "Timeout"):
            break
        time.sleep(1)
    docs = []
    for row in r.get("results", []):
        for f in row:
            if f["field"] == "@message" and f["value"].startswith("{"):
                try:
                    docs.append(json.loads(f["value"]))
                except json.JSONDecodeError:
                    pass
    return docs


def session_docs(sid: str, log_group: str | None, start: int, end: int | None = None) -> list:
    """All span docs + event records for a session (epoch seconds window)."""
    end = end or int(time.time()) + 60
    return _query(["aws/spans", log_group], sid, start, end)


def trace_ids(docs: list) -> list:
    """Trace ids that contain an agent invocation (adlc.triage or invoke_agent span)."""
    out = []
    for d in docs:
        name = d.get("name", "")
        attrs = d.get("attributes", {}) or {}
        if name == "adlc.triage" or attrs.get("gen_ai.operation.name") == "invoke_agent" or name.startswith("invoke_agent"):
            t = d.get("traceId")
            if t and t not in out:
                out.append(t)
    return out


def spans_only(docs: list) -> list:
    return [d for d in docs if d.get("spanId") and d.get("name")]


# ---------------------------------------------------------------------------------------------- tool calls
# Strands + ADOT (split mode, telemetry/fixtures/local-adot-v0-sessions.json): one `execute_tool <name>` span per call
# (gen_ai.tool.name / call.id / status, duration) and one log record on the same spanId whose body carries the tool
# input (input.messages[role=tool]) and its result (output.messages[].content.message, a JSON list of text blocks).
# Fallback: the toolUse / toolResult blocks of the chat messages, matched by call id.

TOOL_INPUT_MAX = 1500
TOOL_OUTPUT_MAX = 3000


def _maybe_json(v, depth: int = 0):
    if isinstance(v, str) and depth < 4:
        s = v.strip()
        if s[:1] in ("[", "{"):
            try:
                return _maybe_json(json.loads(s), depth + 1)
            except (ValueError, RecursionError):  # not JSON, or nested too deep to parse: keep the text
                return v
    return v


def _text_of(v) -> str:
    """Plain text of a tool payload: content blocks [{"text": ...}] joined, JSON objects re-serialized."""
    v = _maybe_json(v)
    if isinstance(v, list) and v and all(isinstance(b, dict) and "text" in b for b in v):
        return "\n".join(str(b.get("text") or "") for b in v)
    if isinstance(v, dict):
        for k in ("message", "content"):
            if k in v and set(v) <= {"message", "content", "role", "id"}:
                return _text_of(v[k])
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return json.dumps(v, ensure_ascii=False)
    return "" if v is None else str(v)


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _walk_blocks(v, uses: dict, results: dict, depth: int = 0):
    """Collect toolUse inputs and toolResult contents by call id from any (possibly JSON-encoded) message content."""
    if depth > 8:
        return
    v = _maybe_json(v)
    if isinstance(v, dict):
        tu, tr = v.get("toolUse"), v.get("toolResult")
        if isinstance(tu, dict) and tu.get("toolUseId"):
            uses.setdefault(str(tu["toolUseId"]), tu.get("input"))
        if isinstance(tr, dict) and tr.get("toolUseId"):
            results.setdefault(str(tr["toolUseId"]), (tr.get("status"), tr.get("content")))
        for x in v.values():
            _walk_blocks(x, uses, results, depth + 1)
    elif isinstance(v, list):
        for x in v:
            _walk_blocks(x, uses, results, depth + 1)


def _msgs(body: dict, side: str) -> list:
    m = body.get(side)
    msgs = m.get("messages") if isinstance(m, dict) else None
    return [x for x in msgs if isinstance(x, dict)] if isinstance(msgs, list) else []


def _span_key(d: dict):
    k = d.get("spanId")
    return str(k) if isinstance(k, (str, int)) and not isinstance(k, bool) else None


def tool_calls(docs: list, trace_id: str | None = None) -> list:
    """Tool calls of one session in call order: [{name, input, output, status, duration_ms, call_id}].

    Display data for the results page (labkit.results_page), read from span docs labkit.evaluate already fetched;
    input / output are clipped. Tolerates odd docs (never raises on a missing or oddly typed field)."""
    docs = [d for d in docs or [] if isinstance(d, dict) and (not trace_id or d.get("traceId") in (None, trace_id))]
    logs_by_span: dict = {}
    uses, results = {}, {}
    for d in docs:
        if not d.get("name") and isinstance(d.get("body"), dict):
            if _span_key(d) is not None:
                logs_by_span.setdefault(_span_key(d), []).append(d["body"])
            _walk_blocks(d["body"], uses, results)
    calls = []
    for d in docs:
        attrs = d.get("attributes") if isinstance(d.get("attributes"), dict) else {}
        name = str(d.get("name") or "")
        if not (name.startswith("execute_tool") or attrs.get("gen_ai.operation.name") == "execute_tool"):
            continue
        cid = attrs.get("gen_ai.tool.call.id")
        cid = None if cid is None else str(cid)
        tool = attrs.get("gen_ai.tool.name") or name.removeprefix("execute_tool").strip() or "tool"
        inp = out = None
        for body in logs_by_span.get(_span_key(d), []) if _span_key(d) is not None else []:
            for m in _msgs(body, "input"):
                if inp is None and m.get("role") == "tool":
                    inp = m.get("content")
            for m in _msgs(body, "output"):
                if out is None and m.get("content") is not None:
                    out = m.get("content")
        if inp is None and cid in uses:
            inp = uses[cid]
        # status: the tool's own (gen_ai.tool.status), else the toolResult's, else the span's unless it is UNSET
        st = attrs.get("gen_ai.tool.status") or (results[cid][0] if cid in results else None)
        if not st:
            code = (d.get("status") or {}).get("code") if isinstance(d.get("status"), dict) else None
            st = code if code and str(code).upper() != "UNSET" else None
        if out is None and cid in results:
            out = results[cid][1]
        dur = d.get("durationNano")
        t0, t1 = d.get("startTimeUnixNano"), d.get("endTimeUnixNano")
        t0 = t0 if isinstance(t0, (int, float)) and not isinstance(t0, bool) else None
        t1 = t1 if isinstance(t1, (int, float)) and not isinstance(t1, bool) else None
        if not isinstance(dur, (int, float)) or isinstance(dur, bool):
            dur = (t1 - t0) if t0 is not None and t1 is not None else None
        calls.append({"name": str(tool), "input": _clip(_text_of(inp), TOOL_INPUT_MAX),
                      "output": _clip(_text_of(out), TOOL_OUTPUT_MAX), "status": None if st is None else str(st),
                      "duration_ms": round(dur / 1e6, 1) if isinstance(dur, (int, float)) else None,
                      "call_id": cid, "_t": t0})
    calls.sort(key=lambda c: (c["_t"] is None, c["_t"] or 0))  # call order; spans without a start time last
    for c in calls:
        del c["_t"]
    return calls
