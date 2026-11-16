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
LOCAL_FILE = ROOT / "config" / "local.env"  # S12_LOCAL_ENV overrides (tests / alternate profiles)
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
    local = Path(os.environ["S12_LOCAL_ENV"]) if os.environ.get("S12_LOCAL_ENV") else LOCAL_FILE
    cfg.update(parse_env_file(local))
    origin = {k: "file" for k in cfg}
    for k in KEYS:
        v = os.environ.get(k)
        # `source ./00-config.sh` exports every FILE-derived key together with a _S12_EXP_<key>
        # snapshot. An env value equal to its snapshot is only our own earlier export, so the files
        # (e.g. 40-switch-version.sh / use-model.sh writing config/local.env) still win. Values that
        # really come from the environment (EC2 ~/.bashrc region, one-off `K=v ./script`) have no
        # snapshot, so they override the files and keep doing so in child processes.
        if v and v != os.environ.get(f"_S12_EXP_{k}"):
            cfg[k] = v
            origin[k] = "env"
    cfg["_ORIGIN"] = origin
    suffix = cfg.get("LAB_SUFFIX", "")
    if require_suffix and not SUFFIX_RE.match(suffix or ""):
        raise ConfigError(
            "LAB_SUFFIX 未设置或格式不对（小写字母开头，2-12 位小写字母/数字）。"
            "请先运行: ./tools/set-suffix.sh <你的后缀>   例如 ./tools/set-suffix.sh zhang3"
        )
    cfg.update(derive(cfg))
    return cfg


def origin_of(cfg: dict, key: str) -> str:
    return (cfg.get("_ORIGIN") or {}).get(key, "file")


def derive(cfg: dict) -> dict:
    p = cfg.get("LAB_PREFIX", "s12lab")
    s = cfg.get("LAB_SUFFIX", "") or "unset"
    r = cfg.get("AWS_REGION", "us-west-2")
    dash, us = f"{p}-{s}", f"{p}_{s}"
    return {
        "LAB_NAME": dash,
        # AgentCore Runtime / evaluator names: [a-zA-Z][a-zA-Z0-9_]{0,47}
        "RUNTIME_NAME": f"{us}_agent",
        "RUNTIME_ROLE_NAME": f"{dash}-runtime-role-{r}",
        "EVAL_LAMBDA_NAME": f"{dash}-label-accuracy",
        "EVAL_LAMBDA_ROLE_NAME": f"{dash}-eval-lambda-role-{r}",
        "EVALUATOR_LABEL_NAME": f"{us}_label_accuracy",
        "EVALUATOR_JUDGE_NAME": f"{us}_reject_quality",
        "ONLINE_EVAL_NAME": f"{us}_online",
        "ONLINE_EVAL_ROLE_NAME": f"{dash}-online-eval-role-{r}",
        # A path (agent not on Runtime): OTel service name + CloudWatch log group
        "LOCAL_SERVICE_NAME": f"{dash}-local-agent",
        "LOCAL_LOG_GROUP": f"/aws/bedrock-agentcore/{dash}-local-agent",
        "CONVERTED_SERVICE_NAME": f"{dash}-imported-logs",
        "CONVERTED_LOG_GROUP": f"/aws/bedrock-agentcore/{dash}-imported-logs",
        # IAM roles are global, so their names carry the region (policies are region-scoped);
        # the bucket also gets account id + region appended at runtime (see labkit.aws)
        "BOUNDARY_POLICY_NAME": f"{p}-boundary-{s}-{r}",
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
            if k.startswith("_"):
                continue
            if k == "AWS_PROFILE" and not cfg[k]:
                continue  # empty profile would break boto3/CLI; EC2 uses the instance role
            print(f"export {k}={shlex.quote(str(cfg[k]))}")
            if k in KEYS:
                if origin_of(cfg, k) == "file":
                    print(f"export _S12_EXP_{k}={shlex.quote(str(cfg[k]))}")
                else:
                    print(f"unset _S12_EXP_{k}")
        print("export AWS_DEFAULT_REGION=\"$AWS_REGION\"")
    else:
        w = max(len(k) for k in cfg)
        for k in KEYS + sorted(set(cfg) - set(KEYS) - {"_ORIGIN"}):
            if k in cfg:
                print(f"{k.ljust(w)}  {cfg[k]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
