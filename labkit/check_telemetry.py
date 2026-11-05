"""A5: check whether the telemetry in CloudWatch is good enough for AgentCore Evaluations.

  python -m labkit.check_telemetry                # latest run (A local / imported / B runtime)
  python -m labkit.check_telemetry --run <run_id> [--sessions 5] [--no-probe]
Checks a sample of the run's sessions and prints a verdict:
  通过    all critical items present -> continue to chapter 6
  有缺口  critical present, optional missing -> evaluation works, some evaluators limited
  差距大  a critical item is missing -> fix instrumentation or switch to path B
With --probe (default) it also calls the Evaluate API once (Builtin.Helpfulness) on one
session to prove the service can actually read the data.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from . import aws, spans

SUPPORTED_SCOPES = ("strands.telemetry.tracer", "opentelemetry.instrumentation.langchain",
                    "openinference.instrumentation.langchain", "openinference.instrumentation", "crewai")


def _content_ok(docs, kind: str) -> bool:
    for d in docs:
        body = d.get("body") or {}
        if isinstance(body, dict):
            part = body.get(kind) or {}
            if isinstance(part, dict) and part.get("messages"):
                return True
        for ev in d.get("events", []) or []:
            n = ev.get("name", "")
            if kind == "input" and n in ("gen_ai.user.message", "gen_ai.system.message"):
                return True
            if kind == "output" and n in ("gen_ai.choice", "gen_ai.assistant.message"):
                return True
        attrs = d.get("attributes", {}) or {}
        if kind == "output" and (attrs.get("s12lab.triage_output") or attrs.get("gen_ai.output.messages")):
            return True
        if kind == "input" and attrs.get("gen_ai.input.messages"):
            return True
    return False


def check_session(sid: str, log_group: str, start: int) -> dict:
    docs = spans.session_docs(sid, log_group, start)
    sp = spans.spans_only(docs)
    scopes = {((d.get("scope") or {}).get("name") or "") for d in sp}
    ops = {(d.get("attributes") or {}).get("gen_ai.operation.name") for d in sp}
    attrs = [d.get("attributes") or {} for d in sp]
    res = {d.get("resource", {}).get("attributes", {}).get("service.name") for d in sp if d.get("resource")}
    r = {
        "spans": len(sp), "docs": len(docs),
        "c_spans": bool(sp),
        "c_session": bool(sp) and all(a.get("session.id") == sid for a in attrs),
        "c_scope": any(s.startswith(SUPPORTED_SCOPES) for s in scopes),
        "c_agent": "invoke_agent" in ops,
        "c_input": _content_ok(docs, "input"),
        "c_output": _content_ok(docs, "output"),
        "o_tools": "execute_tool" in ops,
        "o_model": any(a.get("gen_ai.request.model") for a in attrs),
        "o_tokens": any(a.get("gen_ai.usage.input_tokens") or a.get("gen_ai.usage.prompt_tokens") for a in attrs),
        "o_triage": any(a.get("s12lab.triage_output") or a.get("s12lab.feedback_id") for a in attrs),
        "o_service": bool(res - {None, ""}),
        "scopes": sorted(s for s in scopes if s), "service_names": sorted(x for x in res if x),
        "trace_ids": spans.trace_ids(docs), "_docs": docs,
    }
    return r


ITEMS = [
    ("c_spans", "能在 CloudWatch（aws/spans）按 session.id 找到 span", True),
    ("c_session", "每个 span 都带 session.id", True),
    ("c_scope", "span 来自受支持的框架埋点（如 Strands: strands.telemetry.tracer）", True),
    ("c_agent", "有 Agent 调用 span（gen_ai.operation.name = invoke_agent）", True),
    ("c_input", "能取到用户输入内容", True),
    ("c_output", "能取到 Agent 最终输出内容", True),
    ("o_tools", "有工具调用 span（execute_tool）→ 工具类评估器可用", False),
    ("o_model", "记录了模型 ID（gen_ai.request.model）", False),
    ("o_tokens", "记录了 token 用量 → 成本分析可用", False),
    ("o_triage", "带需求编号 / 判断结果属性 → 标签准确率评估器最稳", False),
    ("o_service", "带 service.name → 在线评估 / 控制台按 Agent 聚合", False),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run")
    ap.add_argument("--sessions", type=int, default=5)
    ap.add_argument("--no-probe", action="store_true")
    a = ap.parse_args(argv)
    runs = aws.load_state("runs", default=[])
    if not runs:
        print("❌ 没有运行记录。先运行 ./10-A-run-local.sh trace 3 或 ./11-A-convert-logs.sh send <文件>"); return 1
    run = next((r for r in runs if r["run_id"] == a.run), None) if a.run else runs[-1]
    if not run:
        print(f"❌ 找不到 run {a.run}"); return 1
    from pathlib import Path
    recs = [json.loads(x) for x in (Path(aws.cfg()["LAB_ROOT"]) / run["file"]).read_text(encoding="utf-8").splitlines() if x.strip()]
    recs = [r for r in recs if not r.get("error")][: a.sessions]
    start = int(datetime.strptime(run["created"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()) - 3600
    print(f"▶ 检查 run {run['run_id']}（{run['mode']}）的 {len(recs)} 个 session · 日志组 {run.get('log_group')}")
    results = []
    for rec in recs:
        r = check_session(rec["session_id"], run.get("log_group"), start)
        results.append(r)
        print(f"  · {rec['session_id'][:70]}  span={r['spans']} 文档={r['docs']}")
    if not results:
        print("❌ 这个 run 没有成功的 session"); return 1
    print("\n检查项（✅ 全部 session 满足 · ⚠️ 部分满足 · ❌ 都不满足）")
    crit_fail = opt_fail = 0
    for key, text, critical in ITEMS:
        n = sum(1 for r in results if r[key])
        mark = "✅" if n == len(results) else ("⚠️ " if n else "❌")
        tag = "必需" if critical else "可选"
        print(f"  {mark} [{tag}] {text}  ({n}/{len(results)})")
        if n < len(results):
            if critical:
                crit_fail += 1
            else:
                opt_fail += 1
    sc = sorted({s for r in results for s in r["scopes"]})
    sv = sorted({s for r in results for s in r["service_names"]})
    print(f"  埋点来源 scope：{sc or '无'}   service.name：{sv or '无'}")
    probe = None
    if not a.no_probe and crit_fail == 0:
        r0 = results[0]
        try:
            out = aws.client("bedrock-agentcore").evaluate(
                evaluatorId="Builtin.Helpfulness", evaluationInput={"sessionSpans": r0["_docs"]},
                evaluationTarget={"traceIds": [r0["trace_ids"][-1]]})
            res = (out.get("evaluationResults") or [{}])[-1]
            probe = res
            if res.get("errorCode"):
                print(f"  ❌ 试评估（Builtin.Helpfulness）失败：{res.get('errorCode')} {res.get('errorMessage', '')[:200]}")
                crit_fail += 1
            else:
                print(f"  ✅ 试评估（Builtin.Helpfulness）成功：{res.get('label')} {res.get('value')}")
        except Exception as e:  # noqa: BLE001
            print(f"  ❌ 试评估调用失败：{type(e).__name__}: {str(e)[:300]}")
            crit_fail += 1
    print()
    if crit_fail >= 2 or (results and not any(r["c_spans"] for r in results)):
        verdict = "差距大"
        print("🟥 结论：差距大 —— 数据缺少评估必需的内容。")
        print("   建议：不要在现场硬改，切换到 B 版（mock agent 部署到 AgentCore Runtime）：打开手册 B3 章。")
        code = 2
    elif crit_fail == 1:
        verdict = "有缺口"
        print("🟧 结论：有 1 项必需内容缺失。按上面的 ❌ 项补埋点后重试；10 分钟内补不上就切换到 B 版。")
        code = 1
    elif opt_fail:
        verdict = "有缺口"
        print("🟨 结论：可以评估（必需项都满足），部分可选项缺失会限制个别评估器。继续第 6 章。")
        code = 0
    else:
        verdict = "通过"
        print("🟩 结论：通过 —— 数据满足 AgentCore Evaluations 的要求。继续第 6 章。")
        code = 0
    aws.save_state(f"telemetry-check-{run['run_id']}", {"verdict": verdict, "probe": probe,
                   "items": {k: sum(1 for r in results if r[k]) for k, _, _ in ITEMS}, "sessions": len(results)})
    return code


if __name__ == "__main__":
    sys.exit(main())
