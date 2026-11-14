"""Workshop Studio delivery: lab bundle (tools/ws_bundle.py), static/adlc-lab.yaml and contentspec.yaml.

Offline only. The WS template is derived from ec2/adlc-ec2.yaml; these tests keep the shared parts identical
(network, security group, instance role, permissions boundary) so the two delivery paths cannot drift.
"""
import importlib.util
import json
import os
import re
import shutil
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
    assert wsb.main(["--out", str(out), "--allow-dirty"]) == 0
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
    host = [str(ROOT).encode(), os.path.expanduser("~").encode() + b"/"]
    with zipfile.ZipFile(bundle) as z:
        leaked = [n for n in names if any(h in z.read(n) for h in host)]
    assert not leaked, f"build host path inside {leaked}"


def test_forbidden_catches_machine_local_paths():
    for p in ("config/local.env", ".lab-state/runs.json", "results/eval.json", "a/__pycache__/x.pyc", ".venv/bin/python",
              "data/customer/raw.csv", "setup.log", "x.zip", ".env.local", "reports/data-pages/a.html"):
        assert wsb.forbidden(p), p
    for p in ("labkit/config.py", "config/lab.env", "data/golden/README.md", "docs/assets/style.css"):
        assert not wsb.forbidden(p), p


def test_bundle_is_deterministic_and_names_its_source(bundle, tmp_path):
    again = tmp_path / "again.zip"
    assert wsb.main(["--out", str(again), "--allow-dirty"]) == 0
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


def test_tracked_files_matched_by_gitignore_are_refused(monkeypatch):
    assert wsb.tracked_but_ignored() == set()
    monkeypatch.setattr(wsb, "tracked_but_ignored", lambda: {"agent/.env.local", "labkit/config.py"})
    with pytest.raises(SystemExit, match="labkit/config.py"):
        wsb.select()


def test_dirty_tree_is_refused_unless_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(wsb, "source_state", lambda files: ("0" * 40, True))
    out = tmp_path / "dirty.zip"
    assert wsb.main(["--out", str(out)]) == 1 and not out.exists()
    assert wsb.main(["--out", str(out), "--allow-dirty"]) == 0
    with zipfile.ZipFile(out) as z:
        assert json.loads(z.read(wsb.MANIFEST))["source_dirty"] is True


