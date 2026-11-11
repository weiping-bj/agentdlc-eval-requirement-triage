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
    # the red-line item is wrong in every label (9/10): the gates still pass, the red line alone fails the run
    assert all(v == 0.9 for v in s["three_labels"].values())
    assert all(g["status"] in ("met", "failed") and (g["key"] == "reject_quality" or g["status"] == "met")
               for g in s["pillars"]["gates"])
    assert s["meets_target"] is False and s["verdict"] == "fail"
    assert s["pillars"]["red_line"]["count"] == 1
    lines = "\n".join(evaluate.summary_lines(s))
    assert "红线（一票否决，要求 0 条）：1 条 ❌" in lines and "F9" in lines and "门禁" in lines and "观测" in lines


def test_legacy_results_are_read_under_the_new_rule():
    """Old Lambdas: label_important Pass for a misrejected item, reject red flag at 0.4."""
    mis = _session("M", "拒绝", {"value": 0.0, "label": "Fail",
                                 "explanation": "需求 M（-）拒绝话术评审 0.0：理由专业性 5。红线：拒绝了不应自动拒绝的需求（黄金集标注）。"},
                   labels={"label_decision": {"value": 0.0, "label": "CriticalFail"}},
                   la={"value": 0.6667, "label": "CriticalFail"})
    ev = compare.EvalRun({"sessions": [mis, _session("F", "拒绝", FLAG_REJ), _session("G")]}, Path("eval-x.json"))
    p = compare.pillars_of(ev, {}, 0.7)
    assert p["red_line"]["count"] == 2 and p["red_line"]["by_kind"] == {"misreject": 1, "reject_red_flag": 1}
    gates = {g["key"]: g for g in p["gates"]}
    # both red-line items count wrong although label_important said Pass for them
    assert gates["important"]["actual"] == round(1 / 3, 4)
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
    fb = compare.feedback_index()
    crit = {s["feedback_id"] for s in v0["sessions"] if (s.get("scores") or {}).get("label_accuracy", {}).get("label") == "CriticalFail"}
    crit |= {s["feedback_id"] for s in v0["sessions"] if (s.get("agent_output") or {}).get("decision") == "拒绝"
             and fb.get(s["feedback_id"], {}).get("can_auto_reject") is False}
    p = compare.pillars_of(compare.EvalRun(v0, FX / "eval-sample-v0.json"), fb, 0.7)
    assert {x["feedback_id"] for x in p["red_line"]["items"]} == crit and p["red_line"]["by_kind"]["misreject"] == len(crit)
    errors = [s for s in v0["sessions"] if s.get("agent_error")]
    imp = [s for s in v0["sessions"] if isinstance(((s.get("scores") or {}).get("label_important") or {}).get("value"), (int, float))]
    ok = sum(1 for s in imp if s["scores"]["label_important"]["value"] == 1 and s["feedback_id"] not in crit)
    g = next(g for g in p["gates"] if g["key"] == "important")
    assert errors and g["n"] == len(imp) + len(errors)  # an agent error counts as wrong, like batch_score
    assert g["actual"] == round(ok / (len(imp) + len(errors)), 4)


# ------------------------------------------------------------------ review round 1
import pytest  # noqa: E402


@pytest.mark.parametrize("reject", [None, {"label": "Skipped"}, {"label": "不适用"}, {"label": "FormatError", "value": 0.0},
                                    {"errorCode": "S12_REJECT_JUDGE_FAILED", "errorMessage": "x"}])
@pytest.mark.parametrize("decision", ["拒绝", "建议拒绝", "Reject"])
def test_a_rejection_without_a_judged_verdict_never_passes(reject, decision):
    s = _session("R", decision)
    if reject is None:
        s["scores"].pop("reject_quality")
    else:
        s["scores"]["reject_quality"] = reject
    out = evaluate.summarize([_session(f"F{i}") for i in range(9)] + [s], 0.7, fb={})
    assert out["verdict"] == "incomplete" and not out["meets_target"]
    assert "⚠️" in evaluate.summary_lines(out)[0]  # the red-line headline does not claim ✅ either


def test_misreject_is_found_from_the_golden_set_when_evaluators_said_nothing():
    s = _session("R", "拒绝", reject={"label": "Skipped"},
                 labels={k: {"label": "Skipped", "value": None} for k in LABELS}, la={"label": "Skipped"})
    p = compare.pillars_of(compare.EvalRun({"sessions": [s, _session("G")]}, Path("e.json")), {"R": {"can_auto_reject": False}}, 0.7)
    assert p["verdict"] == "fail" and p["red_line"]["by_kind"]["misreject"] == 1


def test_graded_scheme_gate_comes_from_label_accuracy_when_no_level_evaluator_ran():
    s = _session("L", la={"value": 0.5, "label": "Fail", "explanation": "需求 L（-）：1/2 项正确。优先级 错（期望 P1，实际 P0）；处理建议 对（期望 迭代，实际 迭代）。"})
    p = compare.pillars_of(compare.EvalRun({"sessions": [s]}, Path("e.json")), {}, 0.7)
    lv = next(g for g in p["gates"] if g["key"] == "level")
    assert lv["actual"] == 0.0 and lv["status"] == "failed" and p["verdict"] == "fail"


def test_label_accuracy_error_alone_does_not_make_the_verdict_incomplete():
    s = _session("F", la={"errorCode": "CLIENT_ERROR", "errorMessage": "x"})
    assert compare.pillars_of(compare.EvalRun({"sessions": [s]}, Path("e.json")), {}, 0.7)["verdict"] == "pass"


def test_batch_score_and_evaluate_agree_on_the_same_run(tmp_path):
    """Cross-tool: batch_score on a run file vs labkit.evaluate / compare on eval sessions built from it."""
    from s12_cross import build_pair  # tests/s12_cross.py
    run, ev, rej = build_pair(tmp_path)
    import subprocess
    oj = tmp_path / "bs.json"
    subprocess.run([sys.executable, str(ROOT / "evaluators/s12_label_accuracy/batch_score.py"), "--results", str(run),
                    "--reject-eval", str(rej), "--out-json", str(oj)], check=True, capture_output=True)
    b = json.loads(oj.read_text(encoding="utf-8"))["pillars"]
    e = evaluate.summarize(json.loads(ev.read_text(encoding="utf-8"))["sessions"], 0.7)["pillars"]
    assert b["verdict"] == e["verdict"]
    assert b["red_line"]["by_kind"] == e["red_line"]["by_kind"]
    assert [(g["key"], g["actual"], g["n"]) for g in b["gates"]] == [(g["key"], g["actual"], g["n"]) for g in e["gates"]]
    assert b["observations"]["all_correct_rate"] == e["observations"]["all_correct_rate"]
