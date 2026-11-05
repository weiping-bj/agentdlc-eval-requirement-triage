"""Run the agent over a set of feedback items and record one session per item.

  python -m labkit.batch --mode local   --source golden     # A path (run under opentelemetry-instrument)
  python -m labkit.batch --mode runtime --source golden     # B path (AgentCore Runtime)
  python -m labkit.batch --mode local --one FB-2026-10-001  # single item smoke test

Writes results/run-<path>-<version>-<timestamp>.jsonl and registers the run in
.lab-state/runs.json (used by 31-run-eval.sh and 32-compare.sh).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import aws, items


def _session_id(prefix: str, version: str, fid: str) -> str:
    # >= 33 chars (Runtime requirement); readable in the console
    return f"{prefix}-{version}-{fid}-{uuid.uuid4().hex[:12]}"[:200]


def _local_one(row: dict, version: str, model: str, sid: str) -> dict:
    from opentelemetry import baggage, context  # provided by strands / ADOT
    sys.path.insert(0, str(Path(aws.cfg()["LAB_ROOT"]) / "agent"))
    from s12agent.core import triage

    token = context.attach(baggage.set_baggage("session.id", sid))
    try:
        return triage(items.item_of(row), version=version, model_id=model, session_id=sid)
    finally:
        context.detach(token)


def _runtime_one(row: dict, arn: str, sid: str) -> dict:
    cli = aws.client("bedrock-agentcore")
    payload = json.dumps({"feedback": items.item_of(row)}, ensure_ascii=False).encode()
    for attempt in range(3):  # transient RuntimeClientError / throttling: retry the same session
        try:
            resp = cli.invoke_agent_runtime(agentRuntimeArn=arn, runtimeSessionId=sid, qualifier="DEFAULT",
                                            contentType="application/json", accept="application/json",
                                            payload=payload)
            break
        except Exception as e:  # noqa: BLE001
            transient = any(s in str(e) for s in ("RuntimeClientError", "Throttl", "ServiceUnavailable", "InternalServer", "timeout"))
            if attempt < 2 and transient:
                time.sleep(5 * (attempt + 1))
                continue
            raise
    body = resp["response"].read().decode("utf-8")
    data = json.loads(body)
    if isinstance(data, str):
        data = json.loads(data)
    return {"session_id": sid, "result": data.get("result", data), "raw": data.get("raw", "")}


def run(mode: str, source: str, version: str, limit: int | None, workers: int, one: str | None,
        label: str | None) -> Path:
    c = aws.cfg()
    rows = items.load(source)
    if one:
        rows = [r for r in rows if items.fid_of(r) == one] or [{"id": one, "content": one}]
    if limit:
        rows = rows[:limit]
    model = c["AGENT_MODEL_ID"]
    path_tag = "A" if mode == "local" else "B"
    arn = None
    if mode == "runtime":
        st = aws.load_state("runtime") or {}
        arn = st.get("agentRuntimeArn")
        if not arn:
            raise SystemExit("❌ 找不到 Runtime（.lab-state/runtime.json）。请先运行 ./20-B-deploy-runtime.sh")
        version = st.get("agentVersion", version)
        model = st.get("modelId", model)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = label or f"{path_tag}-{version}-{ts}"
    out_dir = Path(c["RESULTS_DIR"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"run-{run_id}.jsonl"
    prefix = c["LAB_NAME"]
    print(f"▶ 运行 {len(rows)} 条需求 · 路线 {path_tag} · 版本 {version} · 模型 {model} · 并发 {workers}")
    started = time.time()

    def work(row):
        fid = items.fid_of(row)
        sid = _session_id(prefix, version, fid or "x")
        t0 = time.time()
        rec = {"run_id": run_id, "feedback_id": fid, "session_id": sid, "version": version, "mode": mode,
               "model": model, "started_at": datetime.now(timezone.utc).isoformat()}
        try:
            res = _local_one(row, version, model, sid) if mode == "local" else _runtime_one(row, arn, sid)
            rec.update(result=res.get("result"), raw=res.get("raw", ""))
        except Exception as e:  # noqa: BLE001 - record and continue
            rec.update(result=None, error=f"{type(e).__name__}: {e}")
        rec["latency_ms"] = int((time.time() - t0) * 1000)
        return rec

    done = 0
    with out.open("w", encoding="utf-8") as fh, cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for rec in ex.map(work, rows):
            done += 1
            r = rec.get("result") or {}
            status = "❌ " + rec["error"][:200] if rec.get("error") else (
                f"重要={r.get('important')} 紧急={r.get('urgent')} 建议={r.get('decision')}")
            print(f"  [{done:>2}/{len(rows)}] {rec['feedback_id']:<16} {rec['latency_ms']/1000:5.1f}s  {status}")
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()

    if mode == "local":
        try:
            from opentelemetry import trace
            prov = trace.get_tracer_provider()
            if hasattr(prov, "force_flush"):
                prov.force_flush(30000)
        except Exception:  # noqa: BLE001
            pass

    runs = aws.load_state("runs", default=[])
    runs.append({"run_id": run_id, "file": str(out.relative_to(c["LAB_ROOT"])), "mode": mode, "version": version,
                 "model": model, "source": source, "count": len(rows), "created": ts,
                 "service_name": (c["LOCAL_SERVICE_NAME"] if mode == "local" else f"{c['RUNTIME_NAME']}.DEFAULT"),
                 "log_group": (c["LOCAL_LOG_GROUP"] if mode == "local"
                               else (aws.load_state("runtime") or {}).get("logGroup")),
                 "agentRuntimeArn": arn})
    aws.save_state("runs", runs)
    errs = sum(1 for _ in open(out, encoding="utf-8") if '"error"' in _)
    print(f"✅ 完成：{len(rows)} 条，用时 {time.time()-started:.0f}s，失败 {errs} 条 → {out.relative_to(c['LAB_ROOT'])}")
    print(f"   run_id = {run_id}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["local", "runtime"], required=True)
    ap.add_argument("--source", default="golden", help="golden | feedback | <jsonl path>")
    ap.add_argument("--version", default=os.environ.get("AGENT_VERSION", aws.cfg()["AGENT_VERSION"]))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--one", help="run a single feedback id")
    ap.add_argument("--label", help="custom run id")
    a = ap.parse_args(argv)
    run(a.mode, a.source, a.version, a.limit, a.workers, a.one, a.label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
