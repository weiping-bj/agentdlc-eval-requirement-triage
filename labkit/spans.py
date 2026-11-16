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
    """Trace ids that contain an agent invocation (s12lab.triage or invoke_agent span)."""
    out = []
    for d in docs:
        name = d.get("name", "")
        attrs = d.get("attributes", {}) or {}
        if name == "s12lab.triage" or attrs.get("gen_ai.operation.name") == "invoke_agent" or name.startswith("invoke_agent"):
            t = d.get("traceId")
            if t and t not in out:
                out.append(t)
    return out


def spans_only(docs: list) -> list:
    return [d for d in docs if d.get("spanId") and d.get("name")]
