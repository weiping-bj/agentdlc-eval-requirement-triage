import json
import uuid

from opentelemetry import baggage, context, trace

tracer = trace.get_tracer("s12lab.triage")


def handle_one(item: dict) -> dict:
    """处理一条需求：一条需求 = 一个 session = 一个 trace。在每次处理请求的入口调用。"""
    session_id = f"s12lab-{uuid.uuid4()}"  # 每条需求一个新的唯一 ID
    token = context.attach(baggage.set_baggage("session.id", session_id))  # 之后产生的 span 都带上它
    try:
        with tracer.start_as_current_span("s12lab.triage") as span:
            span.set_attribute("session.id", session_id)
            fid = item.get("id") or item.get("feedback_id")  # 需求编号：按你们的字段名改
            if fid:
                span.set_attribute("s12lab.feedback_id", fid)
            result = my_agent(item)  # 换成你们原来的 Agent 调用；返回 dict，字段见 evaluators/README.md 第 1 节
            span.set_attribute("s12lab.triage_output", json.dumps(result, ensure_ascii=False, default=str))
            return result
    finally:
        context.detach(token)
