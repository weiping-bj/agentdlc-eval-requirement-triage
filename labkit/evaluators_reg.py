"""Chapter 4 (evaluate): deploy the custom evaluator Lambdas and register all evaluators in AgentCore Evaluations.

  python -m labkit.evaluators_reg            # create or update everything (idempotent)
  python -m labkit.evaluators_reg --list     # show registered evaluators

Registered (names get the personal prefix adlc_<suffix>_...):
  label_accuracy   code-based TRACE  3-label hit ratio vs golden set (the 70% target)
  label_important  code-based TRACE  single label 0/1 (console average = accuracy)
  label_urgent     code-based TRACE
  label_decision   code-based TRACE
  reject_quality   code-based TRACE  LLM review of rejection reason + customer reply (Skipped if not rejected)
  reject_quality_llm  LLM-as-a-judge TRACE (native AgentCore judge, same rubric)
  label_level      code-based TRACE  only when LABEL_SCHEME=level (extension "label scheme switch")
LABEL_SCHEME / LEVEL_TOLERANCE (config/lab.env, config/local.env) reach every label Lambda as
ADLC_LABEL_SCHEME / ADLC_LEVEL_TOLERANCE.
Source code lives in evaluators/ (see evaluators/README.md).
"""
from __future__ import annotations

import io
import json
import sys
import time
import zipfile
from pathlib import Path

from . import aws

ROOT = Path(aws.cfg()["LAB_ROOT"])
EV = ROOT / "evaluators"

LAMBDAS = {
    # key: (source dir, handler, extra env). One function per scored field, so the
    # field never depends on what the service puts into the event.
    "label": ("adlc_label_accuracy", "lambda_function.handler", {"ADLC_SCORE_FIELD": "all"}),
    "label-important": ("adlc_label_accuracy", "lambda_function.handler", {"ADLC_SCORE_FIELD": "important"}),
    "label-urgent": ("adlc_label_accuracy", "lambda_function.handler", {"ADLC_SCORE_FIELD": "urgent"}),
    "label-decision": ("adlc_label_accuracy", "lambda_function.handler", {"ADLC_SCORE_FIELD": "decision"}),
    "reject": ("adlc_reject_quality", "lambda_function.handler", {}),
}
CODE_EVALUATORS = [
    # (name suffix, lambda key, description)
    ("label_accuracy", "label", "三项标签（是否重要/是否紧急/处理建议）与黄金集一致的比例"),
    ("label_important", "label-important", "是否重要 单项准确率（0/1）"),
    ("label_urgent", "label-urgent", "是否紧急 单项准确率（0/1）"),
    ("label_decision", "label-decision", "处理建议 单项准确率（0/1）"),
    ("reject_quality", "reject", "拒绝理由与客户话术质量（LLM 评审，非拒绝则 Skipped）"),
]
# graded label scheme (LABEL_SCHEME=level): one more single-field Lambda + evaluator for 优先级
LEVEL_LAMBDA = ("label-level", ("adlc_label_accuracy", "lambda_function.handler", {"ADLC_SCORE_FIELD": "level"}))
LEVEL_EVALUATOR = ("label_level", "label-level", "优先级 P0/P1/P2 单项准确率（0/1，分级口径）")


def label_scheme() -> str:
    s = (aws.cfg().get("LABEL_SCHEME") or "binary").strip().lower()
    return s if s in ("binary", "level") else "binary"


def lambdas() -> dict:
    out = dict(LAMBDAS)
    if label_scheme() == "level":
        out[LEVEL_LAMBDA[0]] = LEVEL_LAMBDA[1]
    return out


def code_evaluators() -> list:
    return CODE_EVALUATORS + ([LEVEL_EVALUATOR] if label_scheme() == "level" else [])
JUDGE_CONFIG_CANDIDATES = ["llm_judge_config/adlc_reject_quality.json", "llm_judge_config/reject_quality_llm.json", "llm_judge_config/evaluator_config.json",
                           "llm_judge_config/config.json"]


def ev_name(suffix: str) -> str:
    return f"{aws.cfg()['LAB_PREFIX']}_{aws.cfg()['LAB_SUFFIX']}_{suffix}"


def fn_name(key: str) -> str:
    return f"{aws.cfg()['LAB_NAME']}-eval-{key}"


def lambda_role() -> str:
    c, a = aws.cfg(), aws.account_id()
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"},
                                                      "Action": "sts:AssumeRole"}]}
    inline = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
         "Resource": f"arn:aws:logs:*:{a}:log-group:/aws/lambda/{c['LAB_NAME']}-*"},
        {"Effect": "Allow", "Action": ["bedrock:InvokeModel", "bedrock:Converse"],
         "Resource": ["arn:aws:bedrock:*::foundation-model/*", f"arn:aws:bedrock:*:{a}:inference-profile/*"]}]}
    return aws.ensure_role(c["EVAL_LAMBDA_ROLE_NAME"], trust, inline, "adlc evaluator Lambda role")


