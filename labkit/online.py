"""Online evaluation config: lets AgentCore Evaluations score new sessions automatically and
write the scores to CloudWatch, so they show in GenAI Observability > agent > Evaluations.

  python -m labkit.online enable  --path a|b     # create (or re-enable) the config for your path
  python -m labkit.online disable --path a|b     # needed before updating/deleting evaluators
  python -m labkit.online status
"""
from __future__ import annotations

import argparse
import sys

from . import aws

ONLINE_EVALUATORS = ["label_accuracy", "label_important", "label_urgent", "label_decision", "reject_quality"]
ONLINE_BUILTINS = ["Builtin.Helpfulness"]


def role_arn() -> str:
    c, a, r = aws.cfg(), aws.account_id(), aws.cfg()["AWS_REGION"]
    trust = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": "bedrock-agentcore.amazonaws.com"}, "Action": "sts:AssumeRole",
        "Condition": {"StringEquals": {"aws:SourceAccount": a, "aws:ResourceAccount": a},
                      "ArnLike": {"aws:SourceArn": [f"arn:aws:bedrock-agentcore:{r}:{a}:evaluator/*",
                                                    f"arn:aws:bedrock-agentcore:{r}:{a}:online-evaluation-config/*"]}}}]}
    inline = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["logs:DescribeLogGroups", "logs:GetQueryResults", "logs:StartQuery",
                                       "logs:StopQuery", "logs:FilterLogEvents", "logs:GetLogEvents",
                                       "logs:DescribeLogStreams", "logs:DescribeIndexPolicies",
                                       "logs:DescribeFieldIndexes"], "Resource": "*"},
        {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
         "Resource": f"arn:aws:logs:{r}:{a}:log-group:/aws/bedrock-agentcore/evaluations/*"},
        {"Effect": "Allow", "Action": ["logs:PutIndexPolicy"],
         "Resource": [f"arn:aws:logs:{r}:{a}:log-group:aws/spans", f"arn:aws:logs:{r}:{a}:log-group:aws/spans:*",
                      f"arn:aws:logs:{r}:{a}:log-group:/aws/bedrock-agentcore/*"]},
        {"Effect": "Allow", "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream",
                                       "bedrock:Converse", "bedrock:ConverseStream"],
         "Resource": ["arn:aws:bedrock:*::foundation-model/*", f"arn:aws:bedrock:*:{a}:inference-profile/*"]},
        {"Effect": "Allow", "Action": ["lambda:InvokeFunction", "lambda:GetFunction"],
         "Resource": [f"arn:aws:lambda:{r}:{a}:function:{c['LAB_NAME']}-eval-*"]},
        {"Effect": "Allow", "Action": ["cloudwatch:PutMetricData"], "Resource": "*"},
    ]}
    return aws.ensure_role(c["ONLINE_EVAL_ROLE_NAME"], trust, inline, "s12lab online evaluation role")


def config_name(path: str) -> str:
    return f"{aws.cfg()['ONLINE_EVAL_NAME']}_{path}"


def data_source(path: str) -> dict:
    c = aws.cfg()
    if path == "a":
        return {"cloudWatchLogs": {"logGroupNames": ["aws/spans", c["LOCAL_LOG_GROUP"]],
                                   "serviceNames": [c["LOCAL_SERVICE_NAME"]]}}
    st = aws.load_state("runtime")
    if not st:
        raise SystemExit("❌ 还没有部署 Runtime：先运行 ./20-B-deploy-runtime.sh")
    return {"cloudWatchLogs": {"logGroupNames": ["aws/spans", st["logGroup"]], "serviceNames": [st["serviceName"]]}}


def find(name: str):
    ctl = aws.client("bedrock-agentcore-control")
    token = None
    while True:
        kw = {"maxResults": 100}
        if token:
            kw["nextToken"] = token
        r = ctl.list_online_evaluation_configs(**kw)
        for x in r.get("onlineEvaluationConfigs", []):
            if x.get("onlineEvaluationConfigName") == name:
                return x
        token = r.get("nextToken")
        if not token:
            return None


def enable(path: str) -> int:
    ctl = aws.client("bedrock-agentcore-control")
    ev = aws.load_state("evaluators") or {}
    if not ev:
        raise SystemExit("❌ 先运行 ./30-create-evaluators.sh 注册评估器")
    evaluators = [{"evaluatorId": ev[k]["evaluatorId"]} for k in ONLINE_EVALUATORS if k in ev]
    evaluators += [{"evaluatorId": b} for b in ONLINE_BUILTINS]
    name = config_name(path)
    existing = find(name)
    if existing:
        cid = existing["onlineEvaluationConfigId"]
        ctl.update_online_evaluation_config(onlineEvaluationConfigId=cid, executionStatus="ENABLED",
                                            evaluators=evaluators, dataSourceConfig=data_source(path))
        print(f"✅ 在线评估配置 {name} 已启用（{cid}）")
    else:
        import time
        role = role_arn()
        for attempt in range(8):
            try:
                r = ctl.create_online_evaluation_config(
                    onlineEvaluationConfigName=name,
                    description=f"s12lab online evaluation (path {path.upper()})",
                    rule={"samplingConfig": {"samplingPercentage": 100.0}, "sessionConfig": {"sessionTimeoutMinutes": 2}},
                    dataSourceConfig=data_source(path), evaluators=evaluators,
                    evaluationExecutionRoleArn=role, enableOnCreate=True, tags=aws.tags_dict())
                break
            except (ctl.exceptions.ValidationException, ctl.exceptions.ConflictException) as e:
                # fresh IAM permissions / a just-deleted config with the same name need a moment
                if attempt < 7 and ("permission" in str(e).lower() or "exist" in str(e).lower()
                                    or isinstance(e, ctl.exceptions.ConflictException)):
                    print(f"  ⏳ 等待 IAM 权限生效后重试（{attempt + 1}/7）")
                    time.sleep(10)
                    continue
                raise
        cid = r["onlineEvaluationConfigId"]
        print(f"✅ 已创建在线评估配置 {name}（{cid}），采样 100%，session 空闲 2 分钟后打分")
    st = aws.load_state("online", default={})
    st[path] = {"name": name, "id": cid}
    aws.save_state("online", st)
    print("   之后每个新 session 都会自动评分，结果写入 CloudWatch：")
    print("   CloudWatch 控制台 → GenAI Observability → Bedrock AgentCore → 你的 Agent → Evaluations")
    return 0


def disable(path: str) -> int:
    ctl = aws.client("bedrock-agentcore-control")
    x = find(config_name(path))
    if not x:
        print("ℹ️  没有找到在线评估配置，无需停用"); return 0
    ctl.update_online_evaluation_config(onlineEvaluationConfigId=x["onlineEvaluationConfigId"], executionStatus="DISABLED")
    print(f"✅ 已停用 {config_name(path)}（可以更新评估器了；之后用 enable 重新启用）")
    return 0


def status() -> int:
    for p in ("a", "b"):
        x = find(config_name(p))
        if x:
            print(f"{p.upper()}: {x.get('onlineEvaluationConfigName')} {x.get('onlineEvaluationConfigId')} "
                  f"status={x.get('status')} execution={x.get('executionStatus')}")
        else:
            print(f"{p.upper()}: 未创建")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["enable", "disable", "status"])
    ap.add_argument("--path", choices=["a", "b"], default="b")
    a = ap.parse_args(argv)
    return {"enable": enable, "disable": disable}.get(a.cmd, lambda p: status())(a.path)


if __name__ == "__main__":
    sys.exit(main())
