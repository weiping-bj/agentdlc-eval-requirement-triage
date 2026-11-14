"""11-enable-observability: turn on CloudWatch Transaction Search (once per account+region).

Idempotent. Creates/keeps the logs resource policy that lets X-Ray write spans to the
`aws/spans` log group, switches the trace segment destination to CloudWatch Logs and
sets 100% span indexing. `--disable` reverts it (instructor-only, see 99-cleanup.sh).
`--verify [--wait N]` only checks: destination CloudWatchLogs/ACTIVE (polled up to N s), a logs
resource policy that lets X-Ray write aws/spans, and the Default indexing rule. The last line is an
ASCII summary (the Workshop Studio template fails the stack with it, and CloudFormation shows
non-ASCII text as "?").
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import sys
import time

from . import aws

POLICY_NAME = "TransactionSearchXRayAccess"


def _policy_doc(r: str, a: str) -> str:
    return json.dumps({"Version": "2012-10-17", "Statement": [{
        "Sid": "TransactionSearchXRayAccess", "Effect": "Allow",
        "Principal": {"Service": "xray.amazonaws.com"},
        "Action": ["logs:PutLogEvents", "logs:CreateLogStream"],
        "Resource": [f"arn:aws:logs:{r}:{a}:log-group:aws/spans:*",
                     f"arn:aws:logs:{r}:{a}:log-group:/aws/application-signals/data:*"],
        "Condition": {"ArnLike": {"aws:SourceArn": f"arn:aws:xray:{r}:{a}:*"},
                      "StringEquals": {"aws:SourceAccount": a}}}]})


def enable() -> int:
    """Turn Transaction Search on, or complete it: when the destination is already CloudWatch Logs only a
    missing resource policy / indexing rule is added. `enabled_by_lab` is True only when THIS lab switched
    the destination (saved right after that call succeeded), so cleanup --account-level never reverts a
    setting that was on before."""
    c = aws.cfg()
    r, a = c["AWS_REGION"], aws.account_id()
    logs, xray = aws.client("logs"), aws.client("xray")
    d = xray.get_trace_segment_destination()
    already = d.get("Destination") == "CloudWatchLogs"  # ACTIVE, or PENDING: someone is turning it on
    has_policy = already and _xray_policy(logs, r, a) is not None
    pct = _indexing_percent(xray) if already else None
    if already and d.get("Status") == "ACTIVE" and has_policy and pct:
        print("✅ Transaction Search 已经开启，无需操作。")
        if not aws.load_state("observability"):
            aws.save_state("observability", {"enabled_by_lab": False, "region": r})
        return 0
    if not has_policy:
        logs.put_resource_policy(policyName=POLICY_NAME, policyDocument=_policy_doc(r, a))
        print("  ✅ 已授权 X-Ray 写入 aws/spans")
    if not already:
        xray.update_trace_segment_destination(Destination="CloudWatchLogs")
        aws.save_state("observability", {"enabled_by_lab": True, "region": r,
                                         "previous_destination": d.get("Destination")})
    elif not aws.load_state("observability"):
        aws.save_state("observability", {"enabled_by_lab": False, "region": r})
    if not pct:
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
        xray.update_indexing_rule(Name="Default", Rule={"Probabilistic": {"DesiredSamplingPercentage": 0}})
    except Exception as e:  # noqa: BLE001
        print(f"  (indexing rule: {e})")
    xray.update_trace_segment_destination(Destination="XRay")
    try:
        logs.delete_resource_policy(policyName=POLICY_NAME)
    except logs.exceptions.ResourceNotFoundException:
        pass
    print("✅ Transaction Search 已关闭（trace 目的地恢复为 X-Ray），资源策略已删除。")
    return 0


def _as_list(v):
    return [v] if isinstance(v, str) else (v if isinstance(v, list) else [])


def _lets_xray_write_spans(st, r: str, a: str) -> bool:
    """One policy statement allows X-Ray to PutLogEvents into this account's aws/spans log group."""
    if not isinstance(st, dict) or st.get("Effect") != "Allow" or not isinstance(st.get("Principal"), dict):
        return False
    if "xray.amazonaws.com" not in _as_list(st["Principal"].get("Service")):
        return False
    if not any(fnmatch.fnmatchcase("logs:PutLogEvents", x) for x in _as_list(st.get("Action"))):
        return False
    stream = f"arn:aws:logs:{r}:{a}:log-group:aws/spans:log-stream:x"
    return any(fnmatch.fnmatchcase(stream, x) for x in _as_list(st.get("Resource")))


