"""Layer-1 restrictions: every Bedrock-calling role may invoke only the lab's models; nothing mints credentials.

The allowlist lives in labkit/models.py. These tests keep the CloudFormation templates (instance role + permissions
boundary), the manual-path policy, the participant console policy, the roles labkit creates at runtime and every
model id the lab is configured with in sync with it.
"""
import fnmatch
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
                "iam:CreateServiceSpecificCredential", "iam:ResetServiceSpecificCredential", "iam:AttachUserPolicy",
                "iam:PutUserPolicy", "iam:UpdateAccessKey", "iam:UpdateServiceSpecificCredential",
                "iam:UploadSSHPublicKey", "iam:UploadSigningCertificate", "iam:AddUserToGroup", "iam:PutGroupPolicy",
                "iam:AttachGroupPolicy", "iam:CreateOpenIDConnectProvider", "iam:CreateSAMLProvider",
                "sts:GetFederationToken"}
# Allowed only on the allowlisted ARNs:
SCOPED = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock:CountTokens"]
# Other ways to make a model run (or to get a credential for it) that no lab role may be granted at all:
NEVER = ["bedrock:InvokeModelWithBidirectionalStream", "bedrock:CreateModelInvocationJob", "bedrock:InvokeAgent",
         "bedrock:InvokeInlineAgent", "bedrock:RetrieveAndGenerate", "bedrock:InvokeFlow", "bedrock:CallWithBearerToken",
         "bedrock:CreateInferenceProfile", "bedrock:CreateProvisionedModelThroughput", "bedrock:InvokeDataAutomationAsync",
         "bedrock:OptimizePrompt", "bedrock-mantle:CreateInference", "bedrock-mantle:CallWithBearerToken",
         "rolesanywhere:CreateTrustAnchor", "iam:CreateRole", "iam:PassRole"]
R, A = "us-west-2", "123456789012"


def _acts(st):
    a = st.get("Action", [])
    return [a] if isinstance(a, str) else a


def _grants(st, action):
    """Does this Allow statement grant `action` (wildcards expanded; NotAction counts as granting)?"""
    if st.get("Effect") != "Allow":
        return False
    if "NotAction" in st:
        return True
    return any(fnmatch.fnmatchcase(action.lower(), a.lower()) for a in _acts(st))


def _sub(v):
    """Render a !Sub ARN the way CloudFormation would in account A / Region R."""
    s = v["Fn::Sub"] if isinstance(v, dict) else v
    return s.replace("${AWS::Partition}", "aws").replace("${AWS::Region}", R).replace("${AWS::AccountId}", A)


def _res(st):
    r = st.get("Resource", [])
    return [_sub(x) for x in (r if isinstance(r, list) else [r])]


def _check_statements(name, stmts, allow_iam_create_role=False):
    """Every Allow that grants a SCOPED action is exactly the allowlist; no Allow grants a NEVER action."""
    want = sorted(models.invoke_resources(R, A))
    for s in stmts:
        assert "NotAction" not in s or s["Effect"] == "Deny", (name, s.get("Sid"), "Allow with NotAction")
        assert "NotResource" not in s or s["Effect"] == "Deny", (name, s.get("Sid"), "Allow with NotResource")
        if any(_grants(s, a) for a in SCOPED):
            assert sorted(_res(s)) == want, (name, s.get("Sid"), _res(s))
            assert not [a for a in _acts(s) if "*" in a and a.lower().startswith("bedrock")], (name, s.get("Sid"))
        never = [a for a in NEVER if _grants(s, a) and not (allow_iam_create_role and a.startswith("iam:"))]
        assert not never, (name, s.get("Sid"), never)


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
    role = t["Resources"]["InstanceRole"]["Properties"]
    assert [m["Fn::Sub"] if isinstance(m, dict) else m for m in role["ManagedPolicyArns"]] == \
        ["arn:${AWS::Partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"]  # no bedrock / iam grants
    policies = [p["PolicyDocument"]["Statement"] for p in role["Policies"]]
    assert len(policies) == 1
    other_iam = [r for r, v in t["Resources"].items() if v["Type"] in ("AWS::IAM::Policy", "AWS::IAM::RolePolicy",
                                                                      "AWS::IAM::User", "AWS::IAM::AccessKey")]
    assert not other_iam, other_iam
    # the instance role creates the lab roles (with the boundary), the boundary itself never grants IAM
    _check_statements("instance role", policies[0], allow_iam_create_role=True)
    _check_statements("boundary", t["Resources"]["LabRoleBoundary"]["Properties"]["PolicyDocument"]["Statement"])
    assert [s for s in policies[0] if any(_grants(s, a) for a in SCOPED)]


