"""Three pillars (红线 / 门禁 / 观测): whole-item red-line veto in the evaluators and the batch verdict."""
import json
import os

import pytest

from s12_eval_helpers import EV, event, load_pkg, output_from_gold, triage_span

la = load_pkg("s12_label_accuracy")
rq = load_pkg("s12_reject_quality")
bs = load_pkg("s12_label_accuracy", "batch_score")


class _Lazy:
    """pillars.py imported on first use (so each test fails on its own assertion when the module is missing)."""

    def __getattr__(self, name):
        return getattr(load_pkg("s12_label_accuracy", "pillars"), name)


pillars = _Lazy()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("S12_SCORE_FIELD", "S12_LABEL_SCHEME", "S12_LEVEL_TOLERANCE", "S12_GOLDEN_PATH"):
        monkeypatch.delenv(k, raising=False)


def g(rows, gid):
    return next(r for r in rows if r["golden_id"] == gid)


def non_rejectable(rows):
    return next(r for r in rows if r["can_auto_reject"] is False)


# ------------------------------------------------------------------ 红线 1: misreject fails the whole item
def test_misreject_fails_whole_item_even_when_other_labels_are_right(golden_rows):
    row = non_rejectable(golden_rows)
    out = output_from_gold(row, decision="拒绝")  # important / urgent right, decision is the misreject
    r = la.handler(event([triage_span(out)]))
    assert r["label"] == "CriticalFail" and r["value"] == 0.0
    assert "红线一票否决" in r["explanation"] and "原本 2/3" in r["explanation"]


@pytest.mark.parametrize("field", ["important", "urgent", "decision"])
def test_single_label_evaluators_also_veto_a_misreject(golden_rows, field):
    out = output_from_gold(non_rejectable(golden_rows), decision="拒绝")
    r = la.handler(event([triage_span(out)], name=f"s12lab_ab12_label_{field}"))
    assert r["label"] == "CriticalFail" and r["value"] == 0.0


def test_score_record_keeps_raw_correctness_for_diagnosis(golden_rows):
    ls = load_pkg("s12_label_accuracy", "label_scoring")
    gold = ls.gold_from_golden_item(non_rejectable(golden_rows))
    pred = dict(output_from_gold(non_rejectable(golden_rows), decision="拒绝"))
    s = ls.score_record(pred, gold)
    assert s["n_correct"] == 0 and s["n_correct_raw"] == 2 and s["value"] == 0.0
    assert s["fields"]["important"]["raw_correct"] and not s["fields"]["important"]["correct"]


# ------------------------------------------------------------------ 红线 2: judge red flag -> Fail, lowest failing grade
FLAGGED = json.dumps({"scores": {"A": 5, "B": 5, "C": 5, "D": 5, "E": 5}, "red_flags": ["编造了不存在的配置路径"],
                      "comment": "路径无依据", "missing_points": [], "covered_points": []}, ensure_ascii=False)


class _Fake:
    used_model = "m"

    def __init__(self, text):
        self.text = text

    def complete(self, system, prompt):
        return self.text


def test_red_flag_makes_the_item_fail_with_the_lowest_failing_grade(golden_rows):
    r = rq.evaluate(event([triage_span(output_from_gold(g(golden_rows, "G02")))]), client=_Fake(FLAGGED))
    assert r["label"] == "Fail" and r["value"] == rq.judge.rubric.RED_LINE_VALUE == 0.2
    assert pillars.red_flags_from_text(r["explanation"]) == ["编造了不存在的配置路径"]


def test_lowest_failing_grade_is_shared_and_zero_stays_not_applicable():
    rubric = rq.judge.rubric
    grades = sorted(v for v, _label, _d in rubric.GRADES)
    assert rubric.RED_LINE_VALUE == grades[0] == 0.2 and rubric.NOT_APPLICABLE[0] == 0.0
    assert rubric.NOT_APPLICABLE[1] == "不适用"
    assert pillars.REJECT_RED_VALUE == rubric.RED_LINE_VALUE and pillars.REJECT_PASS == rubric.PASS_THRESHOLD
    assert pillars.MISREJECT_FLAG == rq.judge.MISREJECT_FLAG
    assert [n for _k, n in pillars.JUDGE_DIMS] == [n for _k, n, _d in rubric.DIMENSIONS]


def test_native_judge_config_puts_every_red_flag_at_the_lowest_grade():
    cfg = json.load(open(os.path.join(EV, "llm_judge_config", "s12_reject_quality.json"), encoding="utf-8"))["llmAsAJudge"]
    ins = cfg["instructions"]
    assert "一律给“不合格”（0.2）" in ins and "最高只能给“较差”" not in ins
    scale = {x["value"]: x for x in cfg["ratingScale"]["numerical"]}
    assert "红线" in scale[0.2]["definition"] and "没有触发红线" in scale[0.4]["definition"]
    assert scale[0.0]["label"] == "不适用"


# ------------------------------------------------------------------ batch verdict
def _write(tmp_path, name, rows):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return str(p)