def test_relative_out_is_resolved_against_the_callers_cwd(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert wsb.main(["--out", "rel/b.zip", "--allow-dirty"]) == 0
    assert (tmp_path / "rel" / "b.zip").is_file()
    assert re.search(r"^[0-9a-f]{64}  b\.zip$", capsys.readouterr().out, re.M)


def test_bundle_name_matches_the_template_default():
    assert wsb.DEFAULT_OUT.name == cfn(WS_TPL)["Parameters"]["BundleName"]["Default"]


# --------------------------------------------------------------------------- template
def _statements(tpl):
    return tpl["Resources"]["InstanceRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]


def test_ws_template_shares_network_role_and_boundary_with_ec2_template():
    ws, ec2 = cfn(WS_TPL), cfn(EC2_TPL)
    assert set(ws["Resources"]) == set(ec2["Resources"]) | {"SetupWaitHandle", "SetupWait"}
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
    # the stack completes only on the instance's signal; the reason of a FAILURE shows in the stack events
    wait = ws["Resources"]["SetupWait"]
    assert wait["Type"] == "AWS::CloudFormation::WaitCondition" and wait["DependsOn"] == "Instance"
    assert wait["Properties"] == {"Handle": {"Ref": "SetupWaitHandle"}, "Count": 1, "Timeout": "3600"}
    assert ws["Resources"]["SetupWaitHandle"]["Type"] == "AWS::CloudFormation::WaitConditionHandle"
    assert "CreationPolicy" not in ws["Resources"]["Instance"]
    pattern = re.compile(ws["Parameters"]["AssetsBucketPrefix"]["AllowedPattern"])
    assert pattern.match("") and pattern.match("0f1e/assets/")
    assert not any(pattern.match(p) for p in ("a'b/", "a*/", "no-slash", "/lead/"))


def _userdata(tpl):
    return tpl["Resources"]["Instance"]["Properties"]["UserData"]["Fn::Base64"]["Fn::Sub"]


def test_userdata_sub_references_are_parameters_only_and_bash_parses():
    """!Sub owns ${...}: a bash ${var} in UserData would break the stack (or worse, silently resolve)."""
    ws = cfn(WS_TPL)
    ud = _userdata(ws)
    refs = set(re.findall(r"\$\{([^}!]+)\}", ud))
    assert refs <= set(ws["Parameters"]) | {"AWS::Region", "SetupWaitHandle"}, refs - set(ws["Parameters"])
    rendered = _render(ud)
    r = subprocess.run(["bash", "-n"], input=rendered, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "s3://ws-event-x-us-west-2/b1/assets/adlc-lab.zip" in rendered


SAMPLE = {"AWS::Region": "us-west-2", "AssetsBucketName": "ws-event-x-us-west-2", "AssetsBucketPrefix": "b1/assets/",
          "BundleName": "adlc-lab.zip", "LabSuffix": "ws", "SetupWaitHandle": "https://wait.example.invalid/handle"}


def _render(ud):
    return re.sub(r"\$\{([^}!]+)\}", lambda m: SAMPLE[m.group(1)], ud)


# Stand-ins for what UserData calls as root on AL2023. Everything else (unzip, git, python3, sed, tail,
# the bundled tools/set-suffix.sh) runs for real; setup-ec2.sh / 02-enable-observability.sh are stubbed.
STUBS = {
    "dnf": '[ -n "${FAIL_DNF:-}" ] && { echo "dnf: cannot reach the repo"; exit 1; }; echo "dnf $*"',
    "aws": 'echo "$@" >> "$T/aws.args"; [ -n "${FAIL_DL:-}" ] && { echo "An error occurred (403)"; exit 1; }\n'
           'for a in "$@"; do dst="$a"; done; cp "$BUNDLE" "$dst"',
    "install": 'for a in "$@"; do d="$a"; done; mkdir -p "$d"',
    "chown": "exit 0",
    "sleep": "exit 0",
    "curl": 'echo "$*" >> "$T/curl.args"\n'
            'while [ $# -gt 0 ]; do [ "$1" = "--data-binary" ] && { shift; printf "%s\\n" "$1" >> "$T/signals.jsonl"; }; '
            'last="$1"; shift; done; echo "$last" >> "$T/curl.urls"; [ -z "${FAIL_CURL:-}" ]',
    "runuser": 'if [ "$1" = "-l" ]; then cmd="$4"\n'
               '  case "$cmd" in\n'
               '    *"bash ec2/setup-ec2.sh"*) n=$(( $(cat "$T/setup.n" 2>/dev/null || echo 0) + 1 )); echo $n > "$T/setup.n"\n'
               '      if [ -n "${FAIL_SETUP:-}" ] && [ "$n" -le "$FAIL_SETUP" ]; then\n'
               '        echo "npm ERR! network 安装中止 (try $n)" | tee -a "$T/home/adlc-setup.log"; exit 1; fi\n'
               '      echo setup-ok | tee -a "$T/home/adlc-setup.log"; exit 0 ;;\n'
               '    *"./02-enable-observability.sh"*) [ -n "${FAIL_TS:-}" ] && exit 1; echo ts-ok; exit 0 ;;\n'
               '  esac\n'
               '  cd "$T/home" && HOME="$T/home" exec bash -c "$cmd"\n'
               'elif [ "$1" = "-u" ]; then shift 3; exec "$@"; fi\nexit 3',
}
REAL_PATHS = {"/var/log/adlc-bootstrap.log": "bootstrap.log", "/etc/profile.d/adlc.sh": "profile-adlc.sh",
              "/home/ec2-user": "home", "/tmp/adlc-bundle.XXXXXX": "adlc-bundle.XXXXXX"}


def _run_userdata(tmp_path, bundle, inject=None, **fail):
    t = tmp_path
    script = _render(_userdata(cfn(WS_TPL)))
    if inject:
        assert inject[0] in script
        script = script.replace(inject[0], inject[1], 1)
    for real, fake in REAL_PATHS.items():
        assert real in script, real
        script = script.replace(real, f"{t}/{fake}")
    (t / "home").mkdir()
    (t / "home" / ".bashrc").write_text("# existing\n", encoding="utf-8")
    bindir = t / "bin"
    bindir.mkdir()
    for name, body in STUBS.items():
        (bindir / name).write_text("#!/bin/bash\n" + body + "\n", encoding="utf-8")
        (bindir / name).chmod(0o755)
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "T": str(t), "BUNDLE": str(bundle), "LANG": "C.UTF-8",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", **{k: str(v) for k, v in fail.items()}}
    # stdin closed: bash must not mistake a socket stdin for rsh and source ~/.bashrc (would bypass the stubs)
    r = subprocess.run(["bash", "-c", script], env=env, cwd=t, capture_output=True, text=True, timeout=120,
                       stdin=subprocess.DEVNULL)
    sig = t / "signals.jsonl"
    signals = [json.loads(x) for x in sig.read_text(encoding="utf-8").splitlines()] if sig.exists() else []
    return r, signals


needs_linux_tools = pytest.mark.skipif(
    sys.platform != "linux" or not all(shutil.which(x) for x in ("bash", "unzip", "git", "python3")),
    reason="UserData targets Amazon Linux (GNU sed, unzip, git)")


@needs_linux_tools
def test_userdata_success_unpacks_a_git_lab_as_the_user_and_signals_once(tmp_path, bundle):
    r, signals = _run_userdata(tmp_path, bundle)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert [s["Status"] for s in signals] == ["SUCCESS"], signals
    with zipfile.ZipFile(bundle) as z:
        commit = json.loads(z.read(wsb.MANIFEST))["source_commit"][:12]
    assert "transaction search enabled" in signals[0]["Reason"] and signals[0]["UniqueId"] == "adlc-setup"
    assert f"bundle {commit}" in signals[0]["Reason"]
    assert (tmp_path / "curl.urls").read_text().split() == [SAMPLE["SetupWaitHandle"]]
    curl = (tmp_path / "curl.args").read_text()
    assert "-X PUT" in curl and "-H Content-Type: " in curl  # pre-signed S3 PUT is signed without a content type
    assert "s3://ws-event-x-us-west-2/b1/assets/adlc-lab.zip" in (tmp_path / "aws.args").read_text()
    lab = tmp_path / "home" / "adlc-lab"
    assert (lab / "ec2" / "setup-ec2.sh").is_file() and os.access(lab / "01-check-env.sh", os.X_OK)
    assert (lab / "config" / "local.env").read_text().strip() == "LAB_SUFFIX=ws"
    assert "export LAB_SUFFIX=ws" in (tmp_path / "profile-adlc.sh").read_text()
    bashrc = (tmp_path / "home" / ".bashrc").read_text()
    assert bashrc.startswith("# existing\n") and bashrc.count("# >>> adlc-ws >>>") == 1
    # like a git clone: pull-templates can read the committed default templates, and the tree is clean
    from labkit import pull_templates
    for name in ("mock-data-params.yaml", "discussion-feedback.md", "golden-set-feedback.csv"):
        assert pull_templates.git_default(lab, name) == (lab / "data" / "templates" / name).read_bytes(), name
    st = subprocess.run(["git", "-C", str(lab), "status", "--porcelain"], capture_output=True, text=True,
                        env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})
    assert st.returncode == 0 and st.stdout == "", st.stdout


@needs_linux_tools
@pytest.mark.parametrize("fail, step, text", [
    ({"FAIL_DNF": 1}, "packages", "dnf install failed"),
    ({"FAIL_DL": 1}, "download", "cannot download s3://ws-event-x-us-west-2/b1/assets/adlc-lab.zip"),
    # three attempts, then the tail of adlc-setup.log (UTF-8 kept) travels in the reason
    ({"FAIL_SETUP": 9}, "setup", "npm ERR! network 安装中止 (try 3)"),
])
def test_userdata_failure_signals_failure_once_with_the_step(tmp_path, bundle, fail, step, text):
    r, signals = _run_userdata(tmp_path, bundle, **fail)
    assert r.returncode != 0
    assert [s["Status"] for s in signals] == ["FAILURE"], signals
    reason = signals[0]["Reason"]
    assert reason.startswith(f"adlc bootstrap failed at step {step}: ") and text in reason, reason


@needs_linux_tools
def test_userdata_retries_a_transient_setup_failure(tmp_path, bundle):
    r, signals = _run_userdata(tmp_path, bundle, FAIL_SETUP=1)
    assert r.returncode == 0 and (tmp_path / "setup.n").read_text().strip() == "2"
    assert [s["Status"] for s in signals] == ["SUCCESS"], signals


@needs_linux_tools
def test_userdata_unexpected_exit_still_signals_failure_once(tmp_path, bundle):
    r, signals = _run_userdata(tmp_path, bundle, inject=("STEP=unpack\n", "STEP=unpack\nexit 3\n"))
    assert r.returncode == 3
    assert [s["Status"] for s in signals] == ["FAILURE"]
    assert "exited (rc=3) at step unpack" in signals[0]["Reason"]


@needs_linux_tools
def test_userdata_gives_up_signalling_after_five_attempts(tmp_path, bundle):
    r, signals = _run_userdata(tmp_path, bundle, FAIL_CURL=1)
    assert len(signals) == 5 and {s["Status"] for s in signals} == {"SUCCESS"}  # retries, no FAILURE echo
    assert "could not signal CloudFormation" in r.stdout


@needs_linux_tools
def test_userdata_transaction_search_failure_is_not_fatal(tmp_path, bundle):
    r, signals = _run_userdata(tmp_path, bundle, FAIL_TS=1)
    assert r.returncode == 0
    assert [s["Status"] for s in signals] == ["SUCCESS"] and "not-confirmed" in signals[0]["Reason"]


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


def test_participant_policy_is_scoped():
    """Console needs: Session Manager on the lab instance only, own sessions only, uploads to adlc-* buckets."""
    doc = json.loads((WS / "static" / "iam-policy.json").read_text(encoding="utf-8"))
    st = {s["Sid"]: s for s in doc["Statement"]}
    assert all(s["Effect"] == "Allow" for s in st.values())
    assert st["SessionManagerOnLabInstance"]["Condition"] == {"StringEquals": {"ssm:resourceTag/adlc:lab": "agentdlc"}}
    own = st["SessionManagerOwnSessions"]
    assert own["Condition"] == {"StringEquals": {"ssm:resourceTag/aws:ssmmessages:session-id": "${aws:userid}"}}
    up = st["LabBucketConsoleUploads"]
    assert up["Resource"] == "arn:aws:s3:::adlc-*/*" and not any(a.endswith("*") for a in up["Action"])
    assert not [s for s in st.values() if s.get("Resource") == "*" or "iam:" in json.dumps(s.get("Action"))]
