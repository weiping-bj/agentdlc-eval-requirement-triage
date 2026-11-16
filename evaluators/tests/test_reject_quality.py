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


def test_red_flag_is_red_line(golden_rows):
    c, _ = client([FLAGGED])
    r = lf.evaluate(event([triage_span(output_from_gold(g(golden_rows, "G02")))]), client=c)
    assert r["label"] == "Fail" and r["value"] == 0.2 and "承诺" in r["explanation"] and "红线一票否决" in r["explanation"]


def test_rejecting_non_rejectable_is_red_line(golden_rows):
    c, _ = client([GOOD])
    out = output_from_gold(g(golden_rows, "G03"), decision="拒绝", reject_reason="不做", customer_reply="抱歉")
    r = lf.evaluate(event([triage_span(out)]), client=c)
    # lowest failing grade (不合格 0.2), not 0.0: the native judge reserves 0.0 for 不适用
    assert r["label"] == "Fail" and r["value"] == 0.2 and "不应自动拒绝" in r["explanation"]


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


def test_judge_excerpt_carries_catalog_menu_path():
    """A menu path the agent quotes from the catalog (how_to) must be in the reviewer's catalog excerpt; without it
    the reviewer cannot tell a catalog path from an invented one (red line 编造平台并不存在的功能或配置路径)."""
    caps = [c for c in lf._catalog() if c.get("how_to")]
    assert caps
    for c in caps:
        line = next((l for l in lf.capability_hints(c["name"] + " " + c["description"], k=50).splitlines()
                     if l.startswith(f"- {c['id']} ")), "")
        assert c["how_to"] in line, (c["id"], line)


def test_judge_prompt_excerpt_shows_menu_path(golden_rows):
    c, fake = client([GOOD])
    lf.evaluate(event([triage_span(output_from_gold(g(golden_rows, "G06")))]), client=c)
    prompt = fake.calls[0]["messages"][0]["content"][0]["text"]
    excerpt = prompt.split("【平台能力目录摘录】")[1].split("【评分维度")[0]
    assert "CAP-004" in excerpt and "后台 → 对象管理 → 查重规则" in excerpt


def test_judge_excerpt_adds_capability_whose_menu_path_the_agent_quotes(golden_rows):
    """A catalog path quoted in the reply is shown to the judge even when its capability is outside the top-k
    ranking of requirement + reject_reason; a path that is not in the catalog adds nothing."""
    req = "审批超时后希望自动提醒审批人并升级给上级"
    assert "CAP-017" not in lf.capability_hints(req, k=1)
    hints = lf.capability_hints(req, k=1, quoted="可在“后台→通知设置”里选择提醒方式")
    assert "- CAP-017 " in hints and "用法：后台 → 通知设置" in hints
    assert lf.capability_hints(req, k=1, quoted="可在“后台 → 提醒中心 → 升级”里设置") == lf.capability_hints(req, k=1)
    excerpts = []
    for reply in ("可以通过开放接口对接。", "可以在“开放平台 → 应用管理”创建应用，通过开放接口对接。"):
        c, fake = client([GOOD])
        lf.evaluate(event([triage_span(output_from_gold(g(golden_rows, "G04"), customer_reply=reply))]), client=c)
        excerpts.append(fake.calls[0]["messages"][0]["content"][0]["text"].split("【平台能力目录摘录】")[1].split("【评分维度")[0])
    assert "- CAP-009 " not in excerpts[0] and "- OOS-001 " in excerpts[0]
    assert "- CAP-009 " in excerpts[1] and "用法：开放平台 → 应用管理" in excerpts[1] and "- OOS-001 " in excerpts[1]


@pytest.mark.parametrize("quoted, added", [
    ("在 后台 > 通知设置 里配置", True),                      # other arrow, spaces
    ("在 后台 --> 通知设置 里配置", True),
    ("在“后台 → 通知设置 → 升级规则”里配置", False),          # a level appended to the catalog path
    ("在“系统 → 后台 → 通知设置”里配置", False),              # a level put in front
    ("在“后台 → 提醒中心 → 升级”里设置", False),              # not in the catalog
])
def test_quoted_path_lookup_needs_the_catalog_levels_as_a_path_of_their_own(quoted, added):
    req = "审批超时后希望自动提醒审批人并升级给上级"
    assert ("- CAP-017 " in lf.capability_hints(req, k=1, quoted=quoted)) is added


def test_quoted_sub_path_and_quote_marks():
    req = "希望 CRM 内置财务总账和凭证"  # top-1 is OOS-001
    assert "- CAP-007 " in lf.capability_hints(req, k=1, quoted="“流程管理 → 审批流 → 节点设置 → 超时处理”")
    assert "- CAP-003 " in lf.capability_hints(req, k=1, quoted="在 列表页 → 导入 → 选择‘按唯一字段更新’ 即可")
    assert "- CAP-007 " not in lf.capability_hints(
        req, k=1, quoted="后台 → 流程管理 → 审批流 → 选择对应审批节点 → 节点设置 → 超时处理")  # a level inserted
    assert "- CAP-007 " not in lf.capability_hints(req, k=1, quoted="后台 → 流程管理 → 超时处理")  # middle levels dropped


def test_path_rule_in_both_judges():
    """Both judges read 编造…配置路径 against the catalog how_to: a catalog path is fine, a changed or uncatalogued one is not."""
    assert "how_to" in rubric.PATH_RULE and "插入、追加、替换或删去任何一级" in rubric.PATH_RULE
    assert "话术所说功能" in rubric.PATH_RULE and "别的功能的路径" in rubric.PATH_RULE  # a real path on the wrong feature
    assert rubric.PATH_RULE in rubric.judge_user_prompt("r", "x", "y")
    cfg = json.load(open(os.path.join(EV, "llm_judge_config", "s12_reject_quality.json"), encoding="utf-8"))
    assert rubric.PATH_RULE in cfg["llmAsAJudge"]["instructions"].split("【评分档位】")[0]


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
    assert r["label"] == "Fail" and r["value"] == 0.2 and "截断" in r["explanation"]
    with pytest.raises(ValueError):
        judge.parse_verdict('{"scores":{"A":2,"B"')


def test_max_tokens_default_and_env(monkeypatch):
    c, fake = client([GOOD])
    c.complete("s", "p")
    assert fake.calls[0]["inferenceConfig"]["maxTokens"] >= 2000
    monkeypatch.setenv("JUDGE_MAX_TOKENS", "4096")
    c2 = judge.BedrockJudgeClient("m", None, "us-west-2", client=FakeConverse([GOOD]))
    assert c2.max_tokens == 4096


@pytest.mark.parametrize("flags,expect_pass", [("none", True), ("无红线", True), ("N/A", True), (["没有"], True),
                                               ("承诺上线时间", False), (None, True), (123, True)])
def test_review_M5_red_flag_shapes(flags, expect_pass):
    res = judge.score_verdict({"scores": {"A": 5, "B": 5, "C": 5, "D": 5, "E": 5}, "red_flags": flags})
    assert (res["label"] == "Pass") is expect_pass, (flags, res)


def test_review_L2_unmatched_trace_skipped(golden_rows):
    c, fake = client([GOOD])
    r = lf.evaluate(event([triage_span(output_from_gold(g(golden_rows, "G04")), trace="1" * 32)], trace="2" * 32), client=c)
    assert r["label"] == "Skipped" and fake.calls == []