def _reject_eval(tmp_path, golden_rows, outputs, flag_fid=None):
    sessions = []
    for o in outputs:
        if o["decision"] != "拒绝":
            sc = {"label": "不适用", "value": None, "explanation": "处理建议不是拒绝"}
        elif o["feedback_id"] == flag_fid:
            sc = {"label": "Fail", "value": 0.4, "explanation": f"需求 {o['feedback_id']}（-）拒绝话术评审 0.4：理由专业性 4。"
                                                                 "红线：承诺了上线时间。点评：x"}
        else:
            sc = {"label": "Pass", "value": 0.9, "explanation": f"需求 {o['feedback_id']}（-）拒绝话术评审 0.9：理由专业性 5。"}
        sessions.append({"feedback_id": o["feedback_id"], "scores": {"reject_quality": sc}})
    p = tmp_path / "eval.json"
    p.write_text(json.dumps({"sessions": sessions}, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _run(tmp_path, golden_rows, outputs, flag_fid=None, extra=()):
    oj = tmp_path / "o.json"
    rc = bs.main(["--results", _write(tmp_path, "r.jsonl", outputs), "--reject-eval",
                  _reject_eval(tmp_path, golden_rows, outputs, flag_fid), "--out-json", str(oj), "--target", "0.7",
                  "--fail-under", *extra])
    return rc, json.loads(oj.read_text(encoding="utf-8"))


def test_perfect_run_with_judged_rejections_passes(tmp_path, golden_rows):
    rc, res = _run(tmp_path, golden_rows, [output_from_gold(r) for r in golden_rows])
    assert rc == 0 and res["passed"] and res["verdict"] == "pass"
    assert res["pillars"]["red_line"]["count"] == 0
    assert all(gt["status"] in ("met", "not_applicable") for gt in res["pillars"]["gates"])


def test_one_misreject_fails_the_run_although_every_accuracy_is_above_target(tmp_path, golden_rows):
    row = non_rejectable(golden_rows)
    outs = [output_from_gold(r, decision="拒绝") if r is row else output_from_gold(r) for r in golden_rows]
    rc, res = _run(tmp_path, golden_rows, outs)
    assert all(v >= 0.7 for v in res["accuracy"].values())
    assert rc == 1 and not res["passed"] and res["verdict"] == "fail"
    red = res["pillars"]["red_line"]
    assert red["count"] == 1 and red["items"][0]["feedback_id"] == row["feedback_id"]
    assert red["items"][0]["kinds"][0]["kind"] == "misreject"
    # whole-item failure: the misrejected item is wrong in every label gate (29/30 each)
    assert res["accuracy"] == {"important": pytest.approx(29 / 30, abs=1e-4), "urgent": pytest.approx(29 / 30, abs=1e-4),
                               "decision": pytest.approx(29 / 30, abs=1e-4)}
    gates = {gt["key"]: gt["actual"] for gt in res["pillars"]["gates"]}
    assert gates["important"] == res["accuracy"]["important"]


def test_one_judge_red_flag_fails_the_run(tmp_path, golden_rows):
    outs = [output_from_gold(r) for r in golden_rows]
    fid = next(o["feedback_id"] for o in outs if o["decision"] == "拒绝")
    rc, res = _run(tmp_path, golden_rows, outs, flag_fid=fid)
    assert res["accuracy"] == {"important": 1.0, "urgent": 1.0, "decision": 1.0}
    assert rc == 1 and res["verdict"] == "fail" and res["pillars"]["red_line"]["by_kind"]["reject_red_flag"] == 1
    rq_gate = next(gt for gt in res["pillars"]["gates"] if gt["key"] == "reject_quality")
    assert rq_gate["ok"] == rq_gate["n"] - 1  # the red-flag item is a Fail even though the old value was 0.4


def test_report_shows_three_pillars(tmp_path, golden_rows):
    row = non_rejectable(golden_rows)
    outs = [output_from_gold(r, decision="拒绝") if r is row else output_from_gold(r) for r in golden_rows]
    md = tmp_path / "rep.md"
    bs.main(["--results", _write(tmp_path, "r.jsonl", outs), "--report-md", str(md)])
    text = md.read_text(encoding="utf-8")
    for s in ("## 1. 红线（一票否决）：1 条", "## 2. 门禁（全部达标才通过）", "## 3. 观测（只看趋势，不影响结论）",
              "❌ 不通过", row["feedback_id"], "红线·整条判错", "未评估"):
        assert s in text, s


def test_observations_never_change_the_verdict():
    it = dict(fields={"important": True, "urgent": True, "decision": True}, rejected=False, decision="迭代")
    a = pillars.build([pillars.new_item("A", dimensions=["认知"], case_type="简单", **it)], ["important", "urgent", "decision"])
    b = pillars.build([pillars.new_item("A", dimensions=["性能"], case_type="对抗", format_error=False,
                                        capabilities=["CAP-1"], cap_hit=False, **it)], ["important", "urgent", "decision"])
    assert a["verdict"] == b["verdict"] == "pass"
    assert a["observations"] != b["observations"]


def test_unjudged_rejections_keep_the_run_incomplete():
    it = pillars.new_item("A", fields={"important": True}, rejected=True, decision="拒绝")
    p = pillars.build([it], ["important"])
    assert p["verdict"] == "incomplete" and not p["passed"]
    assert next(g for g in p["gates"] if g["key"] == "reject_quality")["status"] == "not_evaluated"
