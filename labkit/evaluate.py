"""Chapters 4/5: on-demand evaluation of one agent run with AgentCore Evaluations.

  python -m labkit.evaluate                 # evaluate the latest run
  python -m labkit.evaluate --run <run_id>  # a specific run (see .lab-state/runs.json)

For every session of the run: fetch its spans from CloudWatch, call the Evaluate API once
per evaluator (custom + built-in) with the golden labels as reference input, and save
results/eval-<run_id>.json (input to 45-results-page.sh and 51-compare.sh; each session also records the tool calls
of its trace, read from the same spans, for the results page). The verdict follows the three pillars
(evaluators/shared/pillars.py): no red line (误拒 / 话术红线, one-vote veto) AND every gate met;
observation metrics are printed for the trend only.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import aws, compare, items, spans

BUILTINS = ["Builtin.Correctness", "Builtin.Helpfulness"]
LABEL_KEYS = {"label_important": "important", "label_urgent": "urgent", "label_decision": "decision"}
CN = {"important": "是否重要", "urgent": "是否紧急", "decision": "处理建议"}


def pick_run(run_id: str | None) -> dict:
    runs = aws.load_state("runs", default=[])
    if not runs:
        raise SystemExit("❌ 还没有任何运行记录：先跑黄金集（第 4.4 步 ./42-run-golden.sh）")
    if run_id:
        for r in runs:
            if r["run_id"] == run_id:
                return r
        raise SystemExit(f"❌ 找不到 run {run_id}；可选：{[r['run_id'] for r in runs]}")
    return runs[-1]


def expected_text(row: dict) -> str:
    """Golden labels as the reference input (label evaluators parse this JSON)."""
    exp = {"feedback_id": items.fid_of(row), **items.expected_of(row)}
    for k in ("golden_id", "acceptable_decisions", "can_auto_reject"):
        if k in row:
            exp[k] = row[k]
    return json.dumps(exp, ensure_ascii=False)


def evaluate_session(rec: dict, golden: dict, evaluators: list, log_group: str, start: int) -> dict:
    try:
        return _evaluate_session(rec, golden, evaluators, log_group, start)
    except Exception as e:  # noqa: BLE001 - e.g. Logs Insights concurrency limit: keep the other sessions
        return {"feedback_id": rec.get("feedback_id"), "session_id": rec.get("session_id"),
                "agent_output": rec.get("result"), "agent_error": rec.get("error"), "scores": {},
                "error": f"{type(e).__name__}: {str(e)[:300]}（可稍后用 ./43-run-eval.sh --run <run_id> 重试）"}


def _evaluate_session(rec: dict, golden: dict, evaluators: list, log_group: str, start: int) -> dict:
    sid, fid = rec["session_id"], rec["feedback_id"]
    out = {"feedback_id": fid, "session_id": sid, "agent_output": rec.get("result"), "agent_error": rec.get("error"),
           "latency_ms": rec.get("latency_ms"), "scores": {}}
    if rec.get("error"):
        return out
    docs = []
    for attempt in range(8):  # spans become queryable 1-3 minutes after the run
        docs = spans.session_docs(sid, log_group, start)
        tids = spans.trace_ids(docs)
        if tids:
            break
        time.sleep(20)
    tids = spans.trace_ids(docs)
    if not tids:
        out["error"] = "no spans found in CloudWatch for this session (wait 2-3 min and retry)"
        return out
    tid = tids[-1]
    out["trace_id"] = tid
    out["span_docs"] = len(docs)
    try:  # display only (results page): the tool calls of the evaluated trace, from the docs already fetched
        out["tool_calls"] = spans.tool_calls(docs, trace_id=tid)
    except Exception:  # noqa: BLE001 - never fail an evaluation over it
        pass
    ref = None
    if fid in golden:
        ref = [{"context": {"spanContext": {"sessionId": sid, "traceId": tid}},
                "expectedResponse": {"text": expected_text(golden[fid])}}]
    cli = aws.client("bedrock-agentcore")
    for name, eid in evaluators:
        kw = {"evaluatorId": eid, "evaluationInput": {"sessionSpans": docs}, "evaluationTarget": {"traceIds": [tid]}}
        if ref:
            kw["evaluationReferenceInputs"] = ref
        for attempt in range(4):
            try:
                r = cli.evaluate(**kw)
                res = (r.get("evaluationResults") or [{}])[-1]
                out["scores"][name] = {k: res.get(k) for k in ("value", "label", "explanation", "errorCode", "errorMessage")}
                break
            except Exception as e:  # noqa: BLE001
                msg = f"{type(e).__name__}: {str(e)[:300]}"
                if attempt < 3 and any(s in msg for s in ("Throttl", "TooMany", "ServiceUnavailable", "Internal")):
                    time.sleep(5 * (attempt + 1))
                    continue
                out["scores"][name] = {"errorCode": "CLIENT_ERROR", "errorMessage": msg}
                break
    return out


def summarize(sessions: list, target: float, fb: dict | None = None) -> dict:
    def mean(name, only_numeric=True, skip_labels=("Skipped", "不适用")):
        vals = [s["scores"][name]["value"] for s in sessions
                if name in s["scores"] and isinstance(s["scores"][name].get("value"), (int, float))
                and s["scores"][name].get("label") not in skip_labels]
        return (sum(vals) / len(vals), len(vals)) if vals else (None, 0)
    summ = {"sessions": len(sessions), "agent_errors": sum(1 for s in sessions if s.get("agent_error")),
            "eval_errors": sum(1 for s in sessions if s.get("error")), "target": target, "metrics": {}}
    names = sorted({n for s in sessions for n in s["scores"]})
    for n in names:
        m, k = mean(n)
        summ["metrics"][n] = {"mean": m, "n": k}
    ev = compare.EvalRun({"sessions": sessions}, Path("eval-summary.json"))
    p = compare.pillars_of(ev, compare.feedback_index() if fb is None else fb, target)
    p.pop("_items", None)
    summ["pillars"] = p
    summ["verdict"] = p["verdict"]
    # gate accuracies (a misrejected item is wrong in every label), falling back to the evaluator means
    gates = {g["key"]: g["actual"] for g in p["gates"]}
    summ["three_labels"] = {CN[v]: (gates.get(v) if gates.get(v) is not None else summ["metrics"].get(k, {}).get("mean"))
                            for k, v in LABEL_KEYS.items()}
    # an incomplete evaluation can never pass: failed sessions are missing from the means
    summ["meets_target"] = bool(p["passed"]) and summ["eval_errors"] == 0
    return summ


def _p(v) -> str:
    return "—" if v is None else f"{v:.0%}"


def summary_lines(summ: dict) -> list:
    """Terminal summary: 红线 → 门禁 → 观测, then the verdict (also used by tests and the docs)."""
    p = summ["pillars"]
    rl, obs = p["red_line"], p["observations"]
    by = rl["by_kind"]
    L = [f"  红线（一票否决，要求 0 条）：{rl['count']} 条 {compare.pillars.red_line_mark(p)}"
         f"  误拒 {by.get('misreject', 0)} · 话术红线 {by.get('reject_red_flag', 0)}"]
    for x in rl["items"][:10]:
        for k in x["kinds"]:
            name = "误拒" if k["kind"] == "misreject" else "话术红线"
            L.append(f"    ⛔ {compare.pillars.plain(x['feedback_id'], 40)}（{x.get('golden_id') or '-'}）{name}："
                     f"{compare.pillars.plain(k['detail'], 60)}")
    if len(rl["items"]) > 10:
        L.append(f"    …… 另有 {len(rl['items']) - 10} 条，见对比页或 eval JSON")
    L.append(f"  门禁（各 ≥ {p['target']:.0%}）：")
    for g in p["gates"]:
        flag = {"met": "✅", "failed": "❌"}.get(g["status"], "—")
        extra = f"（{g['ok']}/{g['n']} 条 Pass）" if g["key"] == "reject_quality" and g["n"] else ""
        st = "" if g["status"] in ("met", "failed") else f" {compare.pillars.GATE_STATUS_CN[g['status']]}"
        L.append(f"    {g['name']}：{_p(g['actual'])}{extra} {flag}{st}")
    r = obs["reject"]
    L.append(f"  观测（只看趋势，不影响结论）：整条通过率 {_p(obs['all_correct_rate'])} · "
             f"格式错误 {obs['format_error']['n']} 条 · 话术均分 {'—' if r['mean'] is None else format(r['mean'], '.2f')}"
             f"（{r['n']} 条）")
    for n in ("label_accuracy", "reject_quality", "reject_quality_llm", *BUILTINS):
        m = summ["metrics"].get(n)
        if m and m["mean"] is not None:
            L.append(f"    {n}: 平均 {m['mean']:.2f}（{m['n']} 条）")
    return L


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run")
    ap.add_argument("--no-builtin", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    c = aws.cfg()
    run = pick_run(a.run)
    ev_state = aws.load_state("evaluators") or {}
    if not ev_state:
        raise SystemExit("❌ 还没有注册评估器：先运行 ./40-create-evaluators.sh")
    evaluators = [(k, v["evaluatorId"]) for k, v in ev_state.items() if not k.startswith("_")]
    if not a.no_builtin:
        evaluators += [(b, b) for b in BUILTINS]
    recs = [json.loads(x) for x in (Path(c["LAB_ROOT"]) / run["file"]).read_text(encoding="utf-8").splitlines() if x.strip()]
    golden = {items.fid_of(r): r for r in items.load("golden")}
    created = datetime.strptime(run["created"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    start = int(created.timestamp()) - 600
    print(f"▶ 评估 run {run['run_id']}：{len(recs)} 个 session × {len(evaluators)} 个评估器")
    print("  评估器：" + ", ".join(n for n, _ in evaluators))
    t0 = time.time()
    results = []
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(evaluate_session, r, golden, evaluators, run.get("log_group"), start) for r in recs]
        for i, f in enumerate(cf.as_completed(futs), 1):
            s = f.result()
            results.append(s)
            sc = s["scores"]
            def fmt(n):
                v = sc.get(n, {})
                return v.get("label") or v.get("errorCode") or "-"
            print(f"  [{i:>2}/{len(recs)}] {s['feedback_id']:<14} 三项={fmt('label_accuracy'):<8} "
                  f"重要={fmt('label_important'):<5} 紧急={fmt('label_urgent'):<5} 建议={fmt('label_decision'):<5} "
                  f"话术={fmt('reject_quality'):<8} {s.get('error', '')}")
    order = {items.fid_of(r): i for i, r in enumerate(recs)}
    results.sort(key=lambda s: order.get(s["feedback_id"], 0))
    summ = summarize(results, float(c["TARGET_ACCURACY"]))
    report = {"run": run, "evaluated_at": datetime.now(timezone.utc).isoformat(), "region": c["AWS_REGION"],
              "evaluators": dict(evaluators), "summary": summ, "sessions": results}
    out = Path(c["RESULTS_DIR"]) / f"eval-{run['run_id']}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    evals = aws.load_state("evals", default=[])
    evals.append({"run_id": run["run_id"], "file": str(out.relative_to(c["LAB_ROOT"])), "version": run["version"],
                  "mode": run["mode"], "evaluated_at": report["evaluated_at"]})
    aws.save_state("evals", evals)
    print()
    print(f"═══ 结果（{time.time()-t0:.0f}s）· 三大支柱：红线 0 条 + 门禁各 ≥ {float(c['TARGET_ACCURACY']):.0%} 才通过 ═══")
    for line in summary_lines(summ):
        print(line)
    if summ["eval_errors"]:
        print(f"  ⚠️  {summ['eval_errors']} 个 session 评估失败（见上方错误），结论不完整：稍后运行 ./43-run-eval.sh --run {run['run_id']} 重试")
    print(f"  {compare.pillars.verdict_line(summ['pillars'])} · 详细结果：{out.relative_to(c['LAB_ROOT'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
