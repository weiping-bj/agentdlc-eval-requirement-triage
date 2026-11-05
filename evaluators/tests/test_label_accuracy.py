"""s12_label_accuracy Lambda + batch scorer."""
import json
import os
import subprocess
import sys

import pytest

from s12_eval_helpers import EV, REPO, event, load_pkg, output_from_gold, triage_span

lf = load_pkg("s12_label_accuracy")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("S12_SCORE_FIELD", "S12_LABEL_SCHEME", "S12_LEVEL_TOLERANCE", "S12_GOLDEN_PATH"):
        monkeypatch.delenv(k, raising=False)


def g(rows, gid):
    return next(r for r in rows if r["golden_id"] == gid)


def test_pass(golden_rows):
    g01 = g(golden_rows, "G01")
    res = lf.handler(event([triage_span(output_from_gold(g01), fid=g01["feedback_id"])]))
    assert res["label"] == "Pass" and res["value"] == 1.0
    assert "3/3" in res["explanation"] and len(res["explanation"]) <= 2000


def test_partial_and_critical(golden_rows):
    g01, g03 = g(golden_rows, "G01"), g(golden_rows, "G03")
    res = lf.handler(event([triage_span(output_from_gold(g01, urgent="是"))]))
    assert res["label"] == "Fail" and res["value"] == pytest.approx(0.6667)
    res = lf.handler(event([triage_span(output_from_gold(g03, decision="拒绝"))]))
    assert res["label"] == "CriticalFail" and "严重错误" in res["explanation"]


def test_acceptable_decisions(golden_rows):
    amb = next(r for r in golden_rows if len(r["acceptable_decisions"]) > 1)
    alt = [d for d in amb["acceptable_decisions"] if d != amb["expected"]["decision"]][0]
    res = lf.handler(event([triage_span(output_from_gold(amb, decision=alt))]))
    assert res["label"] == "Pass"


def test_single_field_by_suffix_and_env(golden_rows, monkeypatch):
    g03 = g(golden_rows, "G03")
    spans = [triage_span(output_from_gold(g03, urgent="否", decision="拒绝"))]
    r = lf.handler(event(spans, name="s12lab_ab12_label_urgent"))
    assert r["value"] == 0.0 and r["label"] == "Fail"
    r = lf.handler(event(spans, name="s12lab_ab12_label_important"))
    assert r["value"] == 1.0 and r["label"] == "Pass"  # critical only applies when decision is scored
    r = lf.handler(event(spans, name="s12lab_ab12_label_decision"))
    assert r["value"] == 0.0 and r["label"] == "CriticalFail"
    monkeypatch.setenv("S12_SCORE_FIELD", "important")
    assert lf.handler(event(spans, name="s12lab_ab12_label_urgent"))["value"] == 1.0
    monkeypatch.setenv("S12_SCORE_FIELD", "bogus")
    assert lf.handler(event(spans))["errorCode"] == "VALIDATION_FAILED"


def test_format_error_skipped_and_not_in_golden():
    T = "a" * 32
    r = lf.handler(event([{"traceId": T, "attributes": {"gen_ai.completion": "应该迭代吧"}}]))
    assert r["label"] == "FormatError" and r["value"] == 0.0
    r = lf.handler(event([{"traceId": T, "attributes": {}}]))
    assert r["label"] == "Skipped" and "value" not in r
    out = {"feedback_id": "FB-9999-999", "important": "是", "urgent": "否", "decision": "迭代"}
    r = lf.handler(event([triage_span(out)]))
    assert r["label"] == "Skipped" and "黄金集" in r["explanation"]


def test_reference_inputs_override_bundled_golden(golden_rows):
    g01 = g(golden_rows, "G01")
    ref = [{"context": {"spanContext": {"sessionId": "sess-1", "traceId": "a" * 32}},
            "expectedResponse": {"text": json.dumps({"feedback_id": g01["feedback_id"], "important": "否", "urgent": "否",
                                                    "decision": "拒绝", "acceptable_decisions": ["拒绝"]}, ensure_ascii=False)}}]
    r = lf.handler(event([triage_span(output_from_gold(g01))], refs=ref))
    assert r["value"] == pytest.approx(0.3333)  # only urgent agrees with the reference


