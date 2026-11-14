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
                     ("ANTHROPIC_DEFAULT_OPUS_MODEL", "$model"), ("ANTHROPIC_DEFAULT_FABLE_MODEL", "$model")):
        assert f"{key}: {var}" in setup, key                                    # settings.json
        shell = "$FAST_MODEL" if var == "$fast" else "$MODEL"
        assert f'echo "export {key}=\\"{shell}\\""' in setup, key  # ~/.bashrc, same value
    assert "CLAUDE_CODE_SUBAGENT_MODEL" not in setup  # subagents follow the mapped aliases (Haiku stays Haiku)


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


# ---------------------------------------------------------------------------------------------- round-6 guards
# Representative IAM writes; a statement "grants" one when any of its (case-insensitive, wildcard) actions match.
IAM_WRITES = ["iam:CreateRole", "iam:PutRolePolicy", "iam:AttachRolePolicy", "iam:UpdateAssumeRolePolicy", "iam:PassRole",
              "iam:DeleteRole", "iam:DeleteRolePolicy", "iam:DetachRolePolicy", "iam:TagRole", "iam:CreatePolicy",
              "iam:CreatePolicyVersion", "iam:PutRolePermissionsBoundary", "iam:DeleteRolePermissionsBoundary",
              "iam:CreateUser", "iam:CreateAccessKey", "iam:UpdateRole", "iam:CreateServiceLinkedRole",
              "iam:AddRoleToInstanceProfile", "iam:CreateInstanceProfile"]
IAM_SIDS = {"LabIamCreateWithBoundary": {"iam:CreateRole", "iam:PutRolePolicy"},
            "LabIamRoles": {"iam:DeleteRole", "iam:DeleteRolePolicy", "iam:DetachRolePolicy", "iam:TagRole",
                            "iam:UpdateAssumeRolePolicy"},
            "LabIamPassRole": {"iam:PassRole"},
            "ServiceLinkedRoles": {"iam:CreateServiceLinkedRole"}}