@pytest.mark.parametrize("template", TEMPLATES)
def test_instance_role_manages_only_its_own_lab_roles(template):
    """IAM write and PassRole only on adlc-<suffix>-* (never another participant's or a boundary-less role)."""
    st = {s.get("Sid"): s for s in cfn(ROOT / template)["Resources"]["InstanceRole"]["Properties"]["Policies"][0][
        "PolicyDocument"]["Statement"]}
    for sid in ("LabIamCreateWithBoundary", "LabIamRoles", "LabIamPassRole"):
        assert st[sid]["Resource"] == {"Fn::Sub": "arn:${AWS::Partition}:iam::${AWS::AccountId}:role/adlc-${LabSuffix}-*"}, sid
    assert st["LabIamCreateWithBoundary"]["Condition"] == {"StringEquals": {"iam:PermissionsBoundary": {"Ref": "LabRoleBoundary"}}}
    expo = st["DenyPublicExposure"]
    assert expo["Effect"] == "Deny" and set(expo["Action"]) >= {
        "lambda:CreateFunctionUrlConfig", "lambda:UpdateFunctionUrlConfig", "lambda:AddPermission",
        "bedrock-agentcore:PutResourcePolicy"}


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
    calls = sum(p.read_text(encoding="utf-8").count("aws.ensure_role(") for p in (ROOT / "labkit").glob("*.py"))
    assert calls == len(captured_roles) == 3, "a new lab-created role: add it to the captured_roles fixture"
    for name, inline in captured_roles.items():
        _check_statements(name, inline["Statement"])
        invoking = [s for s in inline["Statement"] if any(_grants(s, a) for a in SCOPED)]
        assert len(invoking) == 1 and set(_acts(invoking[0])) == set(models.INVOKE_ACTIONS), name
        assert not [s for s in inline["Statement"] if any(_grants(s, a) for a in CRED_ACTIONS)], name


# ---------------------------------------------------------------------------------------------- other policies
def _policy(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))["Statement"]


def test_manual_path_policy_denies_everything_but_the_allowlist():
    """Best effort only (PowerUserAccess + boundary-less roles can escalate, see labkit/models.py): Deny NotResource."""
    st = {s["Sid"]: s for s in _policy("ec2/manual-iam-inline-policy.json")}
    allow = st["ModelAllowlist"]
    assert allow["Effect"] == "Deny" and set(allow["Action"]) == {"bedrock:InvokeModel*", "bedrock:CountTokens"}
    prof = {f"arn:aws:bedrock:*:*:inference-profile/{p}" for p in models.profile_ids()}
    fm = {x for x in models.invoke_resources(R, A) if ":foundation-model/" in x}
    assert set(allow["NotResource"]) == prof | fm
    other = st["NoOtherModelInvocationPaths"]
    assert other["Effect"] == "Deny" and other["Resource"] == "*" and set(other["Action"]) >= {
        "bedrock:CreateModelInvocationJob", "bedrock:InvokeAgent", "bedrock:InvokeInlineAgent",
        "bedrock:RetrieveAndGenerate", "bedrock:InvokeFlow", "bedrock:CallWithBearerToken"}
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


def test_claude_code_aliases_map_onto_allowlisted_models():
    setup = (ROOT / "ec2" / "setup-ec2.sh").read_text(encoding="utf-8")
    for key, var in (("ANTHROPIC_MODEL", "$model"), ("ANTHROPIC_SMALL_FAST_MODEL", "$fast"),
                     ("ANTHROPIC_DEFAULT_HAIKU_MODEL", "$fast"), ("ANTHROPIC_DEFAULT_SONNET_MODEL", "$model"),
                     ("ANTHROPIC_DEFAULT_OPUS_MODEL", "$model"), ("CLAUDE_CODE_SUBAGENT_MODEL", "$model")):
        assert f"{key}: {var}" in setup, key
        assert f'echo "export {key}=' in setup, key


def test_use_model_rejects_ids_outside_the_allowlist(tmp_path):
    import shutil
    import subprocess
    for rel in ("tools/use-model.sh", "config/lab.env", "labkit/__init__.py", "labkit/models.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, tmp_path / rel)
    run = lambda arg: subprocess.run(["bash", "tools/use-model.sh", arg], cwd=tmp_path, capture_output=True, text=True)
    r = run("us.anthropic.claude-opus-4-8")
    assert r.returncode == 1 and "us.anthropic.claude-haiku-5-5" in r.stderr
    assert not (tmp_path / "config" / "local.env").exists()
    assert run("us.anthropic.claude-haiku-5-5").returncode == 0
    assert "us.anthropic.claude-haiku-5-5" in (tmp_path / "config" / "local.env").read_text()
    assert run("nova").returncode == 0 and "nova-2-lite" in (tmp_path / "config" / "local.env").read_text()