def test_reference_from_agentcore_dataset_projection(golden_rows):
    ds = json.load(open(os.path.join(REPO, "data", "golden", "agentcore_dataset.json"), encoding="utf-8"))
    sc = ds["scenarios"][2]
    gold = next(r for r in golden_rows if r["golden_id"] == sc["scenario_id"])
    ref = [{"expectedResponse": {"text": sc["turns"][0]["expected_response"]}}]
    r = lf.handler(event([triage_span(output_from_gold(gold))], refs=ref))
    assert r["label"] == "Pass"


def test_level_scheme(golden_rows, monkeypatch):
    g01 = g(golden_rows, "G01")  # P1
    monkeypatch.setenv("S12_LABEL_SCHEME", "level")
    r = lf.handler(event([triage_span(output_from_gold(g01, level="P2"))]))
    assert r["value"] == 0.5
    monkeypatch.setenv("S12_LEVEL_TOLERANCE", "1")
    r = lf.handler(event([triage_span(output_from_gold(g01, level="P2"))]))
    assert r["value"] == 1.0


def test_batch_mode_in_one_trace(golden_rows):
    outs = [output_from_gold(r) for r in golden_rows[:3]] + [{"feedback_id": "NOPE", "important": "是", "urgent": "是", "decision": "迭代"}]
    outs[0]["important"] = "否" if outs[0]["important"] == "是" else "是"
    r = lf.handler(event([triage_span({"results": outs})]))
    assert r["label"] == "Fail" and r["value"] == pytest.approx((2 / 3 + 1 + 1) / 3, abs=1e-3)
    assert "NOPE" in r["explanation"]


def test_session_level_and_string_event(golden_rows):
    g01 = g(golden_rows, "G01")
    ev = event([triage_span(output_from_gold(g01))], level="SESSION")
    assert lf.handler(json.dumps(ev, ensure_ascii=False))["label"] == "Pass"
    assert lf.handler({"bad": 1})["label"] == "Skipped"


def test_handler_never_raises(monkeypatch):
    monkeypatch.setattr(lf, "evaluate", lambda e: 1 / 0)
    assert lf.handler({})["errorCode"] == "S12_LABEL_EVAL_FAILED"