def _walk_policies(node, path=""):
    """Every PolicyDocument anywhere in a template (inline policies, managed policies, nested)."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "PolicyDocument":
                yield path, v
            else:
                yield from _walk_policies(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_policies(v, f"{path}[{i}]")


@pytest.mark.parametrize("template", TEMPLATES)
def test_template_has_no_other_iam_principal_or_policy(template):
    t = cfn(ROOT / template)
    iam = {r: v["Type"] for r, v in t["Resources"].items() if v["Type"].startswith("AWS::IAM::")}
    assert iam == {"InstanceRole": "AWS::IAM::Role", "LabRoleBoundary": "AWS::IAM::ManagedPolicy",
                   "InstanceProfile": "AWS::IAM::InstanceProfile"}, iam
    assert not set(t["Resources"]["LabRoleBoundary"]["Properties"]) & {"Roles", "Users", "Groups"}  # never attached
    docs = list(_walk_policies(t["Resources"]))
    assert {p for p, _ in docs} == {"/InstanceRole/Properties/Policies[0]", "/LabRoleBoundary/Properties"}, docs


@pytest.mark.parametrize("template", TEMPLATES)
def test_iam_writes_only_in_the_named_statements(template):
    st = cfn(ROOT / template)["Resources"]["InstanceRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    for s in st:
        if s["Effect"] != "Allow":
            continue
        writes = {w for w in IAM_WRITES if _grants(s, w)}
        assert writes <= IAM_SIDS.get(s.get("Sid"), set()), (s.get("Sid"), writes)
    sids = {s.get("Sid"): s for s in st}
    keep = sids["KeepBoundary"]
    assert keep["Effect"] == "Deny" and keep["Resource"] == "*" and "Condition" not in keep and set(keep["Action"]) >= {
        "iam:DeleteRolePermissionsBoundary", "iam:PutRolePermissionsBoundary", "iam:AttachRolePolicy",
        "iam:CreatePolicyVersion", "iam:SetDefaultPolicyVersion"}
    prot = sids["ProtectParticipantEc2Roles"]
    assert prot["Effect"] == "Deny" and "Condition" not in prot and set(prot["Action"]) >= {
        "iam:DeleteRole", "iam:PutRolePolicy", "iam:UpdateAssumeRolePolicy", "iam:AttachRolePolicy"}
    assert sids["LabLambda"]["Resource"] == {"Fn::Sub": "arn:${AWS::Partition}:lambda:*:${AWS::AccountId}:function:adlc-${LabSuffix}-*"}


@pytest.mark.parametrize("template", TEMPLATES)
def test_deny_statements_are_unconditional_and_cover_the_exposure_paths(template):
    t = cfn(ROOT / template)
    role = {s.get("Sid"): s for s in t["Resources"]["InstanceRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]}
    boundary = {s.get("Sid"): s for s in t["Resources"]["LabRoleBoundary"]["Properties"]["PolicyDocument"]["Statement"]}
    for s in (role["DenyCredentialCreation"], role["DenyPublicExposure"], boundary["DenyCredentialCreation"]):
        assert s["Effect"] == "Deny" and s["Resource"] == "*" and "Condition" not in s
    assert set(role["DenyPublicExposure"]["Action"]) >= {
        "lambda:CreateFunctionUrlConfig", "lambda:UpdateFunctionUrlConfig", "lambda:AddPermission",
        "bedrock-agentcore:PutResourcePolicy", "bedrock-agentcore:CreateGateway*", "bedrock-agentcore:UpdateGateway*",
        "bedrock-agentcore:CreateHarness*", "bedrock-agentcore:UpdateHarness*", "s3:PutBucketPolicy", "s3:PutBucketAcl",
        "s3:PutObjectAcl", "s3:PutBucketWebsite", "s3:DeleteBucketPolicy", "logs:PutSubscriptionFilter",
        "logs:PutDestination", "logs:PutDestinationPolicy", "logs:CreateExportTask"}
    fed = boundary["DenyFederatedSessions"]
    assert fed == {"Sid": "DenyFederatedSessions", "Effect": "Deny", "Action": "*", "Resource": "*",
                   "Condition": {"Null": {"aws:FederatedProvider": "false"}}}
    assert boundary["InvokeLabEvaluators"]["Resource"] == {"Fn::Sub": "arn:${AWS::Partition}:lambda:*:${AWS::AccountId}:function:adlc-${LabSuffix}-*"}


def test_only_labkit_aws_creates_roles_and_through_ensure_role():
    py = [p for g in ("labkit/*.py", "tools/*.py", "agent/**/*.py", "evaluators/**/*.py", "data/tools/*.py")
          for p in ROOT.glob(g) if p.is_file()]
    raw = [str(p.relative_to(ROOT)) for p in py if p.name != "aws.py"
           and re.search(r"\.(create_role|put_role_policy|attach_role_policy|update_assume_role_policy)\(", p.read_text(encoding="utf-8"))]
    assert not raw, f"create IAM roles through labkit.aws.ensure_role (checked by this file): {raw}"
    text = "".join(p.read_text(encoding="utf-8") for p in py if p.name != "aws.py")
    assert not re.search(r"from \.?(labkit\.)?aws import|import labkit\.aws as|ensure_role\s*=", text)  # no aliasing
    assert len(re.findall(r"\baws\.ensure_role\(", text)) == 3  # the three lab roles of the captured_roles fixture


@pytest.mark.parametrize("bad", ["ec2", "Ws", "1ab", "a", "abcdefghijklm", "ws-x"])
def test_suffix_rule_reserves_ec2_everywhere(bad, tmp_path):
    import subprocess
    t = cfn(ROOT / "workshop-studio/static/adlc-lab.yaml")
    for tpl in [t, cfn(ROOT / "ec2/adlc-ec2.yaml")]:
        assert not re.match(tpl["Parameters"]["LabSuffix"]["AllowedPattern"], bad)
    assert not labcfg.SUFFIX_RE.match(bad)
    for good in ("ws", "zhang3", "ec2x", "wssec2"):
        assert re.match(t["Parameters"]["LabSuffix"]["AllowedPattern"], good) and labcfg.SUFFIX_RE.match(good)
    (tmp_path / "tools").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "tools" / "set-suffix.sh").write_text((ROOT / "tools" / "set-suffix.sh").read_text(encoding="utf-8"))
    r = subprocess.run(["bash", "tools/set-suffix.sh", bad], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 1 and not (tmp_path / "config" / "local.env").exists()
    # launch-from-cloudshell.sh validates its argument before any AWS call
    r = subprocess.run(["bash", str(ROOT / "ec2" / "launch-from-cloudshell.sh"), bad], cwd=tmp_path,
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin"})
    assert r.returncode == 1 and "ec2 保留" in r.stderr, r.stderr
    # setup-ec2.sh: run its suffix line on its own
    line = next(x for x in (ROOT / "ec2" / "setup-ec2.sh").read_text(encoding="utf-8").splitlines()
                if 'SUFFIX=""' in x and "[[" in x)
    for value, kept in ((bad, ""), ("zhang3", "zhang3"), ("ec2x", "ec2x")):
        out = subprocess.run(["bash", "-c", f'SUFFIX={value}; {line}; printf %s "$SUFFIX"'], capture_output=True, text=True)
        assert out.stdout == kept, (value, out.stdout)


def test_online_enable_repoints_an_existing_config_at_the_refreshed_role(monkeypatch, tmp_path):
    monkeypatch.setattr(labcfg, "LOCAL_FILE", tmp_path / "none.env")
    monkeypatch.setenv("LAB_SUFFIX", "zhang3")
    aws.cfg.cache_clear()
    from labkit import online
    seen = {}

    class Ctl:
        def update_online_evaluation_config(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(aws, "client", lambda name: Ctl())
    monkeypatch.setattr(aws, "load_state", lambda n, default=None: {"label_accuracy": {"evaluatorId": "e1"}} if n == "evaluators" else {})
    monkeypatch.setattr(aws, "save_state", lambda n, data: None)
    monkeypatch.setattr(online, "find", lambda name: {"onlineEvaluationConfigId": "cfg-1"})
    monkeypatch.setattr(online, "role_arn", lambda: "arn:aws:iam::123456789012:role/adlc-zhang3-online-eval-role-us-west-2")
    monkeypatch.setattr(online, "data_source", lambda path: {})
    try:
        online.enable("b")
    finally:
        aws.cfg.cache_clear()
    assert seen["evaluationExecutionRoleArn"].endswith("adlc-zhang3-online-eval-role-us-west-2")
