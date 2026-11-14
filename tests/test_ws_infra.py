"""Workshop Studio delivery: lab bundle (tools/ws_bundle.py), static/adlc-lab.yaml and contentspec.yaml.

Offline only. The WS template is derived from ec2/adlc-ec2.yaml; these tests keep the shared parts identical
(network, security group, instance role, permissions boundary) so the two delivery paths cannot drift.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WS = ROOT / "workshop-studio"
WS_TPL = WS / "static" / "adlc-lab.yaml"
EC2_TPL = ROOT / "ec2" / "adlc-ec2.yaml"


def _load_bundle_module():
    spec = importlib.util.spec_from_file_location("ws_bundle", ROOT / "tools" / "ws_bundle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wsb = _load_bundle_module()


class CfnLoader(yaml.SafeLoader):
    pass


def _cfn_tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        v = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        v = loader.construct_sequence(node, deep=True)
    else:
        v = loader.construct_mapping(node, deep=True)
    return {"Ref" if suffix == "Ref" else f"Fn::{suffix}": v}


CfnLoader.add_multi_constructor("!", _cfn_tag)


def cfn(path):
    return yaml.load(path.read_text(encoding="utf-8"), Loader=CfnLoader)


# --------------------------------------------------------------------------- bundle
@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    out = tmp_path_factory.mktemp("bundle") / "adlc-lab.zip"
    assert wsb.main(["--out", str(out)]) == 0
    return out


def _names(zpath):
    with zipfile.ZipFile(zpath) as z:
        return {i.filename: i for i in z.infolist()}


def test_bundle_has_what_setup_and_the_main_lab_path_need(bundle):
    names = _names(bundle)
    tracked = {rel for rel, _ in wsb.tracked()}
    steps = {rel for rel in tracked if "/" not in rel and rel.endswith(".sh")}
    labkit = {rel for rel in tracked if rel.startswith("labkit/")}
    need = steps | labkit | {
        "requirements.txt", "agent/requirements.txt", "config/lab.env", "ec2/setup-ec2.sh", "LICENSE",
        "agent/main.py", "agent/adlc_agent/prompts/v0.md", "agent/adlc_agent/prompts/v1.md",
        "tools/set-suffix.sh", "tools/use-model.sh", "telemetry/otel_env.sh",
        "docs/assets/style.css",                       # labkit.compare inlines it into the compare page
        "evaluators/sync_shared.py", wsb.MANIFEST,
    }
    assert {"01-check-env.sh", "02-enable-observability.sh", "20-B-deploy-runtime.sh", "21-B-run-golden.sh",
            "30-create-evaluators.sh", "31-run-eval.sh", "99-cleanup.sh"} <= steps
    missing = sorted(need - set(names))
    assert not missing, missing
    assert any(n.startswith("data/golden/") for n in names) and any(n.startswith("evaluators/tests/") for n in names)


def test_bundle_keeps_exec_bits_and_unix_modes(bundle):
    for name, info in _names(bundle).items():
        mode = (info.external_attr >> 16) & 0o777
        assert info.create_system == 3, name
        if name.endswith(".sh") and name != "telemetry/otel_env.sh":
            assert mode == 0o755, f"{name} lost its exec bit ({oct(mode)})"
        assert mode in (0o644, 0o755), name


def test_bundle_has_nothing_machine_local_or_off_path(bundle):
    names = set(_names(bundle))
    assert not [n for n in names if wsb.forbidden(n)]
    assert not [n for n in names if n.startswith(("workshop-studio/", "tests/", "docs/img/", ".git/"))]
    assert not names & {"ec2/adlc-ec2.yaml", "ec2/launch-from-cloudshell.sh", "ec2/README.md", ".repo-url",
                        "tools/set-repo-url.sh", "tools/build-ws-bundle.sh", "tools/ws_bundle.py"}
    with zipfile.ZipFile(bundle) as z:
        leaked = [n for n in names if str(ROOT).encode() in z.read(n)]
    assert not leaked, f"build host path inside {leaked}"


def test_forbidden_catches_machine_local_paths():
    for p in ("config/local.env", ".lab-state/runs.json", "results/eval.json", "a/__pycache__/x.pyc", ".venv/bin/python",
              "data/customer/raw.csv", "setup.log", "x.zip", ".env.local", "reports/data-pages/a.html"):
        assert wsb.forbidden(p), p
    for p in ("labkit/config.py", "config/lab.env", "data/golden/README.md", "docs/assets/style.css"):
        assert not wsb.forbidden(p), p


def test_bundle_is_deterministic_and_names_its_source(bundle, tmp_path):
    again = tmp_path / "again.zip"
    assert wsb.main(["--out", str(again)]) == 0
    assert again.read_bytes() == bundle.read_bytes()
    with zipfile.ZipFile(bundle) as z:
        m = json.loads(z.read(wsb.MANIFEST))
        assert {i.date_time for i in z.infolist()} == {wsb.ZIP_TIME}
    assert m["name"] == "adlc-lab" and re.fullmatch(r"[0-9a-f]{40}", m["source_commit"])
    assert m["files"] == len(_names(bundle)) - 1


def test_bundled_participant_tests_pass_from_the_bundle_alone(bundle, tmp_path):
    """docs/06-evaluators.html tells participants to run `pytest evaluators`: the bundle must be self-sufficient."""
    dest = tmp_path / "adlc-lab"
    with zipfile.ZipFile(bundle) as z:
        z.extractall(dest)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("_ADLC_EXP_", "PYTEST_"))}
    env["ADLC_SKIP_BEDROCK"] = "1"
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "evaluators", "data/tests"],
                       cwd=dest, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]


def test_bundle_name_matches_the_template_default():
    assert wsb.DEFAULT_OUT.name == cfn(WS_TPL)["Parameters"]["BundleName"]["Default"]


# --------------------------------------------------------------------------- template
def _statements(tpl):
    return tpl["Resources"]["InstanceRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]


def test_ws_template_shares_network_role_and_boundary_with_ec2_template():
    ws, ec2 = cfn(WS_TPL), cfn(EC2_TPL)
    assert set(ws["Resources"]) == set(ec2["Resources"])
    for name in set(ec2["Resources"]) - {"InstanceRole", "Instance"}:
        assert ws["Resources"][name] == ec2["Resources"][name], f"{name} drifted from ec2/adlc-ec2.yaml"
    ws_st = {s["Sid"]: s for s in _statements(ws)}
    ec2_st = {s["Sid"]: s for s in _statements(ec2)}
    assert set(ws_st) - set(ec2_st) == {"LabAssetsRead"}
    assert all(ws_st[k] == v for k, v in ec2_st.items()), "instance-role statements drifted"
    wr, er = ws["Resources"]["InstanceRole"]["Properties"], ec2["Resources"]["InstanceRole"]["Properties"]
    assert {k: v for k, v in wr.items() if k != "Policies"} == {k: v for k, v in er.items() if k != "Policies"}
    wi, ei = ws["Resources"]["Instance"], ec2["Resources"]["Instance"]
    assert {k: v for k, v in wi["Properties"].items() if k != "UserData"} == \
           {k: v for k, v in ei["Properties"].items() if k != "UserData"}
    for p, d in ec2["Parameters"].items():
        if p != "LabSuffix":
            assert ws["Parameters"][p] == d, p
    assert ws["Parameters"]["LabSuffix"]["AllowedPattern"] == ec2["Parameters"]["LabSuffix"]["AllowedPattern"]
    assert set(ec2["Outputs"]) <= set(ws["Outputs"])


def test_ws_template_keeps_the_security_posture():
    ws = cfn(WS_TPL)
    sg = ws["Resources"]["InstanceSecurityGroup"]["Properties"]
    assert "SecurityGroupIngress" not in sg
    assert not [r for r in ws["Resources"].values() if r["Type"] == "AWS::EC2::SecurityGroupIngress"]
    props = ws["Resources"]["Instance"]["Properties"]
    assert "KeyName" not in props and props["MetadataOptions"]["HttpTokens"] == "required"
    assets = {s["Sid"]: s for s in _statements(ws)}["LabAssetsRead"]
    assert assets["Action"] == "s3:GetObject" and assets["Effect"] == "Allow"
    assert assets["Resource"] == {"Fn::Sub": "arn:${AWS::Partition}:s3:::${AssetsBucketName}/${AssetsBucketPrefix}${BundleName}"}
    assert ws["Resources"]["Instance"]["CreationPolicy"]["ResourceSignal"] == {"Count": 1, "Timeout": "PT40M"}


def _userdata(tpl):
    return tpl["Resources"]["Instance"]["Properties"]["UserData"]["Fn::Base64"]["Fn::Sub"]


def test_userdata_sub_references_are_parameters_only_and_bash_parses():
    """!Sub owns ${...}: a bash ${var} in UserData would break the stack (or worse, silently resolve)."""
    ws = cfn(WS_TPL)
    ud = _userdata(ws)
    refs = set(re.findall(r"\$\{([^}!]+)\}", ud))
    assert refs <= set(ws["Parameters"]) | {"AWS::Region", "AWS::StackName"}, refs - set(ws["Parameters"])
    sample = {"AWS::Region": "us-west-2", "AWS::StackName": "adlc-lab", "AssetsBucketName": "ws-event-x-us-west-2",
              "AssetsBucketPrefix": "b1/assets/", "BundleName": "adlc-lab.zip", "LabSuffix": "ws"}
    rendered = re.sub(r"\$\{([^}!]+)\}", lambda m: sample[m.group(1)], ud)
    r = subprocess.run(["bash", "-n"], input=rendered, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "s3://ws-event-x-us-west-2/b1/assets/adlc-lab.zip" in rendered


def test_userdata_runs_setup_as_the_lab_user_and_always_signals():
    ud = _userdata(cfn(WS_TPL))
    assert 'LAB_USER=ec2-user' in ud and 'runuser -l "$LAB_USER"' in ud
    assert "bash ec2/setup-ec2.sh" in ud and "./02-enable-observability.sh" in ud
    assert "./tools/set-suffix.sh ${LabSuffix}" in ud
    assert ud.count("cfn_signal 0 ") == 1 and "trap '" in ud and "EXIT" in ud
    assert "/var/log/adlc-bootstrap.log" in ud
    # setup failure is fatal, Transaction Search is not
    assert '|| fail "ec2/setup-ec2.sh failed' in ud and "TS=not-confirmed" in ud


# --------------------------------------------------------------------------- contentspec
LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-\s]*$")


def test_contentspec_matches_the_ws_schema_and_the_template():
    spec = yaml.safe_load((WS / "contentspec.yaml").read_text(encoding="utf-8"))
    assert str(spec["version"]) == "2.0" and spec["defaultLocaleCode"] == "zh-CN"
    acc = spec["awsAccountConfig"]
    assert acc["accountSources"] == ["workshop_studio"]
    role = acc["participantRole"]
    assert role.get("managedPolicies") or role.get("iamPolicies")  # WS build fails without one
    for p in role.get("iamPolicies", []):
        assert p.startswith("static/") and ".." not in p
        doc = json.loads((WS / p).read_text(encoding="utf-8"))
        assert doc["Version"] == "2012-10-17" and doc["Statement"]
    regions = acc["regionConfiguration"]
    assert len(regions["deployableRegions"].get("required", [])) <= 1
    assert set(sum(regions["deployableRegions"].values(), [])) <= {"us-west-2", "us-east-1"}  # Bedrock regions
    (cft,) = spec["infrastructure"]["cloudformationTemplates"]
    assert cft["templateLocation"] == "static/adlc-lab.yaml" and (WS / cft["templateLocation"]).is_file()
    assert LABEL_RE.match(cft["label"]), "WS rejects labels with commas or other punctuation"
    tpl = cfn(WS / cft["templateLocation"])
    given = {p["templateParameter"]: p["defaultValue"] for p in cft["parameters"]}
    assert given == {"AssetsBucketName": "{{.AssetsBucketName}}", "AssetsBucketPrefix": "{{.AssetsBucketPrefix}}"}
    no_default = {k for k, v in tpl["Parameters"].items() if "Default" not in v}
    assert no_default <= set(given), no_default - set(given)
    assert set(cft["participantVisibleStackOutputs"]) <= set(tpl["Outputs"])


def test_content_placeholder_page_and_assets_gitignore():
    page = (WS / "content" / "index.zh.md").read_text(encoding="utf-8")
    assert re.match(r'---\ntitle: "[^"]+"\nweight: 0\n---\n', page)
    assert "sudo su - ec2-user" in page
    ignore = (WS / ".gitignore").read_text(encoding="utf-8").split()
    assert "/assets/*" in ignore
    root_ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    assert "*.zip" in root_ignore
