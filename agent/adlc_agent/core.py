"""Build and run the triage agent (V0 / V1 / V1 参考版 ...).

Label scheme: with ADLC_LABEL_SCHEME=level (set by ./ext-switch-label-scheme.sh through the Runtime environment,
or LABEL_SCHEME from 00-config.sh when the agent runs on the lab EC2) the system prompt of every version gets
prompts/level-scheme.md appended, which asks for the `level` field (P0/P1/P2) instead of null."""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

from opentelemetry import trace
from strands import Agent
from strands.models import BedrockModel

from .tools import TOOLSETS

PROMPTS = Path(__file__).resolve().parent / "prompts"
LABELS = {"important": {"是", "否"}, "urgent": {"是", "否"}, "decision": {"迭代", "拒绝", "待评估"}}


LEVEL_ADDENDUM = PROMPTS / "level-scheme.md"


def label_scheme() -> str:
    s = (os.environ.get("ADLC_LABEL_SCHEME") or os.environ.get("LABEL_SCHEME") or "binary").strip().lower()
    return "level" if s == "level" else "binary"


def system_prompt(version: str) -> str:
    text = (PROMPTS / f"{version}.md").read_text(encoding="utf-8")
    if label_scheme() == "level":
        text = text.rstrip() + "\n\n" + LEVEL_ADDENDUM.read_text(encoding="utf-8").strip() + "\n"
    return text


def build_agent(version: str | None = None, model_id: str | None = None, region: str | None = None,
                session_id: str | None = None, extra_attrs: dict | None = None) -> Agent:
    version = (version or os.environ.get("AGENT_VERSION") or "v0").lower()
    if not (PROMPTS / f"{version}.md").exists():
        raise ValueError(f"unknown AGENT_VERSION {version!r}: no prompts/{version}.md")
    mid = model_id or os.environ.get("AGENT_MODEL_ID", "us.anthropic.claude-sonnet-5-5")
    kw = {"max_tokens": 2048}
    if "anthropic.claude" not in mid:
        kw["temperature"] = 0.0  # Claude 5.x models reject `temperature`; others (e.g. Nova) accept it
    model = BedrockModel(
        model_id=mid,
        region_name=region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2",
        **kw,
    )
    attrs = {"adlc.agent_version": version}
    if session_id:
        attrs["session.id"] = session_id
    attrs.update(extra_attrs or {})
    return Agent(
        name=f"adlc_triage_{version}",
        model=model,
        system_prompt=system_prompt(version),
        tools=TOOLSETS.get(version, TOOLSETS["v1-ref"]),  # a version without its own entry (v1, v2...) gets the V1 参考版 tools
        callback_handler=None,
        trace_attributes=attrs,
    )


def format_input(item) -> str:
    """Render one feedback item as the user message (the feedback id is always line 1)."""
    if isinstance(item, str):
        return item
    fid = item.get("id") or item.get("feedback_id") or ""
    lines = [f"需求编号: {fid}"]
    labels = [("title", "标题"), ("content", "需求描述"), ("description", "需求描述"),
              ("customer", "客户"), ("customer_name", "客户"), ("industry", "行业"),
              ("customer_tier", "客户等级"), ("feedback_type", "反馈类型"), ("channel", "来源渠道"),
              ("module", "相关模块"),
              ("submitted_at", "提交时间"), ("contract_commitment", "合同承诺")]
    seen = set()
    for k, label in labels:
        v = item.get(k)
        if v not in (None, "") and label not in seen:
            lines.append(f"{label}: {v}")
            seen.add(label)
    return "\n".join(lines)


_JSON_RE = re.compile(r"\{.*\}", re.S)


def parse_output(text: str) -> dict:
    """Extract the JSON object from the model's final message; never raises."""
    m = _JSON_RE.search(text or "")
    if not m:
        return {"_parse_error": "no JSON object in output"}
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return {"_parse_error": f"invalid JSON: {e}"}
    for k, allowed in LABELS.items():
        if obj.get(k) not in allowed:
            obj.setdefault("_warnings", []).append(f"{k}={obj.get(k)!r} not in {sorted(allowed)}")
    return obj


def triage(item, version: str | None = None, model_id: str | None = None,
           session_id: str | None = None) -> dict:
    """Triage one feedback item inside a `adlc.triage` span.

    The wrapper span carries the span contract from data/HANDOFF.md section 4:
    adlc.feedback_id, adlc.agent_version and adlc.triage_output (the final JSON),
    so evaluators never depend on whether message bodies are present on chat spans.
    """
    version = (version or os.environ.get("AGENT_VERSION") or "v0").lower()
    session_id = session_id or f"adlc-{uuid.uuid4()}"
    fid = (item.get("id") or item.get("feedback_id")) if isinstance(item, dict) else None
    tracer = trace.get_tracer("adlc.triage")
    with tracer.start_as_current_span("adlc.triage") as span:
        span.set_attribute("session.id", session_id)
        span.set_attribute("adlc.agent_version", version)
        if fid:
            span.set_attribute("adlc.feedback_id", fid)
        agent = build_agent(version, model_id, session_id=session_id,
                            extra_attrs={"adlc.feedback_id": fid} if fid else None)
        result = agent(format_input(item))
        text = str(result).strip()
        out = parse_output(text)
        if fid and not out.get("feedback_id"):
            out["feedback_id"] = fid
        span.set_attribute("adlc.triage_output", json.dumps(out, ensure_ascii=False))
    return {"session_id": session_id, "result": out, "raw": text}