def test_bundled_copies_are_in_sync():
    r = subprocess.run([sys.executable, "-I", os.path.join(EV, "sync_shared.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_lambda_package_is_self_contained():
    for pkg, need in (("s12_label_accuracy", {"lambda_function.py", "span_adapter.py", "golden_store.py", "label_scoring.py", "golden_set.json"}),
                      ("s12_reject_quality", {"lambda_function.py", "span_adapter.py", "golden_store.py", "judge.py", "rubric.py",
                                              "golden_set.json", "capability_catalog.json"})):
        assert need <= set(os.listdir(os.path.join(EV, pkg))), pkg
    code = open(os.path.join(EV, "s12_label_accuracy", "lambda_function.py"), encoding="utf-8").read()
    assert "bedrock_agentcore" not in code.split('"""', 2)[2]


# ---------------------------------------------------------------- batch scorer
def _write(tmp_path, rows, name="r.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return str(p)


def test_batch_score_perfect_and_baseline(tmp_path, golden_rows):
    bs = load_pkg("s12_label_accuracy", "batch_score")
    perfect = [{"session_id": "s", "result": output_from_gold(r)} for r in golden_rows]
    md = tmp_path / "rep.md"
    oj = tmp_path / "rep.json"
    assert bs.main(["--results", _write(tmp_path, perfect), "--report-md", str(md), "--out-json", str(oj), "--target", "0.7"]) == 0
    res = json.loads(oj.read_text(encoding="utf-8"))
    assert res["passed"] and res["accuracy"] == {"important": 1.0, "urgent": 1.0, "decision": 1.0}
    assert "达标" in md.read_text(encoding="utf-8")
    base = [{"feedback_id": r["feedback_id"], "important": "否", "urgent": "否", "decision": "拒绝"} for r in golden_rows]
    oj2 = tmp_path / "b.json"
    rc = bs.main(["--results", _write(tmp_path, base, "b.jsonl"), "--out-json", str(oj2), "--fail-under"])
    res = json.loads(oj2.read_text(encoding="utf-8"))
    assert rc == 1 and not res["passed"]
    assert res["per_class_recall"]["urgent"]["是"] == 0.0  # trivial baseline exposed by recall
    assert res["critical_misrejects"] and res["failures"]
    assert res["distribution"]["decision"] == {"拒绝": 30}


def test_batch_score_missing_unparsed_and_spans(tmp_path, golden_rows):
    bs = load_pkg("s12_label_accuracy", "batch_score")
    rows = [{"result": output_from_gold(r)} for r in golden_rows[:20]] + [{"feedback_id": golden_rows[20]["feedback_id"], "raw": "无法解析"}]
    oj = tmp_path / "o.json"
    bs.main(["--results", _write(tmp_path, rows), "--out-json", str(oj)])
    res = json.loads(oj.read_text(encoding="utf-8"))
    assert res["n_missing_output"] == 10 and res["accuracy"]["decision"] == pytest.approx(20 / 30, abs=1e-3)
    assert res["unparsed_outputs"] == [golden_rows[20]["feedback_id"]]
    spans = [triage_span(output_from_gold(r), trace=f"{i:032x}") for i, r in enumerate(golden_rows)]
    sp = tmp_path / "spans.json"
    sp.write_text(json.dumps({"sessionSpans": spans}, ensure_ascii=False), encoding="utf-8")
    oj = tmp_path / "s.json"
    bs.main(["--spans", str(sp), "--out-json", str(oj)])
    assert json.loads(oj.read_text(encoding="utf-8"))["passed"]
    oj = tmp_path / "d.json"
    bs.main(["--spans", str(sp), "--distribution-only", "--out-json", str(oj)])
    assert json.loads(oj.read_text(encoding="utf-8"))["distribution"]["n"] == 30


def test_batch_score_reads_target_from_config(monkeypatch):
    bs = load_pkg("s12_label_accuracy", "batch_score")
    monkeypatch.delenv("TARGET_ACCURACY", raising=False)
    assert bs._cfg_target() == pytest.approx(0.70)
    monkeypatch.setenv("TARGET_ACCURACY", "0.8")
    assert bs._cfg_target() == pytest.approx(0.8)


def test_batch_score_accepts_customer_log_format(tmp_path):
    """telemetry/samples/customer-agent-logs.jsonl format (agent_output is a JSON string)."""
    bs = load_pkg("s12_label_accuracy", "batch_score")
    line = {"session_id": "s1", "feedback_id": "FB-2610-001", "user_input": "需求编号: FB-2610-001",
            "agent_output": json.dumps({"feedback_id": "FB-2610-001", "important": "是", "urgent": "否", "decision": "迭代"}, ensure_ascii=False)}
    preds = bs.preds_from_results(_write(tmp_path, [line]))
    assert preds[0]["decision"] == "迭代" and preds[0]["feedback_id"] == "FB-2610-001"
    sample = os.path.join(REPO, "telemetry", "samples", "customer-agent-logs.jsonl")
    if os.path.exists(sample):
        rows = [json.loads(l) for l in open(sample, encoding="utf-8") if l.strip()]
        free_text = {r["feedback_id"] for r in rows if "{" not in r["agent_output"]}
        got = bs.preds_from_results(sample)
        assert {p["feedback_id"] for p in got if p.get("_unparsed")} == free_text
        assert all(p.get("decision") for p in got if not p.get("_unparsed"))


@pytest.mark.parametrize("ev_keys,expected", [
    ({"evaluatorName": "s12lab_lead_label_important"}, "important"),
    ({"evaluatorId": "s12lab_lead_label_urgent-jY13ehAi51"}, "urgent"),
    ({"evaluatorArn": "arn:aws:bedrock-agentcore:us-west-2:123456789012:evaluator/s12lab_lead_label_decision-AbCdEf1234"}, "decision"),
    ({"evaluatorName": None, "evaluatorId": "s12lab_lead_label_accuracy-jY13ehAi51"}, "all"),
    ({"evaluatorName": "S12LAB_X_LABEL_IMPORTANT"}, "important"),
    ({"evaluatorName": "s12lab_important_check_v2"}, "important"),  # token match
    ({}, "all"),
])
def test_resolve_score_field_from_name_id_or_arn(ev_keys, expected):
    assert lf.resolve_score_field(ev_keys) == expected


def test_suffix_from_evaluator_id_scores_single_field(golden_rows, capsys):
    g03 = g(golden_rows, "G03")
    ev = event([triage_span(output_from_gold(g03, urgent="否"))], name=None)
    ev.pop("evaluatorName")
    ev["evaluatorId"] = "s12lab_lead_label_urgent-jY13ehAi51"
    r = lf.handler(ev)
    assert r["value"] == 0.0 and "1/1" not in r["explanation"] and "0/1" in r["explanation"]
    log = capsys.readouterr().out
    assert '"field": "urgent"' in log and '"n_spans": 1' in log
