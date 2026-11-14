"""Layer-1 restrictions: every Bedrock-calling role may invoke only the lab's models; nothing mints credentials.

The allowlist lives in labkit/models.py. These tests keep the CloudFormation templates (instance role + permissions
boundary), the manual-path policy, the participant console policy, the roles labkit creates at runtime and every
model id the lab is configured with in sync with it.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from labkit import aws, models  # noqa: E402
from labkit import config as labcfg  # noqa: E402
from test_ws_infra import cfn  # noqa: E402

TEMPLATES = ["ec2/adlc-ec2.yaml", "workshop-studio/static/adlc-lab.yaml"]
CRED_ACTIONS = {"iam:CreateUser", "iam:CreateAccessKey", "iam:CreateLoginProfile", "iam:UpdateLoginProfile",
                "iam:CreateServiceSpecificCredential", "iam:AttachUserPolicy", "iam:PutUserPolicy"}
INVOKE_LIKE = re.compile(r"^bedrock:(InvokeModel|InvokeModelWithResponseStream|Converse|ConverseStream|CountTokens|InvokeModel\*|\*)$")
R, A = "us-west-2", "123456789012"


def _acts(st):
    a = st.get("Action", [])
    return [a] if isinstance(a, str) else a


def _sub(v):
    """Render a !Sub ARN the way CloudFormation would in account A / Region R."""
    s = v["Fn::Sub"] if isinstance(v, dict) else v
    return s.replace("${AWS::Partition}", "aws").replace("${AWS::Region}", R).replace("${AWS::AccountId}", A)


def _res(st):
    r = st.get("Resource", [])
    return [_sub(x) for x in (r if isinstance(r, list) else [r])]


# ---------------------------------------------------------------------------------------------- the model list
def _configured_model_ids():
    env = labcfg.parse_env_file(ROOT / "config" / "lab.env")
    ids = {k: env[k] for k in ("AGENT_MODEL_ID", "JUDGE_MODEL_ID", "FALLBACK_MODEL_ID",
                               "CODING_AGENT_MODEL_ID", "CODING_AGENT_FAST_MODEL_ID")}
    setup = (ROOT / "ec2" / "setup-ec2.sh").read_text(encoding="utf-8")
    for k in ("DEFAULT_MODEL", "DEFAULT_FAST_MODEL"):
        ids[f"setup-ec2.sh {k}"] = re.search(rf'^{k}="([^"]+)"', setup, re.M).group(1)
    judge = (ROOT / "evaluators" / "adlc_reject_quality" / "judge.py").read_text(encoding="utf-8")
    for k in ("DEFAULT_MODEL", "DEFAULT_FALLBACK"):
        ids[f"judge.py {k}"] = re.search(rf'^{k} = "([^"]+)"', judge, re.M).group(1)
    core = (ROOT / "agent" / "adlc_agent" / "core.py").read_text(encoding="utf-8")
    ids["agent core default"] = re.search(r'os\.environ\.get\("AGENT_MODEL_ID", "([^"]+)"\)', core).group(1)
    return ids


def test_every_configured_model_is_allowlisted_and_every_allowlisted_model_is_used():
    ids = _configured_model_ids()
    outside = {k: v for k, v in ids.items() if v not in models.profile_ids()}
    assert not outside, f"model ids outside labkit/models.py ALLOWED_MODELS: {outside}"
    assert set(ids.values()) == set(models.profile_ids()), "an allowlisted model is used by nothing: drop it"
    assert models.ALLOWED_MODELS == ("anthropic.claude-sonnet-5-5", "anthropic.claude-haiku-5-5", "amazon.nova-2-lite-v1:0")


def test_use_model_nova_switches_to_the_allowlisted_fallback():
    s = (ROOT / "tools" / "use-model.sh").read_text(encoding="utf-8")
    assert "nova) M=$(grep '^FALLBACK_MODEL_ID=' config/lab.env" in s


def test_invoke_resources_cover_profile_and_every_route_region():
    res = models.invoke_resources(R, A)
    assert len(res) == 3 + 3 * 3 and not [x for x in res if "*" in x]
    assert f"arn:aws:bedrock:{R}:{A}:inference-profile/us.amazon.nova-2-lite-v1:0" in res
    assert "arn:aws:bedrock:us-east-2::foundation-model/anthropic.claude-haiku-5-5" in res


# ---------------------------------------------------------------------------------------------- templates
@pytest.mark.parametrize("template", TEMPLATES)
def test_instance_role_and_boundary_invoke_only_the_allowlist(template):
    t = cfn(ROOT / template)
    role = t["Resources"]["InstanceRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    boundary = t["Resources"]["LabRoleBoundary"]["Properties"]["PolicyDocument"]["Statement"]
    want = sorted(models.invoke_resources(R, A))
    for name, stmts in (("instance role", role), ("boundary", boundary)):
        invoking = [s for s in stmts if s["Effect"] == "Allow" and any(INVOKE_LIKE.match(a) for a in _acts(s))]
        assert invoking, name
        for s in invoking:
            assert not [a for a in _acts(s) if a.endswith("*")], (name, s.get("Sid"))
            assert sorted(_res(s)) == want, (name, s.get("Sid"))
        assert not [s for s in stmts if s["Effect"] == "Allow" and "bedrock:*" in _acts(s)]


@pytest.mark.parametrize("template", TEMPLATES)
def test_instance_role_and_boundary_deny_credential_creation(template):
    t = cfn(ROOT / template)
    role = t["Resources"]["InstanceRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    boundary = t["Resources"]["LabRoleBoundary"]["Properties"]["PolicyDocument"]["Statement"]
    for stmts in (role, boundary):
        (deny,) = [s for s in stmts if s.get("Sid") == "DenyCredentialCreation"]
        assert deny["Effect"] == "Deny" and deny["Resource"] == "*" and CRED_ACTIONS <= set(_acts(deny))
    assert not [s for s in role if s["Effect"] == "Allow" and set(_acts(s)) & CRED_ACTIONS]


def test_no_wildcard_model_resource_left_in_code_or_policies():
    paths = [p for g in ("labkit/*.py", "ec2/*.yaml", "ec2/*.json", "workshop-studio/static/*", "evaluators/**/*.json",
                         "evaluators/**/*.py", "agent/**/*.py") for p in ROOT.glob(g) if p.is_file()]
    bad = [str(p.relative_to(ROOT)) for p in paths
           if re.search(r"foundation-model/\*|inference-profile/\*", p.read_text(encoding="utf-8"))]
    assert not bad, bad


# ---------------------------------------------------------------------------------------------- runtime roles
@pytest.fixture
def captured_roles(monkeypatch, tmp_path):
    monkeypatch.setattr(labcfg, "LOCAL_FILE", tmp_path / "none.env")
    monkeypatch.setenv("LAB_SUFFIX", "zhang3")
    monkeypatch.setenv("AWS_REGION", R)
    aws.cfg.cache_clear()
    monkeypatch.setattr(aws, "account_id", lambda: A)
    seen = {}
    monkeypatch.setattr(aws, "ensure_role", lambda name, trust, inline, desc="": seen.setdefault(name, inline) and name)
    from labkit import evaluators_reg, online, runtime
    runtime.ensure_role()
    evaluators_reg.lambda_role()
    online.role_arn()
    yield seen
    aws.cfg.cache_clear()


def test_roles_the_lab_creates_invoke_only_the_allowlist(captured_roles):
    assert len(captured_roles) == 3  # AgentCore Runtime, evaluator Lambda, online evaluation
    want = sorted(models.invoke_resources(R, A))
    for name, inline in captured_roles.items():
        invoking = [s for s in inline["Statement"] if any(INVOKE_LIKE.match(a) for a in _acts(s))]
        assert len(invoking) == 1, name
        assert sorted(invoking[0]["Resource"]) == want and set(_acts(invoking[0])) == set(models.INVOKE_ACTIONS), name
        assert not [s for s in inline["Statement"] if set(_acts(s)) & CRED_ACTIONS or "iam:*" in _acts(s)], name


# ---------------------------------------------------------------------------------------------- other policies
def _policy(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))["Statement"]


def test_manual_path_policy_denies_everything_but_the_allowlist():
    """The manual path uses PowerUserAccess (bedrock:*), so the allowlist is a Deny with NotResource."""
    st = {s["Sid"]: s for s in _policy("ec2/manual-iam-inline-policy.json")}
    allow = st["ModelAllowlist"]
    assert allow["Effect"] == "Deny" and "bedrock:InvokeModel*" in allow["Action"]
    prof = {f"arn:aws:bedrock:*:*:inference-profile/{p}" for p in models.profile_ids()}
    fm = {x for x in models.invoke_resources(R, A) if ":foundation-model/" in x}
    assert set(allow["NotResource"]) == prof | fm
    assert st["DenyCredentialCreation"]["Effect"] == "Deny" and CRED_ACTIONS <= set(st["DenyCredentialCreation"]["Action"])


def test_participant_console_role_cannot_invoke_models_or_mint_credentials():
    """ReadOnlyAccess (checked live: no bedrock:InvokeModel*, no iam:Create*/Put*/Attach*) + static/iam-policy.json."""
    stmts = _policy("workshop-studio/static/iam-policy.json")
    allows = [a for s in stmts if s["Effect"] == "Allow" for a in _acts(s)]
    assert not [a for a in allows if a.startswith(("bedrock:", "iam:", "sts:AssumeRole"))], allows
    st = {s["Sid"]: s for s in stmts}
    assert st["NoModelInvocationFromConsole"]["Effect"] == "Deny" and "bedrock:InvokeModel*" in st["NoModelInvocationFromConsole"]["Action"]
    assert CRED_ACTIONS <= set(st["DenyCredentialCreation"]["Action"]) and st["DenyCredentialCreation"]["Effect"] == "Deny"


def test_reference_judge_policy_uses_the_allowlist():
    (inv,) = [s for s in _policy("evaluators/adlc_reject_quality/execution-role-policy.json")
              if "bedrock:InvokeModel" in s["Action"]]
    assert set(inv["Action"]) == set(models.INVOKE_ACTIONS) and not [r for r in inv["Resource"] if r.endswith("/*")]
