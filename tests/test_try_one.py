"""labkit.try_one (46-try-one.sh): argument handling, item lookup and terminal formatting, and the main flow with the
Runtime invocation and the evaluation replaced by fakes. No AWS call: aws.client is patched to fail the test if used."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from labkit import aws, batch, evaluate, items, try_one  # noqa: E402

GOLDEN = items.read_jsonl(ROOT / "data" / "golden" / "golden_set.jsonl")
POOL = items.read_jsonl(ROOT / "data" / "feedback_last_month.jsonl")
G06 = next(r for r in GOLDEN if r["golden_id"] == "G06")


# ------------------------------------------------------------------------------------------ arguments


@pytest.mark.parametrize("argv", [[], ["FB-2610-009", "--text", "x"], ["--text", "   "], ["--text", "x" * 4001],
                                  ["FB-2610-009", "--no-builtin"], ["--session", "short"],
                                  ["--text", "x", "--session", "adlc-t-try-v1-ref-G06-" + "a" * 32]])
def test_bad_arguments_are_usage_errors(argv, capsys):
    with pytest.raises(SystemExit) as e:
        try_one.parse_args(argv)
    assert e.value.code == 2
    assert "usage: ./46-try-one.sh" in capsys.readouterr().err


def test_good_arguments():
    a = try_one.parse_args(["FB-2610-009", "--eval", "--no-builtin", "--raw"])
    assert (a.item, a.text, a.eval, a.no_builtin, a.raw) == ("FB-2610-009", None, True, True, True)
    a = try_one.parse_args(["--text", "  希望列表页支持批量编辑  "])
    assert a.item is None and a.text == "希望列表页支持批量编辑" and not a.eval


def test_session_means_evaluate_again_without_an_item():
    sid = "adlc-t-try-v1-ref-FB-2610-009-" + "0" * 32
    a = try_one.parse_args(["--session", sid])
    assert a.eval and a.item is None and a.session == sid
    assert try_one.parse_args(["G06", "--session", sid]).item == "G06"


def test_find_item_by_feedback_id_or_golden_id_case_insensitive():
    fid = G06["feedback_id"]
    assert try_one.find_item(fid.lower(), GOLDEN, POOL) == (G06, G06)
    assert try_one.find_item("g06", GOLDEN, POOL) == (G06, G06)
    only_pool = next(r for r in POOL if items.fid_of(r) not in {items.fid_of(g) for g in GOLDEN})
    row, gold = try_one.find_item(items.fid_of(only_pool), GOLDEN, POOL)
    assert row is only_pool and gold is None


@pytest.mark.parametrize("arg,msg", [("FB-9999-999", "都没有 FB-9999-999"), ("希望支持批量编辑", "不像需求编号")])
def test_find_item_explains_unknown_ids_and_free_text(arg, msg):
    with pytest.raises(SystemExit) as e:
        try_one.find_item(arg, GOLDEN, POOL)
    assert msg in str(e.value) and "--text" in str(e.value)


def test_free_text_row():
    row = try_one.free_text_row("希望在移动端审批时能看到附件预览\n第二行", datetime(2026, 11, 15, 1, 2, 3, tzinfo=timezone.utc))
    assert row == {"id": "TRY-20261115010203", "title": "希望在移动端审批时能看到附件预览", "content": "希望在移动端审批时能看到附件预览\n第二行"}
    assert items.fid_of(row) == "TRY-20261115010203"


# ------------------------------------------------------------------------------------------ formatting


def test_output_lines_compare_with_the_golden_answer():
    res = {"important": G06["expected"]["important"], "urgent": "是", "decision": "待评估", "reasoning": "需要进一步评估",
           "reject_reason": "", "customer_reply": ""}
    L = try_one.output_lines(G06, G06, res, "raw text", False)
    text = "\n".join(L)
    assert L[0].startswith(f"需求 {G06['feedback_id']}（G06）：")
    assert f"是否重要  {res['important']}    黄金集 {G06['expected']['important']} ✓" in text
    assert "处理建议  待评估    黄金集 " in text and "✗" in text
    assert "理由：\n    需要进一步评估" in text and "客户话术  —" in text
    assert "--eval" in text and "原始输出" not in text
    assert "优先级" not in text  # binary scheme: the agent gives no level and the golden level is not graded
    graded = "\n".join(try_one.output_lines(G06, G06, {**res, "level": "P2"}, None, False, graded=True))
    assert f"优先级  P2    黄金集 {G06['expected']['level']}" in graded
    shown = "\n".join(try_one.output_lines(G06, G06, {**res, "level": "P2"}, None, False))
    assert "优先级  P2" in shown and "优先级  P2    黄金集" not in shown  # shown, but not compared, in the binary scheme


def test_output_lines_read_json_strings_aliases_and_strip_control_characters():
    res = json.dumps({"是否重要": G06["expected"]["important"], "是否紧急": G06["expected"]["urgent"], "处理建议": "拒绝",
                      "customer_reply": "您好\x1b]8;;http://x\x07点我\x1b[2J\x9b31m"}, ensure_ascii=False)
    text = "\n".join(try_one.output_lines(G06, G06, res, "raw\x1b[31m", True))
    assert f"是否重要  {G06['expected']['important']}    黄金集 {G06['expected']['important']} ✓" in text
    assert "处理建议  拒绝    黄金集 拒绝 ✓" in text
    assert "\x1b" not in text and "\x07" not in text and "\x9b" not in text and "您好]8;;http://x点我[2J31m" in text


def test_output_lines_for_free_text_and_empty_output():
    row = try_one.free_text_row("一条自己写的需求")
    L = "\n".join(try_one.output_lines(row, None, {"important": "否", "urgent": "否", "decision": "迭代"}, None, False))
    assert "黄金集" not in L and "优先级" not in L
    L = "\n".join(try_one.output_lines(row, None, None, "模型说了点别的", False))
    assert "没有返回结构化结果" in L and "原始输出：\n    模型说了点别的" in L


def test_long_text_is_wrapped():
    L = try_one.output_lines(G06, None, {"reasoning": "长" * 250}, None, False)
    assert max(len(x) for x in L) <= try_one.WIDTH + 4


def test_score_lines_show_each_evaluator_and_the_red_line():
    sess = {"feedback_id": G06["feedback_id"], "agent_output": {"decision": "拒绝"}, "scores": {
        "label_accuracy": {"value": 0.0, "label": "CriticalFail", "explanation": "误拒了不可自动拒绝的需求"},
        "label_decision": {"value": 0.0, "label": "Fail", "explanation": "处理建议 错"},
        "Builtin.Helpfulness": {"errorCode": "THROTTLED", "errorMessage": "slow down"}}}
    L = "\n".join(try_one.score_lines(sess, G06))
    assert L.index("label_accuracy") < L.index("label_decision") < L.index("Builtin.Helpfulness")
    assert "CriticalFail" in L and "0.00" in L and "误拒了不可自动拒绝的需求" in L
    assert "出错 THROTTLED slow down" in L and "⛔ 红线（一票否决）：误拒" in L
    assert "评估没完成" in "\n".join(try_one.score_lines({"error": "no spans"}, None))
    free = "\n".join(try_one.score_lines({"feedback_id": "TRY-1", "scores": {"label_accuracy": {"label": "Skipped"}}}, None))
    assert "不在黄金集里" in free and "红线：无" not in free


# ------------------------------------------------------------------------------------------ main flow (fakes)


@pytest.fixture
def lab(monkeypatch):
    state = {"runtime": {"agentRuntimeArn": "arn:aws:bedrock-agentcore:us-west-2:000000000000:runtime/x", "agentVersion": "v1-ref",
                         "modelId": "model-x", "logGroup": "/aws/lg"},
             "evaluators": {"label_accuracy": {"evaluatorId": "la-1"}, "_meta": {"x": 1}}}
    calls = {"invoke": [], "eval": []}

    def no_aws(*a, **k):
        raise AssertionError("AWS client used in a unit test")

    def save_state(*a, **k):
        raise AssertionError("a tryout must not write lab state")

    def invoke(row, arn, sid):
        calls["invoke"].append((row, arn, sid))
        return {"session_id": sid, "result": {"important": "否", "urgent": "否", "decision": "拒绝", "reasoning": "r",
                                              "reject_reason": "rr", "customer_reply": "cr"}, "raw": "{}"}

    def fake_eval(rec, golden, evaluators, log_group, start):
        calls["eval"].append((rec, golden, evaluators, log_group, start))
        return {"feedback_id": rec["feedback_id"], "session_id": rec["session_id"], "agent_output": rec["result"],
                "scores": {"label_accuracy": {"value": 1.0, "label": "Pass", "explanation": "3/3"}}}

    monkeypatch.setattr(aws, "client", no_aws)
    monkeypatch.setattr(aws, "save_state", save_state)
    monkeypatch.setattr(aws, "load_state", lambda name, default=None: state.get(name, default))
    monkeypatch.setattr(aws, "cfg", lambda: {"LAB_NAME": "adlc-t", "AGENT_MODEL_ID": "cfg-model"})
    monkeypatch.setattr(batch, "_runtime_one", invoke)
    monkeypatch.setattr(evaluate, "evaluate_session", fake_eval)
    calls["sleep"] = []
    monkeypatch.setattr(try_one.time, "sleep", lambda s: calls["sleep"].append(s))
    return state, calls


def test_main_invokes_once_and_prints_the_output(lab, capsys):
    state, calls = lab
    assert try_one.main(["g06"]) == 0
    out = capsys.readouterr().out
    (row, arn, sid), = calls["invoke"]
    assert row is not None and items.fid_of(row) == G06["feedback_id"] and arn == state["runtime"]["agentRuntimeArn"]
    assert sid.startswith(f"adlc-t-try-v1-ref-{G06['feedback_id']}-") and len(sid) >= 33
    assert f"▶ 试一条：{G06['feedback_id']} · AgentCore Runtime · 版本 v1-ref · 模型 model-x" in out
    assert "客户话术：\n    cr" in out and f"session = {sid}" in out and "加 --eval" in out
    assert calls["eval"] == []


def test_main_eval_runs_the_registered_evaluators_with_the_golden_reference(lab, capsys):
    state, calls = lab
    assert try_one.main([G06["feedback_id"], "--eval"]) == 0
    (rec, golden, evaluators, log_group, _start), = calls["eval"]
    assert golden == {G06["feedback_id"]: G06} and log_group == "/aws/lg"
    assert evaluators == [("label_accuracy", "la-1")] + [(b, b) for b in evaluate.BUILTINS]
    out = capsys.readouterr().out
    assert "▶ 评估这个 session：3 个评估器" in out and "label_accuracy" in out and "红线：无" in out
    assert calls["sleep"] == [try_one.TRACE_WAIT]          # waits for the trace before the first query
    calls["eval"].clear()
    assert try_one.main(["--text", "自己写的", "--eval", "--no-builtin"]) == 0
    (rec, golden, evaluators, _lg, _s), = calls["eval"]
    assert golden == {} and evaluators == [("label_accuracy", "la-1")] and rec["feedback_id"].startswith("TRY-")


def test_main_session_re_evaluates_without_calling_the_agent(lab, monkeypatch, capsys):
    state, calls = lab
    sid = "adlc-t-try-v1-ref-FB-2610-009-" + "0" * 32
    monkeypatch.setattr(try_one.time, "time", lambda: 1_800_000_000)
    assert try_one.main(["G06", "--session", sid, "--no-builtin"]) == 0
    assert calls["invoke"] == [] and calls["sleep"] == []
    (rec, golden, evaluators, _lg, start), = calls["eval"]
    assert rec["session_id"] == sid and golden == {G06["feedback_id"]: G06}
    assert start == 1_800_000_000 - try_one.SESSION_LOOKBACK and evaluators == [("label_accuracy", "la-1")]
    assert "不调用 Agent" in capsys.readouterr().out


def test_main_eval_failure_exits_1_and_says_how_to_retry(lab, monkeypatch, capsys):
    monkeypatch.setattr(evaluate, "evaluate_session", lambda *a, **k: {"error": "no spans found in CloudWatch"})
    assert try_one.main(["G06", "--eval"]) == 1
    out = capsys.readouterr().out
    assert "评估没完成：no spans found" in out and "./46-try-one.sh G06 --session adlc-t-try-v1-ref-" in out


def test_main_explains_missing_runtime_and_evaluators(lab):
    state, _ = lab
    state["evaluators"] = {}
    with pytest.raises(SystemExit) as e:
        try_one.main(["G06", "--eval"])
    assert "./40-create-evaluators.sh" in str(e.value)
    state["runtime"] = {}
    with pytest.raises(SystemExit) as e:
        try_one.main(["G06"])
    assert "./30-deploy-runtime.sh" in str(e.value)


def test_main_reports_an_invocation_failure(lab, monkeypatch, capsys):
    def fail(row, arn, sid):
        raise RuntimeError("RuntimeClientError: " + "x" * 500)

    monkeypatch.setattr(batch, "_runtime_one", fail)
    assert try_one.main(["G06"]) == 1
    out = capsys.readouterr().out
    assert "❌ 调用失败：RuntimeError: RuntimeClientError" in out and len(out.splitlines()[-1]) < 400


def test_wrapper_script_runs_the_module():
    sh = (ROOT / "46-try-one.sh").read_text(encoding="utf-8")
    assert 'exec "${_ADLC_PY}" -m labkit.try_one "$@"' in sh and "source ./00-config.sh" in sh
    for flag in ("--text", "--eval"):
        assert flag in sh