def zip_dir(src: Path) -> bytes:
    """Zip one evaluator dir at the Lambda root, plus evaluators/shared/ as package `shared`."""
    buf = io.BytesIO()

    def keep(p: Path) -> bool:
        return p.is_file() and "__pycache__" not in p.parts and "tests" not in p.parts and p.suffix != ".pyc"

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src.rglob("*")):
            if keep(p):
                zf.write(p, str(p.relative_to(src)))
        shared = EV / "shared"
        if shared.exists() and not (src / "shared").exists():
            for p in sorted(shared.rglob("*")):
                if keep(p):
                    zf.write(p, "shared/" + str(p.relative_to(shared)))
    return buf.getvalue()


def deploy_lambda(key: str, role_arn: str) -> str:
    c = aws.cfg()
    src_dir, handler, extra = lambdas()[key]
    src = EV / src_dir
    if not src.exists():
        raise SystemExit(f"❌ 找不到评估器源码 {src.relative_to(ROOT)}")
    lam = aws.client("lambda")
    name = fn_name(key)
    env = {"Variables": {"JUDGE_MODEL_ID": c["JUDGE_MODEL_ID"], "FALLBACK_MODEL_ID": c["FALLBACK_MODEL_ID"],
                         "TARGET_ACCURACY": c["TARGET_ACCURACY"], "ADLC_LABEL_SCHEME": label_scheme(),
                         "ADLC_LEVEL_TOLERANCE": str(c.get("LEVEL_TOLERANCE") or "0"), **extra}}
    code = zip_dir(src)
    try:
        lam.get_function(FunctionName=name)
        lam.update_function_code(FunctionName=name, ZipFile=code)
        lam.get_waiter("function_updated_v2").wait(FunctionName=name)
        lam.update_function_configuration(FunctionName=name, Handler=handler, Environment=env, Timeout=120,
                                          MemorySize=512, Role=role_arn)
        print(f"  🔁 已更新 Lambda {name}")
    except lam.exceptions.ResourceNotFoundException:
        for attempt in range(6):
            try:
                lam.create_function(FunctionName=name, Runtime="python3.12", Architectures=["arm64"], Role=role_arn,
                                    Handler=handler, Code={"ZipFile": code}, Timeout=120, MemorySize=512,
                                    Environment=env, Tags=aws.tags_dict(),
                                    Description=f"adlc evaluator ({src_dir})")
                break
            except lam.exceptions.InvalidParameterValueException as e:
                if "role" in str(e).lower() and attempt < 5:
                    time.sleep(10)
                    continue
                raise
        print(f"  🆕 已创建 Lambda {name}")
    lam.get_waiter("function_active_v2").wait(FunctionName=name)
    lam.get_waiter("function_updated_v2").wait(FunctionName=name)
    return lam.get_function(FunctionName=name)["Configuration"]["FunctionArn"]


def list_custom() -> dict:
    ctl = aws.client("bedrock-agentcore-control")
    out, token = {}, None
    while True:
        kw = {"maxResults": 100}
        if token:
            kw["nextToken"] = token
        r = ctl.list_evaluators(**kw)
        for e in r.get("evaluators", []):
            if not e.get("evaluatorId", "").startswith("Builtin."):
                out[e.get("evaluatorName")] = e
        token = r.get("nextToken")
        if not token:
            return out


def _same(a, b) -> bool:
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


def upsert(name: str, level: str, config: dict, description: str, existing: dict) -> dict:
    """Create the evaluator, or bring it to `config`.

    An evaluator that was ever attached to an online evaluation config is locked for
    modification for good (disabling/deleting the config does not unlock it), but it can
    still be deleted. So: unchanged -> keep; changed + unlocked -> update; changed + locked
    -> delete and re-create (register() removes my online configs first and restores them).
    """
    ctl = aws.client("bedrock-agentcore-control")
    if name in existing:
        eid = existing[name]["evaluatorId"]
        g = ctl.get_evaluator(evaluatorId=eid)
        if _same(g.get("evaluatorConfig"), config) and g.get("level") == level:
            print(f"  ✔ 评估器无变化 {name}")
            return {"evaluatorId": eid, "evaluatorArn": g.get("evaluatorArn"), "level": level}
        if not g.get("lockedForModification"):
            ctl.update_evaluator(evaluatorId=eid, evaluatorConfig=config, level=level, description=description[:200])
            print(f"  🔁 已更新评估器 {name}")
            return {"evaluatorId": eid, "evaluatorArn": g.get("evaluatorArn"), "level": level}
        ctl.delete_evaluator(evaluatorId=eid)
        print(f"  ♻️  {name} 已被在线评估锁定，删除后重建")
        for _ in range(30):
            try:
                ctl.get_evaluator(evaluatorId=eid)
                time.sleep(2)
            except ctl.exceptions.ResourceNotFoundException:
                break
    for attempt in range(10):
        try:
            r = ctl.create_evaluator(evaluatorName=name, level=level, evaluatorConfig=config,
                                     description=description[:200], tags=aws.tags_dict())
            break
        except ctl.exceptions.ConflictException:
            time.sleep(5)  # name still held by the evaluator being deleted
    else:
        raise SystemExit(f"❌ 无法创建评估器 {name}")
    print(f"  🆕 已注册评估器 {name} → {r['evaluatorId']}")
    return {"evaluatorId": r["evaluatorId"], "evaluatorArn": r.get("evaluatorArn"), "level": level}


