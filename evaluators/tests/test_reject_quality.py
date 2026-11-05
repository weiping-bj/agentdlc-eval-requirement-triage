"""s12_reject_quality: code-based LLM judge (mocked Bedrock) + native LLM-as-judge config."""
import json
import os
import re

import pytest

from s12_eval_helpers import EV, event, load_pkg, output_from_gold, triage_span

lf = load_pkg("s12_reject_quality")
judge = lf.judge
rubric = judge.rubric


class FakeErr(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code, "Message": code}}


class FakeConverse:
    """Stands in for boto3 bedrock-runtime; records calls; returns scripted texts or raises."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def converse(self, **kw):
        self.calls.append(kw)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return {"output": {"message": {"content": [{"text": item}]}}, "usage": {"inputTokens": 10, "outputTokens": 5}}


GOOD = json.dumps({"scores": {"A": 5, "B": 5, "C": 4, "D": 5, "E": 5}, "red_flags": [], "covered_points": ["x"],
                   "missing_points": [], "comment": "专业"}, ensure_ascii=False)
FLAGGED = json.dumps({"scores": {"A": 5, "B": 5, "C": 5, "D": 5, "E": 5}, "red_flags": ["承诺具体上线时间"],
                      "missing_points": ["替代方案"], "comment": "承诺了下个版本"}, ensure_ascii=False)


def client(script, model="m-primary", fallback="m-fallback"):
    fake = FakeConverse(script)
    return judge.BedrockJudgeClient(model, fallback, "us-west-2", client=fake, sleep=lambda s: None), fake


def g(rows, gid):
    return next(r for r in rows if r["golden_id"] == gid)


def test_not_applicable_for_non_rejection(golden_rows):
    c, fake = client([])
    r = lf.evaluate(event([triage_span(output_from_gold(g(golden_rows, "G01")))]), client=c)
    assert r["label"] == "不适用" and "value" not in r and fake.calls == []


def test_good_rejection_passes_and_prompt_has_gold_points(golden_rows):
    g04 = g(golden_rows, "G04")
    c, fake = client(["```json\n" + GOOD + "\n```"])
    r = lf.evaluate(event([triage_span(output_from_gold(g04))]), client=c)
    assert r["label"] == "Pass" and r["value"] == pytest.approx((1 + 1 + 0.75 + 1 + 1) / 5)
    prompt = fake.calls[0]["messages"][0]["content"][0]["text"]
    assert g04["reject_points"][0] in prompt and "平台能力目录摘录" in prompt and "OOS-001" in prompt
    assert fake.calls[0]["inferenceConfig"]["temperature"] == 0.0
    assert "m-primary" in r["explanation"]


def test_red_flag_caps_score(golden_rows):
    c, _ = client([FLAGGED])
    r = lf.evaluate(event([triage_span(output_from_gold(g(golden_rows, "G02")))]), client=c)
    assert r["label"] == "Fail" and r["value"] == 0.4 and "承诺" in r["explanation"]


def test_rejecting_non_rejectable_is_zero(golden_rows):
    c, _ = client([GOOD])
    out = output_from_gold(g(golden_rows, "G03"), decision="拒绝", reject_reason="不做", customer_reply="抱歉")
    r = lf.evaluate(event([triage_span(out)]), client=c)
    assert r["label"] == "Fail" and r["value"] == 0.0 and "不应自动拒绝" in r["explanation"]


def test_fallback_model_on_access_denied(golden_rows):
    c, fake = client([FakeErr("AccessDeniedException"), GOOD])
    r = lf.evaluate(event([triage_span(output_from_gold(g(golden_rows, "G04")))]), client=c)
    assert r["label"] == "Pass" and [k["modelId"] for k in fake.calls] == ["m-primary", "m-fallback"]
    assert "m-fallback" in r["explanation"]


def test_retry_on_throttling_then_error_code():
    c, fake = client([FakeErr("ThrottlingException"), GOOD])
    assert "scores" in c.complete("s", "p") and len(fake.calls) == 2
    c, _ = client([FakeErr("InternalFailure")])
    with pytest.raises(FakeErr):
        c.complete("s", "p")


def test_handler_returns_error_code_on_judge_failure(golden_rows, monkeypatch):
    c, _ = client(["not json at all"])
    monkeypatch.setattr(lf, "_client", lambda: c)
    r = lf.handler(event([triage_span(output_from_gold(g(golden_rows, "G04")))]))
    assert r["errorCode"] == "S12_REJECT_JUDGE_FAILED"


def test_format_error_and_skipped():
    T = "a" * 32
    assert lf.evaluate(event([{"traceId": T, "attributes": {"gen_ai.completion": "拒绝"}}]))["label"] == "FormatError"
    assert lf.evaluate(event([{"traceId": T, "attributes": {}}]))["label"] == "Skipped"


def test_score_verdict_clamps_and_ignores_empty_flags():
    res = judge.score_verdict({"scores": {"A": 9, "B": 0, "C": "x", "D": 3, "E": 3}, "red_flags": ["无", ""]})
    assert res["scores"] == {"A": 5.0, "B": 1.0, "C": 1.0, "D": 3.0, "E": 3.0} and res["red_flags"] == []


def test_resolve_models_reads_repo_config(monkeypatch, tmp_path):
    for k in ("JUDGE_MODEL_ID", "FALLBACK_MODEL_ID", "JUDGE_REGION", "AWS_REGION", "AWS_DEFAULT_REGION"):
        monkeypatch.delenv(k, raising=False)
    m = judge.resolve_models()
    cfg = judge.read_env_file(os.path.join(os.path.dirname(EV), "config", "lab.env"))
    assert m["model"] == cfg.get("JUDGE_MODEL_ID", judge.DEFAULT_MODEL)
    assert m["fallback"] == cfg.get("FALLBACK_MODEL_ID", judge.DEFAULT_FALLBACK)
    monkeypatch.setenv("JUDGE_MODEL_ID", "env-model")
    assert judge.resolve_models()["model"] == "env-model"
    assert judge.resolve_models({})["fallback"] in (judge.DEFAULT_FALLBACK, os.environ.get("FALLBACK_MODEL_ID"))
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "lab.env").write_text("JUDGE_MODEL_ID=a\n", encoding="utf-8")
    (tmp_path / "config" / "local.env").write_text("JUDGE_MODEL_ID=b\n", encoding="utf-8")
    assert judge.repo_config(str(tmp_path / "x" / "y"))["JUDGE_MODEL_ID"] == "b"


def test_native_llm_judge_config_is_online_safe():
    path = os.path.join(EV, "llm_judge_config", "s12_reject_quality.json")
    cfg = json.load(open(path, encoding="utf-8"))["llmAsAJudge"]
    ph = set(re.findall(r"\{([a-z_]+)\}", cfg["instructions"]))
    assert ph == {"context", "assistant_turn"}
    assert cfg["modelConfig"]["bedrockEvaluatorModelConfig"]["modelId"] == "__JUDGE_MODEL_ID__"
    vals = [x["value"] for x in cfg["ratingScale"]["numerical"]]
    assert len(vals) == len(set(vals)) and all(v >= 0 for v in vals)
    labels = [x["label"] for x in cfg["ratingScale"]["numerical"]]
    assert "不适用" in labels and all(1 <= len(l) <= 100 for l in labels)
    assert "输出" not in cfg["instructions"].split("【评分档位】")[1]  # no output-format instructions


def test_bigram_capability_hints():
    hints = lf.capability_hints("希望 CRM 内置财务总账和凭证")
    assert "OOS-001" in hints


def test_temperature_rejected_by_model_is_dropped_and_remembered():
    err = FakeErr("ValidationException")
    err.args = ("The model returned the following errors: `temperature` is deprecated for this model.",)
    c, fake = client([err, GOOD, GOOD])
    c.complete("s", "p")
    c.complete("s", "p")
    assert "temperature" in fake.calls[0]["inferenceConfig"]
    assert "temperature" not in fake.calls[1]["inferenceConfig"] and "temperature" not in fake.calls[2]["inferenceConfig"]
    assert c.used_model == "m-primary"


def test_truncated_judge_output_falls_back_to_scores(golden_rows):
    truncated = ('{"scores":{"A":2,"B":4,"C":3,"D":2,"E":5},"red_flags":["编造功能"],"comment":"x",'
                 '"missing_points":["平台已支持用网页组件把 ERP 页面嵌入首页（CAP')
    v = judge.parse_verdict(truncated)
    assert v["_partial"] and v["scores"]["A"] == 2 and v["red_flags"] == ["编造功能"]
    c, _ = client([truncated])
    r = lf.evaluate(event([triage_span(output_from_gold(g(golden_rows, "G02")))]), client=c)
    assert r["label"] == "Fail" and r["value"] == 0.4 and "截断" in r["explanation"]
    with pytest.raises(ValueError):
        judge.parse_verdict('{"scores":{"A":2,"B"')


def test_max_tokens_default_and_env(monkeypatch):
    c, fake = client([GOOD])
    c.complete("s", "p")
    assert fake.calls[0]["inferenceConfig"]["maxTokens"] >= 2000
    monkeypatch.setenv("JUDGE_MAX_TOKENS", "4096")
    c2 = judge.BedrockJudgeClient("m", None, "us-west-2", client=FakeConverse([GOOD]))
    assert c2.max_tokens == 4096
