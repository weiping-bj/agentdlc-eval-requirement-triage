"""boto3 helpers shared by the lab scripts."""
from __future__ import annotations

import functools
import json
import threading
import time

import boto3
from botocore.config import Config

from . import config as labcfg

_RETRY = Config(retries={"max_attempts": 8, "mode": "adaptive"}, read_timeout=300, connect_timeout=20)


@functools.lru_cache(maxsize=1)
def cfg() -> dict:
    return labcfg.load()


@functools.lru_cache(maxsize=1)
def session() -> boto3.Session:
    c = cfg()
    return boto3.Session(profile_name=c.get("AWS_PROFILE") or None, region_name=c["AWS_REGION"])


_CLIENTS: dict = {}
_LOCK = threading.Lock()


def client(name: str):
    """One cached client per service: boto3 clients are thread-safe, Session objects are not,
    so clients are created once under a lock and then shared by the worker threads."""
    with _LOCK:
        if name not in _CLIENTS:
            _CLIENTS[name] = session().client(name, config=_RETRY)
        return _CLIENTS[name]


@functools.lru_cache(maxsize=1)
def account_id() -> str:
    return client("sts").get_caller_identity()["Account"]


def bucket_name() -> str:
    c = cfg()
    return f"{c['BUCKET_STEM']}-{account_id()}-{c['AWS_REGION']}"


def tags_dict() -> dict:
    c = cfg()
    return {"adlc:lab": "agentdlc", "adlc:owner": c["LAB_SUFFIX"]}


def tags_list(key_name: str = "Key", value_name: str = "Value") -> list:
    return [{key_name: k, value_name: v} for k, v in tags_dict().items()]


def wait_until(fn, desc: str, timeout: int = 600, interval: int = 10):
    """Poll fn() until it returns a truthy value; raise TimeoutError otherwise."""
    start = time.time()
    while True:
        res = fn()
        if res:
            return res
        if time.time() - start > timeout:
            raise TimeoutError(f"timed out waiting for {desc}")
        time.sleep(interval)


MANAGED_TAG = ("adlc:managed-by", "cloudformation")


def stack_managed(role: dict) -> bool:
    """A lab role pre-created by the EC2 CloudFormation stack (its trust and permissions are fixed there)."""
    return any(t.get("Key") == MANAGED_TAG[0] and t.get("Value") == MANAGED_TAG[1] for t in role.get("Tags") or [])


def ensure_role(name: str, trust: dict, inline: dict, description: str = "") -> str:
    """The ARN of lab role `name`.

    On the lab EC2 the stack pre-creates the three lab roles and the EC2 has no IAM write permissions, so the role
    is used as is (nothing can re-trust it). Off the lab EC2 (a laptop with admin credentials, no stack) the role
    is created or updated here with `trust` and one inline policy, with the lab boundary when one exists."""
    iam = client("iam")
    try:
        role = iam.get_role(RoleName=name)["Role"]
    except iam.exceptions.NoSuchEntityException:
        role = None
    if role is not None and stack_managed(role):
        return role["Arn"]
    if "assumed-role/adlc-ec2-" in caller_arn():
        raise SystemExit(f"❌ 找不到由 EC2 堆栈预先创建的角色 {name}（实验 EC2 不能自己创建或修改 IAM 角色）。"
                         f"请确认个人后缀与 EC2 堆栈一致（当前 {cfg()['LAB_SUFFIX']}），区域与 EC2 所在区域一致"
                         f"（当前 {cfg()['AWS_REGION']}）。")
    if role is not None:
        arn = role["Arn"]
        iam.update_assume_role_policy(RoleName=name, PolicyDocument=json.dumps(trust))
        created = False
    else:
        kw = {}
        b = boundary_arn()
        if b:
            kw["PermissionsBoundary"] = b
        arn = iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust),
                              Description=description[:1000], Tags=tags_list(), **kw)["Role"]["Arn"]
        created = True
    iam.put_role_policy(RoleName=name, PolicyName=f"{name}-inline", PolicyDocument=json.dumps(inline))
    if created:
        time.sleep(12)  # IAM propagation before the role is assumable by a service
    return arn


@functools.lru_cache(maxsize=1)
def caller_arn() -> str:
    return client("sts").get_caller_identity()["Arn"]


@functools.lru_cache(maxsize=1)
def boundary_arn():
    """The lab permissions boundary created by the EC2 CloudFormation stack (if any): roles created from a
    laptop in an account that has the stack get it too; without a stack, roles are created without one."""
    arn = f"arn:aws:iam::{account_id()}:policy/{cfg()['BOUNDARY_POLICY_NAME']}"
    try:
        client("iam").get_policy(PolicyArn=arn)
        return arn
    except Exception:  # noqa: BLE001 - NoSuchEntity or AccessDenied: no boundary in use
        return None


def save_state(name: str, data: dict) -> None:
    from pathlib import Path
    d = Path(cfg()["STATE_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_state(name: str, default=None):
    from pathlib import Path
    p = Path(cfg()["STATE_DIR"]) / f"{name}.json"
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))
