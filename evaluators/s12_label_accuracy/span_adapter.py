"""Span adapter: AgentCore Evaluations `sessionSpans` (ADOT span docs + event log records)
-> scenario-12 triage records.

Canonical copy lives in evaluators/shared/; `python3 evaluators/sync_shared.py` copies it next to
each Lambda handler (each Lambda package must be self-contained). Standard library only.

Read order for one trace (see evaluators/README.md "埋点契约"):
  1. span attribute `s12lab.triage_output` (JSON string written by the agent)  -> source="attribute"
  2. final assistant text found in span events / gen_ai attributes / log-record bodies
     (`events` or `span_events` -> body/attributes; top-level `body.output.messages`)   -> source="message"
Feedback id: output JSON `feedback_id` -> span attribute `s12lab.feedback_id` -> `需求编号: <id>` in user text.

Both camelCase (traceId, startTimeUnixNano) and snake_case (trace_id, start_time, span_events)
key styles are accepted, because the Lambda payload shape has varied across delivery modes.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple

OUTPUT_ATTRS = ("s12lab.triage_output", "s12.triage_output", "triage.output")
FEEDBACK_ID_ATTRS = ("s12lab.feedback_id", "s12.feedback_id", "triage.feedback_id", "feedback_id")
VERSION_ATTRS = ("s12lab.agent_version", "agent.version")
USER_EVENT_NAMES = ("gen_ai.user.message",)
ASSISTANT_EVENT_NAMES = ("gen_ai.choice", "gen_ai.assistant.message")
ASSISTANT_ATTRS = ("gen_ai.completion", "gen_ai.output.messages", "output.value", "gen_ai.choice")
USER_ATTRS = ("gen_ai.prompt", "gen_ai.input.messages", "input.value")
FEEDBACK_ID_RE = re.compile(r"需求编号\s*[:：]\s*([A-Za-z0-9][A-Za-z0-9_\-]*)")

# ---- output normalisation ---------------------------------------------------------------
KEY_ALIASES = {
    "feedback_id": ("feedback_id", "id", "requirement_id", "req_id", "需求编号"),
    "important": ("important", "is_important", "importance", "是否重要", "重要"),
    "urgent": ("urgent", "is_urgent", "urgency", "是否紧急", "紧急"),
    "decision": ("decision", "action", "suggestion", "recommendation", "处理建议", "建议"),
    "level": ("level", "priority", "优先级", "等级", "分级"),
    "reasoning": ("reasoning", "reason", "rationale", "判断依据", "依据"),
    "reject_reason": ("reject_reason", "rejection_reason", "拒绝理由"),
    "customer_reply": ("customer_reply", "reply", "customer_message", "话术", "客户话术", "沟通话术"),
}
YES = {"是", "yes", "y", "true", "1", "重要", "紧急", "高"}
NO = {"否", "no", "n", "false", "0", "不重要", "不紧急", "低"}
DECISIONS = ("迭代", "拒绝", "待评估")
DECISION_ALIASES = [
    ("待评估", ("待评估", "需评估", "评估", "pending", "evaluate", "review", "待定", "澄清", "补充信息")),
    ("拒绝", ("拒绝", "reject", "decline", "不做", "驳回")),
    ("迭代", ("迭代", "iterate", "accept", "纳入", "开发", "修复", "fix")),
]
LEVELS = ("P0", "P1", "P2")


def norm_yes_no(v: Any) -> Optional[str]:
    if isinstance(v, bool):
        return "是" if v else "否"
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in YES:
        return "是"
    if s in NO:
        return "否"
    return None


def norm_decision(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip().lower()
    if not s:
        return None
    for canon, words in DECISION_ALIASES:
        if s == canon or s in words:
            return canon
    for canon, words in DECISION_ALIASES:
        if any(w in s for w in words):
            return canon
    return None


def norm_level(v: Any) -> Optional[str]:
    if v is None or v == "":
        return None
    s = str(v).strip().upper()
    if s in LEVELS:
        return s
    if s in ("0", "1", "2"):
        return "P" + s
    return None


def _pick(obj: dict, field: str):
    for k in KEY_ALIASES[field]:
        if k in obj and obj[k] not in (None, ""):
            return obj[k]
    return None


def normalize_output(obj: dict) -> Dict[str, Any]:
    """Map an agent output object (any of the tolerated key/value variants) to the canonical keys.

    Adds `_issues` (list of Chinese messages) for invalid / missing label values.
    """
    out: Dict[str, Any] = {}
    issues: List[str] = []
    fid = _pick(obj, "feedback_id")
    out["feedback_id"] = str(fid).strip() if fid is not None else None
    for f in ("important", "urgent"):
        raw = _pick(obj, f)
        out[f] = norm_yes_no(raw)
        if out[f] is None:
            issues.append(f"{f} 取值无效: {raw!r}")
    raw = _pick(obj, "decision")
    out["decision"] = norm_decision(raw)
    if out["decision"] is None:
        issues.append(f"decision 取值无效: {raw!r}")
    out["level"] = norm_level(_pick(obj, "level"))
    for f in ("reasoning", "reject_reason", "customer_reply"):
        v = _pick(obj, f)
        out[f] = "" if v is None else (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
    out["_issues"] = issues
    return out


def looks_like_triage(obj: Any) -> bool:
    return isinstance(obj, dict) and _pick(obj, "decision") is not None and (
        _pick(obj, "important") is not None or _pick(obj, "urgent") is not None or _pick(obj, "level") is not None)


def triage_objects(obj: Any) -> List[dict]:
    """Return the triage dicts contained in a parsed JSON value (single, list, or {"results": [...]})."""
    if isinstance(obj, list):
        return [o for o in obj if looks_like_triage(o)]
    if isinstance(obj, dict):
        if looks_like_triage(obj):
            return [obj]
        for k in ("results", "items", "outputs", "data"):
            if isinstance(obj.get(k), list):
                return [o for o in obj[k] if looks_like_triage(o)]
    return []


_DECODER = json.JSONDecoder()


def json_values_in_text(text: str) -> List[Any]:
    """All top-level JSON objects/arrays embedded in free text (fenced or bare)."""
    vals: List[Any] = []
    if not isinstance(text, str) or not text:
        return vals
    s = text.strip()
    try:
        return [json.loads(s)]
    except ValueError:
        pass
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch in "{[":
            try:
                v, end = _DECODER.raw_decode(s, i)
                vals.append(v)
                i = end
                continue
            except ValueError:
                pass
        i += 1
    return vals


def triage_from_text(text: str) -> List[dict]:
    found: List[dict] = []
    for v in json_values_in_text(text):
        found.extend(triage_objects(v))
    return found


# ---- span access helpers -----------------------------------------------------------------

def as_dict(span: Any) -> Dict[str, Any]:
    if isinstance(span, dict):
        return span
    if isinstance(span, str):
        try:
            v = json.loads(span)
        except ValueError:
            return {}
        return v if isinstance(v, dict) else {}
    return {}


def iter_spans(session_spans: Iterable[Any]) -> List[Dict[str, Any]]:
    return [d for d in (as_dict(s) for s in (session_spans or [])) if d]


def attrs(span: Dict[str, Any]) -> Dict[str, Any]:
    a = span.get("attributes")
    return a if isinstance(a, dict) else {}


def _norm_tid(t: Any) -> str:
    s = re.sub(r"[^0-9a-f]", "", str(t or "").lower())
    return s[-32:] if len(s) >= 32 else s


def trace_id_of(span: Dict[str, Any]) -> str:
    ctx = span.get("context") if isinstance(span.get("context"), dict) else {}
    return _norm_tid(span.get("traceId") or span.get("trace_id") or ctx.get("trace_id") or ctx.get("traceId"))


def start_of(span: Dict[str, Any]) -> float:
    for k in ("startTimeUnixNano", "start_time", "startTime", "timeUnixNano", "observedTimeUnixNano", "time_unix_nano"):
        v = span.get(k)
        if v in (None, ""):
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def spans_for_trace(spans: List[Dict[str, Any]], target_trace_id: Optional[str]) -> Tuple[List[Dict[str, Any]], bool]:
    """Spans of the target trace. Returns (spans, matched). Falls back to all spans if nothing matches."""
    if not target_trace_id:
        return spans, True
    t = _norm_tid(target_trace_id)
    sel = [s for s in spans if trace_id_of(s) == t]
    if sel:
        return sel, True
    return spans, False


def _events(span: Dict[str, Any]) -> List[Dict[str, Any]]:
    evs: List[Dict[str, Any]] = []
    for k in ("events", "span_events", "spanEvents"):
        raw = span.get(k)
        if isinstance(raw, list):
            evs.extend(e for e in raw if isinstance(e, dict))
    return evs


def _event_name(e: Dict[str, Any]) -> str:
    return str(e.get("name") or e.get("eventName") or e.get("event_name") or (attrs(e).get("event.name")) or "")


def content_text(c: Any) -> str:
    """Flatten message content (str / JSON-encoded block list / dict / list of blocks) to plain text.

    Tool-use / tool-result / reasoning blocks are skipped: they are not the agent's answer.
    """
    if c is None:
        return ""
    if isinstance(c, str):
        st = c.strip()
        if st[:1] in "[{":
            try:
                parsed = json.loads(st)
            except ValueError:
                return c
            if isinstance(parsed, list) and all(isinstance(b, dict) for b in parsed) and any(
                    k in b for b in parsed for k in ("text", "toolUse", "toolResult", "reasoningContent")):
                return content_text(parsed)
            if isinstance(parsed, dict) and any(k in parsed for k in ("text", "message", "content")) and not looks_like_triage(parsed):
                return content_text(parsed)
        return c
    if isinstance(c, list):
        parts = []
        for b in c:
            if isinstance(b, dict):
                if any(k in b for k in ("toolUse", "toolResult", "reasoningContent")):
                    continue
                t = b.get("text")
                if isinstance(t, str):
                    parts.append(t)
                elif "content" in b or "message" in b:
                    parts.append(content_text(b.get("content") if "content" in b else b.get("message")))
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(p for p in parts if p)
    if isinstance(c, dict):
        if any(k in c for k in ("toolUse", "toolResult")):
            return ""
        for k in ("text", "message", "content"):
            if k in c:
                return content_text(c[k])
    return ""


def _messages_texts(msgs: Any, want_role: str) -> List[str]:
    out: List[str] = []
    if isinstance(msgs, str):
        try:
            msgs = json.loads(msgs)
        except ValueError:
            return [msgs] if want_role == "assistant" else []
    if not isinstance(msgs, list):
        return out
    for m in msgs:
        if isinstance(m, dict):
            role = m.get("role")
            if role not in (None, want_role):
                continue
            if role is None and want_role == "user":
                continue
            src = m.get("content") if "content" in m else (m.get("parts") if "parts" in m else m.get("message"))
            t = content_text(src)
            if t:
                out.append(t)
        elif isinstance(m, str) and want_role == "assistant":
            out.append(m)
    return out


def span_texts(span: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """(user_texts, assistant_texts) carried by one span doc or log record."""
    users: List[str] = []
    assistants: List[str] = []
    a = attrs(span)
    for k in USER_ATTRS:
        if k in a:
            v = a[k]
            users.extend(_messages_texts(v, "user") if not isinstance(v, str) or v.strip()[:1] == "[" else [v])
    for k in ASSISTANT_ATTRS:
        if k in a:
            v = a[k]
            if isinstance(v, str) and v.strip()[:1] != "[":
                assistants.append(content_text(v))
            else:
                assistants.extend(_messages_texts(v, "assistant"))
    bodies: List[Tuple[str, Any]] = []
    if isinstance(span.get("body"), (dict, str)):
        bodies.append((_event_name(span), span.get("body")))
    for e in _events(span):
        bodies.append((_event_name(e), e.get("body") if "body" in e else e.get("attributes")))
    for name, body in bodies:
        if isinstance(body, str):
            if name in ASSISTANT_EVENT_NAMES:
                assistants.append(content_text(body))
            elif name in USER_EVENT_NAMES:
                users.append(content_text(body))
            continue
        if not isinstance(body, dict):
            continue
        inp = body.get("input")
        outp = body.get("output")
        if isinstance(inp, dict) or isinstance(outp, dict):
            if isinstance(inp, dict):
                users.extend(_messages_texts(inp.get("messages"), "user"))
            if isinstance(outp, dict):
                assistants.extend(_messages_texts(outp.get("messages"), "assistant"))
            continue
        if name in ASSISTANT_EVENT_NAMES:
            t = content_text(body.get("message") if "message" in body else body.get("content"))
            if t:
                assistants.append(t)
        elif name in USER_EVENT_NAMES:
            t = content_text(body.get("content") if "content" in body else body.get("message"))
            if t:
                users.append(t)
    return [u for u in users if u], [s for s in assistants if s]


# ---- main entry --------------------------------------------------------------------------

def _attr_first(spans: List[Dict[str, Any]], keys: Tuple[str, ...]) -> Optional[str]:
    c: Counter = Counter()
    for s in spans:
        a = attrs(s)
        for k in keys:
            v = a.get(k)
            if v not in (None, ""):
                c[str(v)] += 1
                break
    return c.most_common(1)[0][0] if c else None


def extract_records(session_spans: Iterable[Any], target_trace_id: Optional[str] = None) -> Dict[str, Any]:
    """Extract triage records for one trace (or the whole session when target_trace_id is None).

    Returns {"records": [ {feedback_id, output, raw, source} ], "trace_matched": bool,
             "has_assistant_text": bool, "span_count": int, "agent_version": str|None, "user_text": str}
    """
    all_spans = iter_spans(session_spans)
    spans, matched = spans_for_trace(all_spans, target_trace_id)
    spans = sorted(spans, key=start_of)
    attr_fid = _attr_first(spans, FEEDBACK_ID_ATTRS)
    version = _attr_first(spans, VERSION_ATTRS)

    user_texts: List[str] = []
    assistant_texts: List[Tuple[float, int, str]] = []
    for idx, s in enumerate(spans):
        u, a = span_texts(s)
        user_texts.extend(u)
        for t in a:
            assistant_texts.append((start_of(s), idx, t))

    text_fid = None
    for u in user_texts:
        m = FEEDBACK_ID_RE.search(u)
        if m:
            text_fid = m.group(1)
            break

    raw_objs: List[dict] = []
    source = None
    for s in reversed(spans):
        a = attrs(s)
        for k in OUTPUT_ATTRS:
            v = a.get(k)
            if v in (None, ""):
                continue
            parsed = v
            if isinstance(v, str):
                vals = json_values_in_text(v)
                parsed = vals[0] if vals else None
            objs = triage_objects(parsed)
            if objs:
                raw_objs = objs
                source = "attribute"
                break
        if raw_objs:
            break
    if not raw_objs:
        for _, _, t in sorted(assistant_texts, key=lambda x: (x[0], x[1]), reverse=True):
            objs = triage_from_text(t)
            if objs:
                raw_objs = objs
                source = "message"
                break

    records: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    single = len(raw_objs) == 1
    for i, o in enumerate(raw_objs):
        norm = normalize_output(o)
        fid = norm.get("feedback_id") or (attr_fid if single else None) or (text_fid if single else None)
        if not fid:
            fid = f"_unknown_{i}"
        norm["feedback_id"] = fid
        if fid not in records:
            order.append(fid)
        records[fid] = {"feedback_id": fid, "output": norm, "raw": o, "source": source}
    return {
        "records": [records[k] for k in order],
        "trace_matched": matched,
        "has_assistant_text": bool(assistant_texts) or source == "attribute",
        "span_count": len(spans),
        "agent_version": version,
        "feedback_id_hint": attr_fid or text_fid,
        "user_text": user_texts[0] if user_texts else "",
    }


def group_by_trace(session_spans: Iterable[Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Group span docs by normalised trace id (used by the local batch scorer)."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for s in iter_spans(session_spans):
        groups.setdefault(trace_id_of(s) or "_no_trace", []).append(s)
    return groups
