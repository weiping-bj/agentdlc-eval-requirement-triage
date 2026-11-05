"""02-enable-observability: turn on CloudWatch Transaction Search (once per account+region).

Idempotent. Creates/keeps the logs resource policy that lets X-Ray write spans to the
`aws/spans` log group, switches the trace segment destination to CloudWatch Logs and
sets 100% span indexing. `--disable` reverts it (instructor-only, see 99-cleanup.sh).
"""
from __future__ import annotations

import json
import sys
import time

from . import aws

POLICY_NAME = "TransactionSearchXRayAccess"


def enable() -> int:
    c = aws.cfg()
    r, a = c["AWS_REGION"], aws.account_id()
    logs, xray = aws.client("logs"), aws.client("xray")
    d = xray.get_trace_segment_destination()
    if d.get("Destination") == "CloudWatchLogs" and d.get("Status") == "ACTIVE":
        print("✅ Transaction Search 已经开启，无需操作。")
        return 0
    doc = {"Version": "2012-10-17", "Statement": [{
        "Sid": "TransactionSearchXRayAccess", "Effect": "Allow",
        "Principal": {"Service": "xray.amazonaws.com"},
        "Action": ["logs:PutLogEvents", "logs:CreateLogStream"],
        "Resource": [f"arn:aws:logs:{r}:{a}:log-group:aws/spans:*",
                     f"arn:aws:logs:{r}:{a}:log-group:/aws/application-signals/data:*"],
        "Condition": {"ArnLike": {"aws:SourceArn": f"arn:aws:xray:{r}:{a}:*"},
                      "StringEquals": {"aws:SourceAccount": a}}}]}
    logs.put_resource_policy(policyName=POLICY_NAME, policyDocument=json.dumps(doc))
    print("  ✅ 已授权 X-Ray 写入 aws/spans")
    if d.get("Destination") != "CloudWatchLogs":
        xray.update_trace_segment_destination(Destination="CloudWatchLogs")
    try:
        xray.update_indexing_rule(Name="Default", Rule={"Probabilistic": {"DesiredSamplingPercentage": 100}})
        print("  ✅ span 索引比例设为 100%")
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️  设置索引比例失败（可稍后在控制台调整）：{e}")
    for _ in range(40):
        d = xray.get_trace_segment_destination()
        if d.get("Destination") == "CloudWatchLogs" and d.get("Status") == "ACTIVE":
            print("✅ Transaction Search 已开启。新的 trace 会写入 CloudWatch Logs 的 aws/spans。")
            return 0
        print(f"  ⏳ 等待生效… 当前状态 {d.get('Status')}")
        time.sleep(15)
    print("❌ 等待超时，请到 CloudWatch 控制台 → Application Signals → Transaction search 查看状态。")
    return 1


def disable() -> int:
    logs, xray = aws.client("logs"), aws.client("xray")
    try:
        xray.update_indexing_rule(Name="Default", Rule={"Probabilistic": {"DesiredSamplingPercentage": 1}})
    except Exception as e:  # noqa: BLE001
        print(f"  (indexing rule: {e})")
    xray.update_trace_segment_destination(Destination="XRay")
    try:
        logs.delete_resource_policy(policyName=POLICY_NAME)
    except logs.exceptions.ResourceNotFoundException:
        pass
    print("✅ Transaction Search 已关闭（trace 目的地恢复为 X-Ray），资源策略已删除。")
    return 0


if __name__ == "__main__":
    sys.exit(disable() if "--disable" in sys.argv else enable())
