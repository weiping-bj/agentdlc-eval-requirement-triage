"""Build one golden run twice: as a run JSONL (batch_score input) and as the eval JSON the evaluators would write
for it (labkit.evaluate / compare input), with a misreject, a judge red flag and agent errors in it."""
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LA = ROOT / "evaluators" / "s12_label_accuracy"


def _label_scoring():
    for m in ("label_scoring", "golden_store", "span_adapter", "pillars"):
        sys.modules.pop(m, None)
    sys.path.insert(0, str(LA))
    try:
        return importlib.import_module("label_scoring"), importlib.import_module("span_adapter")
    finally:
        sys.path.remove(str(LA))
        for m in ("label_scoring", "golden_store", "span_adapter", "pillars"):
            sys.modules.pop(m, None)


def build_pair(tmp: Path):
    ls, sa = _label_scoring()
    rows = [json.loads(x) for x in (ROOT / "data/golden/golden_set.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    misreject = next(r for r in rows if r["can_auto_reject"] is False)["feedback_id"]
    flagged = next(r for r in rows if r["expected"]["decision"] == "拒绝")["feedback_id"]
    errors = {r["feedback_id"] for r in rows[-3:]}
    run, sessions = [], []
    for r in rows:
        fid = r["feedback_id"]
        if fid in errors:
            run.append({"feedback_id": fid, "result": None, "error": "ReadTimeoutError"})
            sessions.append({"feedback_id": fid, "agent_output": None, "agent_error": "ReadTimeoutError", "scores": {}})
            continue
        out = {"feedback_id": fid, **{k: r["expected"][k] for k in ("important", "urgent", "decision")}, "level": None,
               "reasoning": "x", "reject_reason": "", "customer_reply": ""}
        if fid == misreject:
            out["decision"] = "拒绝"
        run.append({"feedback_id": fid, "result": out})
        gold = ls.gold_from_golden_item(r)
        pred = sa.normalize_output(out)
        sc = {}
        for f in ("important", "urgent", "decision"):
            s = ls.score_record(pred, gold, "binary", 0, f)
            sc[f"label_{f}"] = {"value": s["value"], "label": s["label"], "explanation": ls.explain(s, pred)}
        s = ls.score_record(pred, gold, "binary", 0)
        sc["label_accuracy"] = {"value": s["value"], "label": s["label"], "explanation": ls.explain(s, pred)}
        if out["decision"] != "拒绝":
            sc["reject_quality"] = {"value": None, "label": "不适用", "explanation": "不是拒绝"}
        elif fid == flagged:
            sc["reject_quality"] = {"value": 0.4, "label": "Fail",
                                    "explanation": f"需求 {fid}（-）拒绝话术评审 0.4：理由专业性 4。红线：编造了配置路径。"}
        else:
            sc["reject_quality"] = {"value": 0.9, "label": "Pass", "explanation": f"需求 {fid}（-）拒绝话术评审 0.9：理由专业性 5。"}
        sessions.append({"feedback_id": fid, "agent_output": out, "scores": sc})
    run_p = tmp / "run-X-v1-cross.jsonl"
    run_p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in run) + "\n", encoding="utf-8")
    ev_p = tmp / "eval-X-v1-cross.json"
    ev_p.write_text(json.dumps({"run": {"file": f"results/{run_p.name}"}, "sessions": sessions}, ensure_ascii=False),
                    encoding="utf-8")
    return run_p, ev_p, ev_p
