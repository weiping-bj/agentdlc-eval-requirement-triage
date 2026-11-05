"""A4: convert an existing agent's own logs (JSONL) into OTel GenAI spans in CloudWatch.

  python -m labkit.convert_logs check <logs.jsonl>   # field check only, sends nothing
  python -m labkit.convert_logs send  <logs.jsonl>   # must run under opentelemetry-instrument
                                                     # (11-A-convert-logs.sh does that)
Input format: telemetry/LOG_FORMAT.md. Each line becomes one session with one trace that
mimics what Strands Agents emits (scope `strands.telemetry.tracer`):
  invoke_agent <agent>  ->  chat  +  execute_tool <tool> (per tool call)
Timestamps are re-stamped to 10 s apart ending ~2 minutes ago (CloudWatch rejects spans older
than 14 days), keeping the original order. The run is registered in .lab-state/runs.json (mode
"imported") so ./31-run-eval.sh and ./12-A-check-telemetry.sh can use it.
"""
from __future__ import annotations

import json
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import aws

REQUIRED = ["session_id", "timestamp", "user_input", "agent_output"]
OPTIONAL = ["feedback_id", "model", "latency_ms", "agent_name", "tool_calls"]
TRIAGE_KEYS = {"important": ("important", "是否重要"), "urgent": ("urgent", "是否紧急"),
               "decision": ("decision", "处理建议")}


def imported_session_id(lab_name: str, ts_tag: str, original) -> str:
    """Session id for one imported log line: always prefixed and salted with this send's timestamp,
    so re-sending the same file never merges into an earlier send's sessions."""
    orig = re.sub(r"[^A-Za-z0-9_.-]", "_", str(original))
    return f"{lab_name}-imp-{ts_tag}-{orig}"[:256]


