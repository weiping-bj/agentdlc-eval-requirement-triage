"""Regenerate the SYNTHETIC V2 sample used by tests/test_results_page.py and the sample results page.

    .venv/bin/python tests/fixtures/make_results_sample.py

Writes tests/fixtures/eval-sample-v2-synthetic.json and run-runtime-v2-20261105T170000Z.jsonl. Everything in them is
synthetic: the agent outputs and evaluator results are copied from eval-sample-v1.json (itself synthetic) with three
items changed by hand (FB-2610-005 / -007 fixed, FB-2610-001 regressed on 是否紧急); the tool calls are what the
agent's own V1 tools (agent/adlc_agent/tools.py) return locally for the item title, clipped like labkit.spans clips
them. No AWS call; needs the lab venv (strands) for the tools import.
"""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "agent")]

from adlc_agent import tools  # noqa: E402
from labkit import items, spans  # noqa: E402

FX = ROOT / "tests" / "fixtures"
RUN_ID = "runtime-v2-20261105T170000Z"
NOTE = ("合成样例（synthetic）：由 tests/fixtures/make_results_sample.py 从 eval-sample-v1.json 派生；"
        "Agent 输出和评估器结果是合成的，工具返回来自本地运行 agent/adlc_agent/tools.py 的 V1 工具")


def fix(s: dict, golden: dict) -> None:
    exp = items.expected_of(golden[s["feedback_id"]])
    s["agent_output"].update(important=exp["important"], urgent=exp["urgent"], decision=exp["decision"])
    for name, key in (("label_important", "important"), ("label_urgent", "urgent"), ("label_decision", "decision")):
        s["scores"][name] = {"value": 1.0, "label": "Pass", "explanation": f"{key} 对（期望 {exp[key]}，实际 {exp[key]}）",
                             "errorCode": None, "errorMessage": None}
    s["scores"]["label_accuracy"] = {"value": 1.0, "label": "Pass", "explanation": "3/3 项正确。", "errorCode": None,
                                     "errorMessage": None}
    if exp["decision"] == "拒绝":
        s["agent_output"].update(
            reasoning="能力目录把财务总账、凭证列为明确不做（超出 CRM 平台定位），集成平台有财务软件连接器。",
            reject_reason="财务总账与会计核算超出 CRM 平台定位（明确不做）；可通过集成平台的财务软件连接器对接现有财务系统。",
            customer_reply="感谢您提出业财一体化的需求。总账、凭证这类会计核算由专业财务系统负责更合适，CRM 不会内置；"
                           "您可以用集成平台的财务软件连接器把订单和回款同步到现有财务系统，需要的话我们可以安排实施顾问协助配置。")
        s["scores"]["reject_quality"] = {"value": 0.86, "label": "Pass", "errorCode": None, "errorMessage": None,
                                         "explanation": "拒绝话术评审 0.86：理由专业性 5，理解与礼貌 4，原因说明 4，替代方案 5，风险控制 4。红线：无。"}


def main() -> int:
    v1 = json.loads((FX / "eval-sample-v1.json").read_text(encoding="utf-8"))
    golden = {items.fid_of(r): r for r in items.read_jsonl(ROOT / "data" / "golden" / "golden_set.jsonl")}
    v2 = copy.deepcopy(v1)
    v2 = {"_synthetic": NOTE, **v2}
    v2["run"].update(run_id=RUN_ID, version="v2", created="20261105T170000Z", file=f"results/run-{RUN_ID}.jsonl")
    v2["evaluated_at"] = "2026-11-05T17:12:00+00:00"
    v2.pop("summary", None)
    runs = []
    for s in v2["sessions"]:
        fid = s["feedback_id"]
        s["session_id"] = s["session_id"].replace("-v1-", "-v2-")
        if fid in ("FB-2610-005", "FB-2610-007"):
            fix(s, golden)
        if fid == "FB-2610-001":
            s["agent_output"]["urgent"] = "是"
            s["scores"]["label_urgent"] = {"value": 0.0, "label": "Fail", "explanation": "是否紧急 错（期望 否，实际 是）",
                                           "errorCode": None, "errorMessage": None}
            s["scores"]["label_accuracy"] = {"value": 0.6667, "label": "Fail", "explanation": "2/3 项正确。是否紧急 错（期望 否，实际 是）",
                                             "errorCode": None, "errorMessage": None}
        title = items.item_of(golden[fid])["title"] if fid in golden else fid
        if fid == "FB-2610-014":
            s["tool_calls"] = []
        else:
            s["tool_calls"] = [
                {"name": n, "input": json.dumps({"query": title}, ensure_ascii=False),
                 "output": spans._clip(str(fn(title)), 900), "status": "success", "duration_ms": d, "call_id": f"tooluse_{fid}_{i}"}
                for i, (n, fn, d) in enumerate((("search_capabilities", tools.search_capabilities, 6.2),
                                                ("find_similar_requests", tools.find_similar_requests, 4.8)))]
        runs.append({"run_id": RUN_ID, "feedback_id": fid, "session_id": s["session_id"], "version": "v2", "mode": "runtime",
                     "model": v2["run"]["model"], "result": s["agent_output"],
                     "raw": "```json\n" + json.dumps(s["agent_output"], ensure_ascii=False) + "\n```",
                     "latency_ms": s.get("latency_ms")})
    (FX / "eval-sample-v2-synthetic.json").write_text(json.dumps(v2, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (FX / f"run-{RUN_ID}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in runs), encoding="utf-8")
    print("wrote", FX / "eval-sample-v2-synthetic.json", FX / f"run-{RUN_ID}.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
