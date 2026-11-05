"""Lab configuration: one source of truth (config/lab.env) + personal overrides.

Load order (later wins): config/lab.env -> config/local.env -> process environment.
All resource names are derived here so bash and python never disagree.

CLI:
  python3 -m labkit.config --shell     # print `export K=V` lines (used by 00-config.sh)
  python3 -m labkit.config --show      # human readable table
Stdlib only and Python 3.9 compatible (runs with the EC2 system python too).
"""
from __future__ import annotations

import os
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE_FILE = ROOT / "config" / "lab.env"
LOCAL_FILE = ROOT / "config" / "local.env"
SUFFIX_RE = re.compile(r"^[a-z][a-z0-9]{1,11}$")
KEYS = [
    "AWS_REGION", "LAB_PREFIX", "LAB_SUFFIX", "AGENT_MODEL_ID", "JUDGE_MODEL_ID",
    "FALLBACK_MODEL_ID", "CODING_AGENT_MODEL_ID", "CODING_AGENT_FAST_MODEL_ID",
    "AGENT_VERSION", "TARGET_ACCURACY", "RUNTIME_PYTHON", "AWS_PROFILE",
]


class ConfigError(RuntimeError):
    pass


def parse_env_file(path: Path) -> dict:
    out: dict = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k.startswith("export "):
            k = k[len("export "):].strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k] = v
    return out


def load(require_suffix: bool = True) -> dict:
    cfg: dict = {}
    cfg.update(parse_env_file(BASE_FILE))
    cfg.update(parse_env_file(LOCAL_FILE))
    for k in KEYS:
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    suffix = cfg.get("LAB_SUFFIX", "")
    if require_suffix and not SUFFIX_RE.match(suffix or ""):
        raise ConfigError(
            "LAB_SUFFIX 未设置或格式不对（小写字母开头，2-12 位小写字母/数字）。"
            "请先运行: ./tools/set-suffix.sh <你的后缀>   例如 ./tools/set-suffix.sh zhang3"
        )
    cfg.update(derive(cfg))
    return cfg


def derive(cfg: dict) -> dict:
    p = cfg.get("LAB_PREFIX", "s12lab")
    s = cfg.get("LAB_SUFFIX", "") or "unset"
    dash, us = f"{p}-{s}", f"{p}_{s}"
    return {
        "LAB_NAME": dash,
        # AgentCore Runtime / evaluator names: [a-zA-Z][a-zA-Z0-9_]{0,47}
        "RUNTIME_NAME": f"{us}_agent",
        "RUNTIME_ROLE_NAME": f"{dash}-runtime-role",
        "EVAL_LAMBDA_NAME": f"{dash}-label-accuracy",
        "EVAL_LAMBDA_ROLE_NAME": f"{dash}-eval-lambda-role",
        "EVALUATOR_LABEL_NAME": f"{us}_label_accuracy",
        "EVALUATOR_JUDGE_NAME": f"{us}_reject_quality",
        "ONLINE_EVAL_NAME": f"{us}_online",
        "ONLINE_EVAL_ROLE_NAME": f"{dash}-online-eval-role",
        # A path (agent not on Runtime): OTel service name + CloudWatch log group
        "LOCAL_SERVICE_NAME": f"{dash}-local-agent",
        "LOCAL_LOG_GROUP": f"/aws/bedrock-agentcore/{dash}-local-agent",
        "CONVERTED_SERVICE_NAME": f"{dash}-imported-logs",
        "CONVERTED_LOG_GROUP": f"/aws/bedrock-agentcore/{dash}-imported-logs",
        # bucket also gets account id + region appended at runtime (see labkit.aws)
        "BUCKET_STEM": dash,
        "STATE_DIR": str(ROOT / ".lab-state"),
        "RESULTS_DIR": str(ROOT / "results"),
        "LAB_ROOT": str(ROOT),
    }


def main(argv: list) -> int:
    try:
        cfg = load(require_suffix="--no-suffix" not in argv)
    except ConfigError as e:
        if "--shell" in argv:
            print(f"echo {shlex.quote('❌ ' + str(e))} >&2; return 1 2>/dev/null || exit 1")
            return 0
        print(f"❌ {e}", file=sys.stderr)
        return 1
    if "--shell" in argv:
        for k in sorted(cfg):
            if k == "AWS_PROFILE" and not cfg[k]:
                continue  # empty profile would break boto3/CLI; EC2 uses the instance role
            print(f"export {k}={shlex.quote(str(cfg[k]))}")
        print("export AWS_DEFAULT_REGION=\"$AWS_REGION\"")
    else:
        w = max(len(k) for k in cfg)
        for k in KEYS + sorted(set(cfg) - set(KEYS)):
            if k in cfg:
                print(f"{k.ljust(w)}  {cfg[k]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