def parse_ts(v) -> float | None:
    if isinstance(v, (int, float)):
        return v / 1000.0 if v > 1e11 else float(v)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def triage_json(text):
    """Return the agent output as a dict with standard keys if it looks like a triage result."""
    obj = text
    if isinstance(text, str):
        s, e = text.find("{"), text.rfind("}")
        if s < 0 or e < s:
            return None
        try:
            obj = json.loads(text[s:e + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(obj, dict):
        return None
    out = dict(obj)
    for std, names in TRIAGE_KEYS.items():
        for n in names:
            if n in obj and std not in out:
                out[std] = obj[n]
    if not any(k in out for k in TRIAGE_KEYS):
        return None
    return out


def load(path: str) -> list:
    rows = []
    for i, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                rows.append({"_line": i, "_error": f"不是合法 JSON：{e}"})
    return rows


def check(path: str) -> int:
    rows = load(path)
    print(f"▶ 检查 {path}：{len(rows)} 行")
    bad = 0
    stats = {k: 0 for k in REQUIRED + OPTIONAL}
    triage_ok = old = 0
    now = time.time()
    for i, r in enumerate(rows, 1):
        if "_error" in r:
            print(f"  ❌ 第 {i} 行 {r['_error']}"); bad += 1; continue
        miss = [k for k in REQUIRED if not r.get(k)]
        for k in REQUIRED + OPTIONAL:
            stats[k] += 1 if r.get(k) else 0
        if miss:
            print(f"  ❌ 第 {i} 行缺少必填字段：{miss}"); bad += 1
        ts = parse_ts(r.get("timestamp"))
        if r.get("timestamp") and ts is None:
            print(f"  ❌ 第 {i} 行 timestamp 无法解析：{r.get('timestamp')}"); bad += 1
        elif ts and now - ts > 14 * 86400:
            old += 1
        if triage_json(r.get("agent_output")):
            triage_ok += 1
    n = len(rows) or 1
    print("  字段覆盖率：")
    for k in REQUIRED + OPTIONAL:
        tag = "必填" if k in REQUIRED else "可选"
        print(f"    {'✅' if stats[k] == len(rows) else ('⚠️ ' if stats[k] else '—')} {k:<13}{tag} {stats[k]}/{len(rows)}")
    print(f"  Agent 输出可解析为“三项判断”JSON：{triage_ok}/{len(rows)}（标签准确率评估器需要）")
    if old:
        print(f"  ℹ️  {old} 行时间早于 14 天；发送时所有记录都会按原顺序重排到最近几分钟内")
    if not stats["tool_calls"]:
        print("  ℹ️  没有 tool_calls：工具类评估器（如 ToolSelectionAccuracy）无法使用，其它评估器不受影响")
    if bad:
        print(f"❌ {bad} 处问题，请按 telemetry/LOG_FORMAT.md 修正后重试")
        return 1
    print("✅ 字段检查通过。下一步：./11-A-convert-logs.sh send " + path)
    if triage_ok / n < 0.5:
        print("  ⚠️  不到一半的输出能解析成三项判断：标签准确率评估器会大量 FormatError，建议先调整 agent_output 格式")
    return 0


def _content_record(logger, span, sid: str, t_ns: int, inp: list, out: list) -> None:
    """Emit one content record exactly like Strands + ADOT (split mode) does: scope
    strands.telemetry.tracer, attributes {event.name, session.id}, body.input/output.messages,
    linked to the span by trace/span id."""
    from opentelemetry._logs import LogRecord, SeverityNumber
    sc = span.get_span_context()
    logger.emit(LogRecord(timestamp=t_ns, observed_timestamp=t_ns, trace_id=sc.trace_id, span_id=sc.span_id,
                          trace_flags=sc.trace_flags, severity_number=SeverityNumber.INFO,
                          body={"input": {"messages": inp}, "output": {"messages": out}},
                          attributes={"event.name": "strands.telemetry.tracer", "session.id": sid}))


def send(path: str) -> int:
    from opentelemetry import baggage, context, trace
    from opentelemetry._logs import get_logger_provider
    from opentelemetry.trace import SpanKind, Status, StatusCode

    c = aws.cfg()
    rows = [r for r in load(path) if "_error" not in r and all(r.get(k) for k in REQUIRED)]
    if not rows:
        print("❌ 没有可发送的行，先运行 check"); return 1
    # Re-stamp: keep the original ORDER but lay the sessions out 10 s apart, ending ~2 min ago.
    # (Original gaps can be hours or years; CloudWatch rejects spans older than 14 days.)
    order = sorted(range(len(rows)), key=lambda i: parse_ts(rows[i]["timestamp"]) or 0)
    base = time.time() - 120 - 10 * len(rows)
    starts = {i: base + 10 * k for k, i in enumerate(order)}
    tracer = trace.get_tracer("strands.telemetry.tracer")
    logger = get_logger_provider().get_logger("strands.telemetry.tracer")
    ts_tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"A-imported-{ts_tag}"
    out_dir = Path(c["RESULTS_DIR"]); out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"run-{run_id}.jsonl"
    print(f"▶ 转换并发送 {len(rows)} 条日志 → service.name={c['CONVERTED_SERVICE_NAME']} · 日志组 {c['CONVERTED_LOG_GROUP']}")
    ns = lambda t: int(t * 1e9)  # noqa: E731
    with out.open("w", encoding="utf-8") as fh:
        for i, r in enumerate(rows):
            start = starts[i]
            dur = min(max(float(r.get("latency_ms") or 3000) / 1000.0, 0.2), 9.0)
            sid = imported_session_id(c["LAB_NAME"], ts_tag, r["session_id"])
            agent_name = r.get("agent_name") or "customer_agent"
            model = r.get("model") or "unknown"
            user = str(r["user_input"])
            output = r["agent_output"] if isinstance(r["agent_output"], str) else json.dumps(r["agent_output"], ensure_ascii=False)
            tri = triage_json(r["agent_output"])
            fid = r.get("feedback_id") or (tri or {}).get("feedback_id") or ""
            user_msg = [{"role": "user", "content": {"content": json.dumps([{"text": user}], ensure_ascii=False)}}]
            final_msg = [{"role": "assistant", "content": {"message": output, "finish_reason": "end_turn"}}]
            token = context.attach(baggage.set_baggage("session.id", sid))
            try:
                root = tracer.start_span(f"invoke_agent {agent_name}", kind=SpanKind.INTERNAL, start_time=ns(start))
                ctx = trace.set_span_in_context(root)
                attrs = {"gen_ai.operation.name": "invoke_agent", "gen_ai.system": "strands-agents",
                         "gen_ai.agent.name": agent_name, "gen_ai.request.model": model, "session.id": sid,
                         "s12lab.source": "imported-logs"}
                if fid:
                    attrs["s12lab.feedback_id"] = fid
                if tri:
                    attrs["s12lab.triage_output"] = json.dumps(tri, ensure_ascii=False)
                root.set_attributes(attrs)
                tools = r.get("tool_calls") or []
                step = dur / (len(tools) + 2)
                t = start + 0.05
                chat = tracer.start_span("chat", context=ctx, start_time=ns(t))
                chat.set_attributes({"gen_ai.operation.name": "chat", "gen_ai.system": "strands-agents",
                                     "gen_ai.request.model": model, "session.id": sid})
                _content_record(logger, chat, sid, ns(t + step), user_msg, final_msg)
                chat.end(end_time=ns(t + step))
                t += step
                for tc in tools:
                    tid = f"tooluse_{uuid.uuid4().hex[:20]}"
                    sp = tracer.start_span(f"execute_tool {tc.get('name', 'tool')}", context=ctx, start_time=ns(t))
                    sp.set_attributes({"gen_ai.operation.name": "execute_tool", "gen_ai.system": "strands-agents",
                                       "gen_ai.tool.name": tc.get("name", "tool"), "gen_ai.tool.call.id": tid,
                                       "gen_ai.tool.status": tc.get("status", "success"), "session.id": sid})
                    _content_record(logger, sp, sid, ns(t + step * 0.9),
                                    [{"role": "tool", "content": {"role": "tool", "id": tid,
                                                                  "content": json.dumps(tc.get("input", {}), ensure_ascii=False)}}],
                                    [{"role": "assistant", "content": {"id": tid, "message": json.dumps(
                                        [{"text": json.dumps(tc.get("output", ""), ensure_ascii=False)}], ensure_ascii=False)}}])
                    if tc.get("status") == "error":
                        sp.set_status(Status(StatusCode.ERROR))
                    sp.end(end_time=ns(t + step * 0.9))
                    t += step
                end = start + dur
                _content_record(logger, root, sid, ns(end), user_msg, final_msg)
                root.end(end_time=ns(end))
            finally:
                context.detach(token)
            fh.write(json.dumps({"run_id": run_id, "feedback_id": fid, "session_id": sid, "version": "imported",
                                 "mode": "imported", "model": model, "result": tri, "raw": output,
                                 "latency_ms": int(dur * 1000)}, ensure_ascii=False) + "\n")
            print(f"  ✔ {sid[:60]}  {fid}")
    for prov in (trace.get_tracer_provider(), get_logger_provider()):
        if hasattr(prov, "force_flush"):
            prov.force_flush(30000)
    runs = aws.load_state("runs", default=[])
    runs.append({"run_id": run_id, "file": str(out.relative_to(c["LAB_ROOT"])), "mode": "imported",
                 "version": "imported", "model": "-", "source": path, "count": len(rows),
                 "created": ts_tag, "service_name": c["CONVERTED_SERVICE_NAME"], "log_group": c["CONVERTED_LOG_GROUP"],
                 "agentRuntimeArn": None})
    aws.save_state("runs", runs)
    print(f"✅ 已发送 {len(rows)} 个 session（run_id = {run_id}）。约 2-3 分钟后可在 CloudWatch 查到。")
    print("   下一步：./12-A-check-telemetry.sh   （检查数据能否被 Evaluations 使用）")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in ("check", "send"):
        print("用法: python -m labkit.convert_logs check|send <logs.jsonl>"); sys.exit(1)
    sys.exit(check(sys.argv[2]) if sys.argv[1] == "check" else send(sys.argv[2]))
