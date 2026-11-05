"""Round-2 review regressions: the bash -> python config chain, boundary coverage, eval error accounting."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _shell(cmd: str, env_extra: dict) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("_S12_EXP_") and k not in (
        "AWS_REGION", "LAB_SUFFIX", "AGENT_VERSION", "AGENT_MODEL_ID", "AWS_PROFILE")}
    env.update(env_extra)
    env["S12_LOCAL_ENV"] = env_extra.get("S12_LOCAL_ENV", "/nonexistent")
    return subprocess.run(["bash", "-c", cmd], cwd=ROOT, env=env, capture_output=True, text=True).stdout


def test_n1_env_region_reaches_python_child():
    """EC2: ~/.bashrc exports AWS_REGION (instance region); scripts source 00-config.sh, then run python."""
    out = _shell('source ./00-config.sh && python3 -m labkit.config --show | grep "^AWS_REGION"',
                 {"AWS_REGION": "us-east-1", "LAB_SUFFIX": "zhang3"})
    assert "us-east-1" in out, out


def test_n1_instructor_suffix_survives_99_cleanup_pattern():
    out = _shell('export LAB_SUFFIX=instructor; source ./00-config.sh && python3 -m labkit.config --show | grep "^LAB_SUFFIX"', {})
    assert "instructor" in out, out


def test_n1_switch_version_after_sourcing_still_works(tmp_path):
    local = tmp_path / "local.env"
    local.write_text("LAB_SUFFIX=zhang3\nAGENT_VERSION=v0\n", encoding="utf-8")
    cmd = ('source ./00-config.sh && echo "AGENT_VERSION=v1" >> "$S12_LOCAL_ENV" && '
           '(source ./00-config.sh && python3 -m labkit.config --show | grep "^AGENT_VERSION")')
    out = _shell(cmd, {"S12_LOCAL_ENV": str(local)})
    assert "v1" in out, out


def test_boundary_covers_every_lab_role_action():
    """Static: inline policies of lab-created roles must stay inside LabRoleBoundary (ec2 template)."""
    tpl = (ROOT / "ec2" / "s12lab-ec2.yaml").read_text(encoding="utf-8")
    block = tpl[tpl.index("  LabRoleBoundary:"):tpl.index("\n  InstanceProfile:")]
    allowed = set(re.findall(r"-\s+([a-z0-9-]+:[A-Za-z*]+)\s*$", block, re.M))
    src = "".join((ROOT / "labkit" / f).read_text(encoding="utf-8") for f in ("runtime.py", "evaluators_reg.py", "online.py"))
    fns = re.findall(r"def (?:ensure_role|lambda_role|role_arn)\(.*?\n(?=def |\Z)", src, re.S)
    used = set()
    for body in fns:
        used |= set(re.findall(r'"((?:bedrock|bedrock-agentcore|logs|xray|cloudwatch|lambda|s3):[A-Z][A-Za-z*]*)"', body))
    used.discard("bedrock:Converse")  # not a real IAM action (authorized via InvokeModel)
    used.discard("bedrock:ConverseStream")
    missing = sorted(a for a in used if a not in allowed)
    assert used and not missing, missing
    assert "lambda:ListFunctions" in tpl and "iam:ListRoles" in tpl


def test_n5_eval_errors_block_target_verdict():
    from labkit import evaluate
    ok = {"scores": {k: {"value": 1.0, "label": "Pass"} for k in ("label_important", "label_urgent", "label_decision")}}
    bad = {"scores": {}, "error": "LimitExceededException"}
    s = evaluate.summarize([ok] + [dict(bad) for _ in range(9)], 0.7)
    assert s["eval_errors"] == 9 and s["meets_target"] is False
