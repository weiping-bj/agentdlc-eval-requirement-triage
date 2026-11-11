"""Three pillars in labkit: evaluate summary verdict and the V0 vs V1 compare page.

Standard library + pytest only; no AWS calls.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from labkit import compare, evaluate  # noqa: E402

FX = ROOT / "tests" / "fixtures"
LABELS = ("label_important", "label_urgent", "label_decision")


def _pass(v=1.0):
    return {"value": v, "label": "Pass" if v == 1.0 else "Fail", "explanation": "x"}


def _session(fid, decision="迭代", reject=None, labels=None, la=None):
    sc = {k: _pass() for k in LABELS}
    sc.update(labels or {})
    sc["label_accuracy"] = la or {"value": 1.0, "label": "Pass", "explanation": "x"}
    sc["reject_quality"] = reject or {"value": None, "label": "不适用", "explanation": "处理建议不是拒绝"}
    return {"feedback_id": fid, "agent_output": {"feedback_id": fid, "decision": decision}, "scores": sc}


GOOD_REJ = {"value": 0.9, "label": "Pass", "explanation": "需求 F（-）拒绝话术评审 0.9：理由专业性 5，理解与礼貌 5。"}
# written by the old evaluator: red flag capped at 0.4, label Fail
FLAG_REJ = {"value": 0.4, "label": "Fail",
            "explanation": "需求 F（-）拒绝话术评审 0.4：理由专业性 4，理解与礼貌 5。红线：话术编造配置路径。点评：删去路径"}


def test_summary_red_line_blocks_the_verdict_although_all_gates_pass():
    sessions = [_session(f"F{i}") for i in range(9)] + [_session("F9", "拒绝", GOOD_REJ)]
    ok = evaluate.summarize(sessions, 0.7, fb={})
    assert ok["meets_target"] and ok["verdict"] == "pass"
    sessions[-1] = _session("F9", "拒绝", FLAG_REJ)
    s = evaluate.summarize(sessions, 0.7, fb={})
    assert all(v == 1.0 for v in s["three_labels"].values())
    assert s["meets_target"] is False and s["verdict"] == "fail"
    assert s["pillars"]["red_line"]["count"] == 1
    lines = "\n".join(evaluate.summary_lines(s))
    assert "红线（一票否决，要求 0 条）：1 条 ❌" in lines and "F9" in lines and "门禁" in lines and "观测" in lines


def test_legacy_results_are_read_under_the_new_rule():
    """Old Lambdas: label_important Pass for a misrejected item, reject red flag at 0.4."""
    mis = _session("M", "拒绝", {"value": 0.0, "label": "Fail", "explanation": "红线：拒绝了不应自动拒绝的需求（黄金集标注）。"},
                   labels={"label_decision": {"value": 0.0, "label": "CriticalFail"}},
                   la={"value": 0.6667, "label": "CriticalFail"})
    ev = compare.EvalRun({"sessions": [mis, _session("F", "拒绝", FLAG_REJ), _session("G")]}, Path("eval-x.json"))
    p = compare.pillars_of(ev, {}, 0.7)
    assert p["red_line"]["count"] == 2 and p["red_line"]["by_kind"] == {"misreject": 1, "reject_red_flag": 1}
    gates = {g["key"]: g for g in p["gates"]}
    assert gates["important"]["actual"] == round(2 / 3, 4)  # misreject counts wrong although label_important said Pass
    assert gates["reject_quality"]["ok"] == 0
    assert p["_items"]["F"]["reject"]["value"] == 0.2   # re-read: red flag -> lowest failing grade


def test_compare_page_shows_three_pillars_with_red_line_markers(tmp_path):
    before = {"run": {"run_id": "R0", "version": "v0", "mode": "local"},
              "sessions": [_session("F1"), _session("F2", "拒绝", GOOD_REJ)]}
    after = {"run": {"run_id": "R1", "version": "v1", "mode": "local"},
             "sessions": [_session("F1"), _session("F2", "拒绝", FLAG_REJ)]}
    pb, pa, out = tmp_path / "eval-b.json", tmp_path / "eval-a.json", tmp_path / "c.html"
    pb.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")
    pa.write_text(json.dumps(after, ensure_ascii=False), encoding="utf-8")
    assert compare.main(["--before", str(pb), "--after", str(pa), "--out", str(out)]) == 0
    page = out.read_text(encoding="utf-8")
    for s in ("三大支柱：红线 · 门禁 · 观测", "1. 红线（一票否决）", "2. 门禁（全部达标才通过）", "3. 观测（只看趋势，不影响结论）",
              "V1（优化后）</b>：❌ 不通过：红线 1 条（一票否决）", "V0（优化前）</b>：✅ 通过",
              "⛔ V1 话术红线", "话术编造配置路径", "拒绝话术通过率", 'id="redlines"', 'data-f="redline"'):
        assert s in page, s
    # F2 had all labels right before and after, but gained a red line: that is a regression, not "kept"
    assert 'data-tags="regressed redline"' in page


def test_sample_fixture_pillars_match_an_independent_count():
    v0 = json.loads((FX / "eval-sample-v0.json").read_text(encoding="utf-8"))
    crit = {s["feedback_id"] for s in v0["sessions"] if (s.get("scores") or {}).get("label_accuracy", {}).get("label") == "CriticalFail"}
    p = compare.pillars_of(compare.EvalRun(v0, FX / "eval-sample-v0.json"), compare.feedback_index(), 0.7)
    assert {x["feedback_id"] for x in p["red_line"]["items"]} >= crit and p["red_line"]["by_kind"]["misreject"] == len(crit)
    imp = [s for s in v0["sessions"] if "label_important" in (s.get("scores") or {})
           and isinstance(s["scores"]["label_important"].get("value"), (int, float))]
    ok = sum(1 for s in imp if s["scores"]["label_important"]["value"] == 1 and s["feedback_id"] not in crit)
    assert next(g for g in p["gates"] if g["key"] == "important")["actual"] == round(ok / len(imp), 4)
