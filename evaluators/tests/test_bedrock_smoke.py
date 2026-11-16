"""One real Bedrock call path for the LLM judge (good vs bad rejection).

Runs only when AWS credentials are available. Set S12_SKIP_BEDROCK=1 to skip.
Uses the model from JUDGE_MODEL_ID / config/lab.env (default Claude Sonnet 5.5), fallback Nova.
"""
import os

import pytest

from s12_eval_helpers import load_pkg

lf = load_pkg("s12_reject_quality")
judge = lf.judge


def _creds_ok():
    if os.environ.get("S12_SKIP_BEDROCK") == "1":
        return False
    try:
        import boto3
        return boto3.Session().get_credentials() is not None
    except Exception:
        return False


@pytest.mark.skipif(not _creds_ok(), reason="no AWS credentials (or S12_SKIP_BEDROCK=1)")
def test_real_judge_ranks_good_above_bad(golden_rows):
    m = judge.resolve_models()
    c = judge.BedrockJudgeClient(m["model"], m["fallback"], m["region"])
    g04 = next(r for r in golden_rows if r["golden_id"] == "G04")
    gold = lf.golden_store.gold_from_golden_item(g04)
    req = g04["input"]["title"] + "。" + g04["input"]["content"]
    good = {"decision": "拒绝", "reasoning": "能力目录 OOS-001 财务总账明确不做",
            "reject_reason": "总账、凭证、会计核算属于专业财务系统范畴，平台明确不做（OOS-001）；回款与开票可通过集成平台的财务软件连接器（CAP-010）同步。",
            "customer_reply": "非常理解贵司希望业财一体、减少系统切换的诉求。CRM 平台的定位是客户与销售业务管理，总账和凭证需要专业财务系统的核算与合规能力，我们不会在 CRM 内重复建设。"
                              "建议通过集成平台的财务软件连接器，把回款、开票数据自动同步到贵司的财务系统，实现业财打通；我们可以安排实施顾问评估对接方案。"}
    bad = {"decision": "拒绝", "reasoning": "", "reject_reason": "做不了",
           "customer_reply": "这个需求我们不做，下个版本也不会做，请自己想办法。"}
    rg = judge.judge_rejection(c, req, good, gold, lf.capability_hints(req))
    rb = judge.judge_rejection(c, req, bad, gold, lf.capability_hints(req))
    print("model", c.used_model, "good", rg["result"], "bad", rb["result"], "usage", c.usage)
    if os.environ.get("S12_ALLOW_FALLBACK") != "1":
        assert c.used_model == m["model"], f"primary judge model not used (fell back to {c.used_model})"
    assert 0.0 <= rb["result"]["value"] < rg["result"]["value"] <= 1.0
    assert rg["result"]["label"] == "Pass" and rb["result"]["label"] == "Fail"


@pytest.mark.skipif(not _creds_ok(), reason="no AWS credentials (or S12_SKIP_BEDROCK=1)")
def test_real_native_judge_instructions_pick_sensible_grades(golden_rows):
    """Render the native LLM-as-judge instructions like the service would and check the grade picked.

    The service appends its own output-format prompt; here a minimal stand-in asks for the score value.
    """
    import json as _json
    import re as _re
    cfg = _json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                       "llm_judge_config", "s12_reject_quality.json"), encoding="utf-8"))["llmAsAJudge"]
    m = judge.resolve_models()
    c = judge.BedrockJudgeClient(m["model"], m["fallback"], m["region"])
    g04 = next(r for r in golden_rows if r["golden_id"] == "G04")
    g01 = next(r for r in golden_rows if r["golden_id"] == "G01")
    ctx = lambda g: f"User: 需求编号: {g['feedback_id']}\n标题: {g['input']['title']}\n需求描述: {g['input']['content']}"
    good = {"feedback_id": g04["feedback_id"], "important": "否", "urgent": "否", "decision": "拒绝",
            "reject_reason": "总账与会计核算属于专业财务系统范畴，平台明确不做（OOS-001）；可用财务软件连接器（CAP-010）同步回款与开票。",
            "customer_reply": "理解贵司希望业财一体化。CRM 的定位是客户与销售管理，总账凭证需要专业财务系统的核算与合规能力，我们不在 CRM 内重复建设。"
                              "建议通过集成平台的财务软件连接器，把回款和开票数据同步到财务系统；我们可以安排实施顾问协助评估对接方案。"}
    iterate = {"feedback_id": g01["feedback_id"], "important": "是", "urgent": "否", "decision": "迭代", "reject_reason": "", "customer_reply": ""}
    std = "\n\n请先用一两句话说明理由，然后在最后一行只输出：score: <从评分档位中选择的数值>"
    scores = []
    for g, out in ((g04, good), (g01, iterate)):
        prompt = cfg["instructions"].replace("{context}", ctx(g)).replace("{assistant_turn}", _json.dumps(out, ensure_ascii=False)) + std
        text = c.complete("", prompt)
        mm = _re.findall(r"score\s*[:：]\s*([0-9.]+)", text)
        assert mm, text[-300:]
        scores.append(float(mm[-1]))
    print("native judge grades", scores, "model", c.used_model)
    assert scores[0] >= 0.8 and scores[1] == 0.0
