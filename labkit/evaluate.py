"""Chapter 7/9: on-demand evaluation of one agent run with AgentCore Evaluations.

  python -m labkit.evaluate                 # evaluate the latest run
  python -m labkit.evaluate --run <run_id>  # a specific run (see .lab-state/runs.json)

For every session of the run: fetch its spans from CloudWatch, call the Evaluate API once
per evaluator (custom + built-in) with the golden labels as reference input, and save
results/eval-<run_id>.json (input to 32-compare.sh).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import aws, items, spans

BUILTINS = ["Builtin.Correctness", "Builtin.Helpfulness"]
LABEL_KEYS = {"label_important": "important", "label_urgent": "urgent", "label_decision": "decision"}
CN = {"important": "是否重要", "urgent": "是否紧急", "decision": "处理建议"}


def pick_run(run_id: str | None) -> dict:
    runs = aws.load_state("runs", default=[])
    if not runs:
        raise SystemExit("❌ 还没有任何运行记录：先跑黄金集（第 7.1 步）")
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
                "error": f"{type(e).__name__}: {str(e)[:300]}（可稍后用 ./31-run-eval.sh --run <run_id> 重试）"}


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


def summarize(sessions: list, target: float) -> dict:
    def mean(name, only_numeric=True, skip_labels=("Skipped", "不适用")):
        vals = [s["scores"][name]["value"] for s in sessions
                if name in s["scores"] and isinstance(s["scores"][name].get("value"), (int, float))
                and s["scores"][name].get("label") not in skip_labels]
        return (sum(vals) / len(vals), len(vals)) if vals else (None, 0)
    summ = {"sessions": len(sessions), "agent_errors": sum(1 for s in sessions if s.get("agent_error")),
            "target": target, "metrics": {}}
    names = sorted({n for s in sessions for n in s["scores"]})
    for n in names:
        m, k = mean(n)
        summ["metrics"][n] = {"mean": m, "n": k}
    summ["three_labels"] = {CN[v]: summ["metrics"].get(k, {}).get("mean") for k, v in LABEL_KEYS.items()}
    vals = [v for v in summ["three_labels"].values() if v is not None]
    summ["meets_target"] = bool(vals) and len(vals) == 3 and all(v >= target for v in vals)
    return summ


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
        raise SystemExit("❌ 还没有注册评估器：先运行 ./30-create-evaluators.sh")
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
    print(f"═══ 结果（{time.time()-t0:.0f}s）· 目标：三项各 ≥ {float(c['TARGET_ACCURACY']):.0%} ═══")
    for k, v in summ["three_labels"].items():
        flag = "—" if v is None else ("✅" if v >= float(c["TARGET_ACCURACY"]) else "❌")
        print(f"  {k}：{'—' if v is None else f'{v:.0%}'} {flag}")
    for n in ("label_accuracy", "reject_quality", "reject_quality_llm", *BUILTINS):
        m = summ["metrics"].get(n)
        if m and m["mean"] is not None:
            print(f"  {n}: 平均 {m['mean']:.2f}（{m['n']} 条）")
    print(f"  {'✅ 达标' if summ['meets_target'] else '❌ 未达标'} · 详细结果：{out.relative_to(c['LAB_ROOT'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
