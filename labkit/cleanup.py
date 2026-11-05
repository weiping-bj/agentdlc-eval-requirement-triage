"""Chapter 10: delete every lab resource and prove nothing is left.

  python -m labkit.cleanup                 # my resources (prefix s12lab, my suffix)
  python -m labkit.cleanup --all           # instructor: every s12lab* resource in this account+region
  python -m labkit.cleanup --all --account-level   # + revert Transaction Search, delete aws/spans
  python -m labkit.cleanup --verify-only   # only list what is left
Deletion order respects dependencies: online eval configs -> evaluators -> Lambdas ->
Runtime -> S3 -> log groups -> IAM roles. The EC2 CloudFormation stack is deleted by --all
(participants delete their own stack in the console, step 10.3).
"""
from __future__ import annotations

import argparse
import sys
import time

from . import aws
from . import config as labcfg


def _pages(fn, key, **kw):
    token = None
    while True:
        if token:
            kw["nextToken"] = token
        r = fn(**kw)
        yield from r.get(key, [])
        token = r.get("nextToken")
        if not token:
            return


class Cleaner:
    def __init__(self, all_suffixes: bool, dry: bool):
        c = labcfg.load(require_suffix=not all_suffixes)
        self.c = c
        p = c["LAB_PREFIX"]
        self.us = f"{p}_" if all_suffixes else f"{p}_{c['LAB_SUFFIX']}_"
        self.dash = f"{p}-" if all_suffixes else f"{p}-{c['LAB_SUFFIX']}-"
        self.all = all_suffixes
        self.dry = dry
        self.ctl = aws.client("bedrock-agentcore-control")

    def act(self, what: str, fn, *a, **kw):
        print(("  [dry-run] " if self.dry else "  🗑  ") + what)
        if self.dry:
            return
        try:
            fn(*a, **kw)
        except Exception as e:  # noqa: BLE001 - keep going, verify() reports leftovers
            print(f"     ⚠️  {type(e).__name__}: {str(e)[:200]}")

    # ---- listers (also used by verify) ----
    def online_configs(self):
        return [x for x in _pages(self.ctl.list_online_evaluation_configs, "onlineEvaluationConfigs", maxResults=100)
                if x.get("onlineEvaluationConfigName", "").startswith(self.us)]

    def evaluators(self):
        return [x for x in _pages(self.ctl.list_evaluators, "evaluators", maxResults=100)
                if (x.get("evaluatorName") or "").startswith(self.us)]

    def runtimes(self):
        return [x for x in _pages(self.ctl.list_agent_runtimes, "agentRuntimes", maxResults=100)
                if x.get("agentRuntimeName", "").startswith(self.us)]

    def lambdas(self):
        lam = aws.client("lambda")
        out = []
        for page in lam.get_paginator("list_functions").paginate():
            out += [f for f in page["Functions"] if f["FunctionName"].startswith(self.dash)]
        return out

    def buckets(self):
        return [b["Name"] for b in aws.client("s3").list_buckets().get("Buckets", []) if b["Name"].startswith(self.dash)]

    def roles(self):
        iam = aws.client("iam")
        out = []
        for page in iam.get_paginator("list_roles").paginate():
            out += [r["RoleName"] for r in page["Roles"] if r["RoleName"].startswith(self.dash)]
        return out

    def log_groups(self, runtime_ids=()):
        logs = aws.client("logs")
        prefixes = [f"/aws/bedrock-agentcore/{self.dash}", f"/aws/lambda/{self.dash}"]
        prefixes += [f"/aws/bedrock-agentcore/runtimes/{rid}" for rid in runtime_ids]
        if self.all:
            prefixes += ["/aws/bedrock-agentcore/runtimes/" + self.us]
        out = []
        for pre in prefixes:
            for page in logs.get_paginator("describe_log_groups").paginate(logGroupNamePrefix=pre):
                out += [g["logGroupName"] for g in page["logGroups"]]
        # evaluation result groups are named by config id; match via our saved/listed config ids
        return sorted(set(out))

    def eval_result_groups(self, config_ids):
        logs = aws.client("logs")
        out = []
        for cid in config_ids:
            for page in logs.get_paginator("describe_log_groups").paginate(
                    logGroupNamePrefix=f"/aws/bedrock-agentcore/evaluations/results/{cid}"):
                out += [g["logGroupName"] for g in page["logGroups"]]
        return out

    def stacks(self):
        cfn = aws.client("cloudformation")
        out = []
        for page in cfn.get_paginator("list_stacks").paginate(StackStatusFilter=[
                "CREATE_COMPLETE", "UPDATE_COMPLETE", "ROLLBACK_COMPLETE", "CREATE_FAILED", "UPDATE_ROLLBACK_COMPLETE",
                "DELETE_FAILED", "CREATE_IN_PROGRESS", "UPDATE_IN_PROGRESS"]):
            out += [s["StackName"] for s in page["StackSummaries"] if s["StackName"].startswith("s12lab-ec2-")]
        if not self.all:
            out = [s for s in out if s == f"s12lab-ec2-{self.c['LAB_SUFFIX']}"]
        return out

    # ---- delete ----
    def run(self, account_level: bool, delete_stacks: bool) -> None:
        print(f"▶ 清理范围：{'本账号所有 s12lab 资源' if self.all else '我的资源 ' + self.dash + '*'} · 区域 {self.c['AWS_REGION']}")
        cfgs = self.online_configs()
        cfg_ids = [x["onlineEvaluationConfigId"] for x in cfgs]
        result_groups = self.eval_result_groups(cfg_ids)
        for x in cfgs:
            cid = x["onlineEvaluationConfigId"]
            self.act(f"停用在线评估 {x['onlineEvaluationConfigName']}", self.ctl.update_online_evaluation_config,
                     onlineEvaluationConfigId=cid, executionStatus="DISABLED")
            self.act(f"删除在线评估 {x['onlineEvaluationConfigName']}", self.ctl.delete_online_evaluation_config,
                     onlineEvaluationConfigId=cid)
        if cfgs and not self.dry:
            time.sleep(10)
        for e in self.evaluators():
            self.act(f"删除评估器 {e['evaluatorName']}", self.ctl.delete_evaluator, evaluatorId=e["evaluatorId"])
        lam = aws.client("lambda")
        for f in self.lambdas():
            self.act(f"删除 Lambda {f['FunctionName']}", lam.delete_function, FunctionName=f["FunctionName"])
        rts = self.runtimes()
        rids = [r["agentRuntimeId"] for r in rts]
        for r in rts:
            self.act(f"删除 Runtime {r['agentRuntimeName']} ({r['agentRuntimeId']})", self.ctl.delete_agent_runtime,
                     agentRuntimeId=r["agentRuntimeId"])
        s3 = aws.session().resource("s3")
        for b in self.buckets():
            def empty_and_delete(name=b):
                bk = s3.Bucket(name)
                bk.object_versions.delete()
                bk.objects.all().delete()
                bk.delete()
            self.act(f"清空并删除 S3 桶 {b}", empty_and_delete)
        logs = aws.client("logs")
        for g in self.log_groups(rids) + result_groups:
            self.act(f"删除日志组 {g}", logs.delete_log_group, logGroupName=g)
        iam = aws.client("iam")
        for rn in self.roles():
            def del_role(name=rn):
                for pn in iam.list_role_policies(RoleName=name).get("PolicyNames", []):
                    iam.delete_role_policy(RoleName=name, PolicyName=pn)
                for ap in iam.list_attached_role_policies(RoleName=name).get("AttachedPolicies", []):
                    iam.detach_role_policy(RoleName=name, PolicyArn=ap["PolicyArn"])
                for ip in iam.list_instance_profiles_for_role(RoleName=name).get("InstanceProfiles", []):
                    if ip["InstanceProfileName"].startswith("s12lab"):
                        iam.remove_role_from_instance_profile(InstanceProfileName=ip["InstanceProfileName"], RoleName=name)
                iam.delete_role(RoleName=name)
            if rn.startswith("s12lab-ec2-"):
                continue  # owned by the EC2 CloudFormation stack
            self.act(f"删除 IAM 角色 {rn}", del_role)
        if delete_stacks:
            cfn = aws.client("cloudformation")
            for s in self.stacks():
                self.act(f"删除 CloudFormation 堆栈 {s}（EC2）", cfn.delete_stack, StackName=s)
                if not self.dry:
                    try:
                        cfn.get_waiter("stack_delete_complete").wait(StackName=s, WaiterConfig={"Delay": 15, "MaxAttempts": 60})
                    except Exception as e:  # noqa: BLE001
                        print(f"     ⚠️  等待堆栈删除失败：{e}")
        if account_level:
            from . import observability
            if not self.dry:
                observability.disable()
            self.act("删除 aws/spans 日志组（账号级，所有人的 trace）", logs.delete_log_group, logGroupName="aws/spans")
        if not self.dry and not self.all:
            st = aws.cfg()["STATE_DIR"]
            import shutil
            for n in ("runtime", "evaluators", "online"):
                try:
                    (__import__("pathlib").Path(st) / f"{n}.json").unlink()
                except FileNotFoundError:
                    pass
            del shutil

    def verify(self) -> int:
        print(f"\n▶ 残留检查（{self.c['AWS_REGION']}，前缀 {self.us}* / {self.dash}*）")
        checks = [
            ("在线评估配置", [x["onlineEvaluationConfigName"] for x in self.online_configs()]),
            ("自定义评估器", [x["evaluatorName"] for x in self.evaluators()]),
            ("AgentCore Runtime", [x["agentRuntimeName"] for x in self.runtimes()]),
            ("Lambda 函数", [x["FunctionName"] for x in self.lambdas()]),
            ("S3 桶", self.buckets()),
            ("IAM 角色", self.roles()),
            ("日志组", self.log_groups()),
            ("EC2 堆栈", self.stacks()),
        ]
        left = 0
        for name, xs in checks:
            print(f"  {'✅' if not xs else '❌'} {name}: {len(xs)} {xs if xs else ''}")
            left += len(xs)
        print("✅ 没有残留的实验资源" if not left else f"❌ 仍有 {left} 个资源（删除中的 Runtime/堆栈可稍后重查）")
        return 0 if not left else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--account-level", action="store_true")
    ap.add_argument("--stacks", action="store_true", help="also delete EC2 stacks (default with --all)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify-only", action="store_true")
    a = ap.parse_args(argv)
    cl = Cleaner(a.all, a.dry_run)
    if not a.verify_only:
        cl.run(a.account_level, a.stacks or a.all)
        if not a.dry_run:
            time.sleep(5)
    return cl.verify()


if __name__ == "__main__":
    sys.exit(main())