def _xray_policy(logs, r: str, a: str):
    """Name of a logs resource policy that lets X-Ray write to aws/spans (any name: console and API differ)."""
    kw: dict = {}
    while True:
        resp = logs.describe_resource_policies(**kw)
        for p in resp.get("resourcePolicies", []):
            try:
                doc = json.loads(p.get("policyDocument") or "{}")
            except ValueError:
                continue
            stmts = doc.get("Statement", []) if isinstance(doc, dict) else []
            if any(_lets_xray_write_spans(st, r, a) for st in (stmts if isinstance(stmts, list) else [stmts])):
                return p.get("policyName")
        if not resp.get("nextToken"):
            return None
        kw = {"nextToken": resp["nextToken"]}


def _indexing_percent(xray):
    """DesiredSamplingPercentage of the Default indexing rule (None when there is no such rule)."""
    for rule in xray.get_indexing_rules().get("IndexingRules", []):
        if rule.get("Name") == "Default":
            return ((rule.get("Rule") or {}).get("Probabilistic") or {}).get("DesiredSamplingPercentage")
    return None


def verify(wait: int = 600, interval: int = 15) -> int:
    """Transaction Search usable? 0 = yes. Polls the destination up to `wait` seconds, then checks the
    resource policy and indexing rule. Prints the Chinese result, then one ASCII summary line last
    (observed status on failure)."""
    logs, xray = aws.client("logs"), aws.client("xray")
    r, a = aws.cfg()["AWS_REGION"], aws.account_id()
    deadline = time.monotonic() + max(wait, 0)
    while True:
        d = xray.get_trace_segment_destination()
        dest, status = d.get("Destination"), d.get("Status")
        if dest == "CloudWatchLogs" and status == "ACTIVE":
            break
        if time.monotonic() >= deadline:
            print(f"❌ Transaction Search 未就绪（等待 {wait}s）：Destination={dest} Status={status}")
            print(f"transaction-search NOT ready after {wait}s: Destination={dest} Status={status}")
            return 1
        print(f"  ⏳ Transaction Search Destination={dest} Status={status}，{interval}s 后重查")
        time.sleep(interval)
    pol, pct = _xray_policy(logs, r, a), _indexing_percent(xray)
    st = aws.load_state("observability") or {}
    who = "本实验开启" if st.get("enabled_by_lab") else "实验前已开启"
    idx = "none" if pct is None else format(pct, "g") + "%"
    summary = (f"Destination=CloudWatchLogs Status=ACTIVE · 资源策略 {pol or '无'} · "
               f"索引 Default {'无' if pct is None else idx} · {who}")
    ascii_ = (f"Destination=CloudWatchLogs Status=ACTIVE policy={pol or 'none'} indexing-default={idx} "
              f"enabled-by-lab={'yes' if st.get('enabled_by_lab') else 'no'}")
    if not pol or not pct:
        print(f"❌ Transaction Search 配置不完整：{summary}")
        print(f"transaction-search incomplete: {ascii_}")
        return 1
    print(f"✅ Transaction Search 已就绪：{summary}")
    print(f"transaction-search ready: {ascii_}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="CloudWatch Transaction Search for the lab")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--disable", action="store_true", help="讲师：还原（trace 目的地改回 X-Ray）")
    g.add_argument("--verify", action="store_true", help="只检查是否已就绪（不做修改）")
    ap.add_argument("--wait", type=int, default=600, help="--verify 时等待 ACTIVE 的最长秒数")
    a = ap.parse_args(argv)
    if a.disable:
        return disable()
    if a.verify:
        return verify(a.wait)
    return enable()


if __name__ == "__main__":
    sys.exit(main())