def needs_replacement(existing: dict, desired: dict) -> bool:
    """True if a changed evaluator is locked (then online configs must be removed first)."""
    ctl = aws.client("bedrock-agentcore-control")
    for name, (level, config) in desired.items():
        if name in existing:
            g = ctl.get_evaluator(evaluatorId=existing[name]["evaluatorId"])
            if g.get("lockedForModification") and not (_same(g.get("evaluatorConfig"), config) and g.get("level") == level):
                return True
    return False


def remove_online() -> list:
    """Delete my online evaluation configs (they pin evaluators); return the modes to restore."""
    from . import online
    ctl = aws.client("bedrock-agentcore-control")
    modes = []
    for mode in online.MODES:
        x = online.find(online.config_name(mode))
        if x:
            ctl.delete_online_evaluation_config(onlineEvaluationConfigId=x["onlineEvaluationConfigId"])
            modes.append(mode)
            print(f"  ⏸  临时删除在线评估配置 {x['onlineEvaluationConfigName']}（更新被锁定的评估器需要）")
    for mode in modes:
        for _ in range(30):
            if not online.find(online.config_name(mode)):
                break
            time.sleep(3)
    return modes


def restore_online(modes: list) -> None:
    from . import online
    for mode in modes:
        print(f"  ▶️  重新创建在线评估配置（{online.LABEL[mode]}）")
        online.enable(mode)


def judge_config() -> dict | None:
    for c in JUDGE_CONFIG_CANDIDATES:
        p = EV / c
        if p.exists():
            txt = p.read_text(encoding="utf-8").replace("__JUDGE_MODEL_ID__", aws.cfg()["JUDGE_MODEL_ID"])
            cfg = json.loads(txt)
            return cfg.get("evaluatorConfig", cfg)
    return None


def register() -> dict:
    c = aws.cfg()
    print(f"▶ 部署评估器 Lambda 并注册到 AgentCore Evaluations（区域 {c['AWS_REGION']}）")
    role = lambda_role()
    arns = {k: deploy_lambda(k, role) for k in lambdas()}
    existing = list_custom()
    desired = {ev_name(s): ("TRACE", {"codeBased": {"lambdaConfig": {"lambdaArn": arns[k], "lambdaTimeoutInSeconds": 120}}})
               for s, k, _ in code_evaluators()}
    jc = judge_config()
    if jc:
        desired[ev_name("reject_quality_llm")] = ("TRACE", jc)
    else:
        print("  ℹ️  未找到原生 LLM 评审配置（evaluators/llm_judge_config/），跳过")
    removed = remove_online() if needs_replacement(existing, desired) else []
    state = {}
    for suffix, key, desc in code_evaluators():
        state[suffix] = upsert(ev_name(suffix), *desired[ev_name(suffix)], desc, existing)
    if jc:
        try:
            state["reject_quality_llm"] = upsert(ev_name("reject_quality_llm"), "TRACE", jc,
                                                 "拒绝理由与话术质量（原生 LLM-as-a-judge）", existing)
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠️  原生 LLM 评审评估器注册失败（不影响其它评估器）：{str(e)[:300]}")
    state["_lambdas"] = arns
    aws.save_state("evaluators", state)
    restore_online(removed)
    print("✅ 评估器就绪：")
    for k, v in state.items():
        if not k.startswith("_"):
            print(f"   {ev_name(k):<40} {v['evaluatorId']}")
    print("   下一步：./41-online-eval.sh enable   （开启在线评估，让分数显示在 CloudWatch 控制台）")
    return state


def show() -> int:
    ex = list_custom()
    mine = {k: v for k, v in ex.items() if k and k.startswith(f"{aws.cfg()['LAB_PREFIX']}_{aws.cfg()['LAB_SUFFIX']}_")}
    for k, v in sorted(mine.items()):
        print(f"{k:<44} {v.get('evaluatorId')}  {v.get('level', '')}  {v.get('status', '')}")
    print(f"共 {len(mine)} 个自定义评估器（另有 Builtin.* 内置评估器）")
    return 0


if __name__ == "__main__":
    sys.exit(show() if "--list" in sys.argv else (register() and 0))
