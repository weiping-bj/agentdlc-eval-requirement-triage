"""boto3 helpers shared by the lab scripts."""
from __future__ import annotations

import functools
import json
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


def client(name: str):
    return session().client(name, config=_RETRY)


@functools.lru_cache(maxsize=1)
def account_id() -> str:
    return client("sts").get_caller_identity()["Account"]


def bucket_name() -> str:
    c = cfg()
    return f"{c['BUCKET_STEM']}-{account_id()}-{c['AWS_REGION']}"


def tags_dict() -> dict:
    c = cfg()
    return {"s12lab:lab": "scenario12", "s12lab:owner": c["LAB_SUFFIX"]}


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


def ensure_role(name: str, trust: dict, inline: dict, description: str = "") -> str:
    """Create (or update) an IAM role with one inline policy; return its ARN."""
    iam = client("iam")
    try:
        arn = iam.get_role(RoleName=name)["Role"]["Arn"]
        iam.update_assume_role_policy(RoleName=name, PolicyDocument=json.dumps(trust))
        created = False
    except iam.exceptions.NoSuchEntityException:
        arn = iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust),
                              Description=description[:1000], Tags=tags_list())["Role"]["Arn"]
        created = True
    iam.put_role_policy(RoleName=name, PolicyName=f"{name}-inline", PolicyDocument=json.dumps(inline))
    if created:
        time.sleep(12)  # IAM propagation before the role is assumable by a service
    return arn


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
