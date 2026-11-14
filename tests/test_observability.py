"""labkit.observability: enable records who turned Transaction Search on; --verify polls and checks it is usable."""
import fnmatch
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from labkit import aws, observability  # noqa: E402

SPANS_POLICY = {"policyName": "TransactionSearchXRayAccess", "policyDocument": json.dumps({"Statement": [{
    "Effect": "Allow", "Principal": {"Service": "xray.amazonaws.com"}, "Action": ["logs:PutLogEvents"],
    "Resource": ["arn:aws:logs:us-west-2:123456789012:log-group:aws/spans:*"]}]})}
OTHER_POLICY = {"policyName": "SomethingElse", "policyDocument": json.dumps({"Statement": {
    "Effect": "Allow", "Principal": {"Service": "es.amazonaws.com"}, "Action": "logs:PutLogEvents",
    "Resource": "arn:aws:logs:us-west-2:123456789012:log-group:/aws/es/*"}})}
DEFAULT_RULE = [{"Name": "Default", "Rule": {"Probabilistic": {"DesiredSamplingPercentage": 100.0}}}]


class FakeXray:
    def __init__(self, states, rules=DEFAULT_RULE):
        self.states, self.rules, self.calls = list(states), rules, []

    def get_trace_segment_destination(self):
        self.calls.append("get")
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    def get_indexing_rules(self):
        return {"IndexingRules": self.rules}

    def update_trace_segment_destination(self, **kw):
        self.calls.append(("update_destination", kw))

    def update_indexing_rule(self, **kw):
        self.calls.append(("update_indexing", kw))


class FakeLogs:
    def __init__(self, pages):
        self.pages, self.put = list(pages), []

    def describe_resource_policies(self, **kw):
        i = int(kw.get("nextToken", 0))
        out = {"resourcePolicies": self.pages[i]}
        if i + 1 < len(self.pages):
            out["nextToken"] = str(i + 1)
        return out

    def put_resource_policy(self, **kw):
        self.put.append(kw)


ACTIVE = {"Destination": "CloudWatchLogs", "Status": "ACTIVE"}
PENDING = {"Destination": "CloudWatchLogs", "Status": "PENDING"}
OFF = {"Destination": "XRay", "Status": "ACTIVE"}


@pytest.fixture
def env(monkeypatch):
    clock = {"t": 0.0}
    state = {}
    monkeypatch.setattr(observability.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(observability.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(aws, "load_state", lambda n, d=None: state.get(n, d))
    monkeypatch.setattr(aws, "save_state", lambda n, data: state.__setitem__(n, data))
    monkeypatch.setattr(aws, "cfg", lambda: {"AWS_REGION": "us-west-2"})
    monkeypatch.setattr(aws, "account_id", lambda: "123456789012")

    def use(xray, logs):
        monkeypatch.setattr(aws, "client", lambda name: {"xray": xray, "logs": logs}[name])
    return {"clock": clock, "state": state, "use": use}


def lines(capsys):
    return capsys.readouterr().out.strip().splitlines()


def last_line(capsys):
    """The ASCII summary the Workshop Studio template puts into the stack's reason."""
    out = lines(capsys)
    assert out[-1].isascii()
    return out[-1]


def test_verify_passes_at_once_when_active_and_complete(env, capsys):
    x = FakeXray([ACTIVE])
    env["use"](x, FakeLogs([[OTHER_POLICY], [SPANS_POLICY]]))  # second page: pagination is followed
    env["state"]["observability"] = {"enabled_by_lab": True}
    assert observability.verify(600) == 0 and x.calls == ["get"]
    out = lines(capsys)
    assert out[-2].startswith("✅ Transaction Search 已就绪") and "本实验开启" in out[-2]
    assert out[-1] == ("transaction-search ready: Destination=CloudWatchLogs Status=ACTIVE "
                       "policy=TransactionSearchXRayAccess indexing-default=100% enabled-by-lab=yes")


def test_verify_polls_until_active_within_the_wait(env, capsys):
    x = FakeXray([OFF, PENDING, PENDING, ACTIVE])
    env["use"](x, FakeLogs([[SPANS_POLICY]]))
    assert observability.verify(600, interval=15) == 0
    assert x.calls.count("get") == 4 and env["clock"]["t"] == 45
    assert last_line(capsys).endswith("enabled-by-lab=no")  # no lab record: it was on before


def test_verify_fails_with_the_observed_status_after_the_wait(env, capsys):
    x = FakeXray([PENDING])
    env["use"](x, FakeLogs([[SPANS_POLICY]]))
    assert observability.verify(60, interval=15) == 1
    assert env["clock"]["t"] == 60
    out = lines(capsys)
    assert out[-2] == "❌ Transaction Search 未就绪（等待 60s）：Destination=CloudWatchLogs Status=PENDING"
    assert out[-1] == "transaction-search NOT ready after 60s: Destination=CloudWatchLogs Status=PENDING"


@pytest.mark.parametrize("pages, rules, missing", [
    ([[OTHER_POLICY]], DEFAULT_RULE, "policy=none"),
    ([[SPANS_POLICY]], [], "indexing-default=none"),
    ([[SPANS_POLICY]], [{"Name": "Default", "Rule": {"Probabilistic": {"DesiredSamplingPercentage": 0}}}], "indexing-default=0%"),
])
def test_verify_fails_when_policy_or_indexing_is_missing(env, capsys, pages, rules, missing):
    env["use"](FakeXray([ACTIVE], rules), FakeLogs(pages))
    assert observability.verify(600) == 1
    line = last_line(capsys)
    assert line.startswith("transaction-search incomplete: ") and missing in line


def test_enable_records_that_the_lab_turned_it_on(env, capsys):
    x, logs = FakeXray([OFF, OFF, ACTIVE]), FakeLogs([[]])
    env["use"](x, logs)
    assert observability.enable() == 0
    assert env["state"]["observability"] == {"enabled_by_lab": True, "region": "us-west-2", "previous_destination": "XRay"}
    assert logs.put and logs.put[0]["policyName"] == observability.POLICY_NAME
    assert ("update_destination", {"Destination": "CloudWatchLogs"}) in x.calls


def test_enable_is_a_no_op_when_already_active_and_keeps_an_earlier_lab_record(env, capsys):
    x, logs = FakeXray([ACTIVE]), FakeLogs([[SPANS_POLICY]])
    env["use"](x, logs)
    assert observability.enable() == 0 and not logs.put and x.calls == ["get"]
    assert env["state"]["observability"] == {"enabled_by_lab": False, "region": "us-west-2"}
    env["state"]["observability"] = {"enabled_by_lab": True, "region": "us-west-2"}  # this lab enabled it earlier
    assert observability.enable() == 0
    assert env["state"]["observability"]["enabled_by_lab"] is True  # cleanup --account-level can still revert


def test_main_dispatches_verify_with_wait(monkeypatch):
    seen = {}
    monkeypatch.setattr(observability, "verify", lambda wait: seen.setdefault("wait", wait) and 0)
    monkeypatch.setattr(observability, "enable", lambda: seen.setdefault("enable", True) and 0)
    observability.main(["--verify", "--wait", "60"])
    observability.main([])
    assert seen == {"wait": 60, "enable": True}


def _role_allows(statements, action):
    allow = [s for s in statements if s["Effect"] == "Allow" and s["Resource"] == "*"
             and any(fnmatch.fnmatchcase(action, a) for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"]))]
    deny = [s for s in statements if s["Effect"] == "Deny"
            and any(fnmatch.fnmatchcase(action, a) for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"]))]
    return bool(allow) and not deny


@pytest.mark.parametrize("template", ["ec2/adlc-ec2.yaml", "workshop-studio/static/adlc-lab.yaml"])
def test_instance_role_allows_every_call_of_enable_and_verify(template):
    """The EC2 enables Transaction Search itself (account-level APIs: Resource "*")."""
    from test_ws_infra import cfn
    src = Path(observability.__file__).read_text(encoding="utf-8")
    body = "".join(re.findall(r"\ndef (?:enable|verify|_xray_policy|_indexing_percent)\(.*?(?=\ndef |\Z)", src, re.S))
    calls = set(re.findall(r"\b(xray|logs)\.([a-z_]+)\(", body))
    actions = {f"{svc}:{''.join(w.title() for w in m.split('_'))}" for svc, m in calls} | {"sts:GetCallerIdentity"}
    assert {"xray:GetTraceSegmentDestination", "xray:UpdateTraceSegmentDestination", "xray:UpdateIndexingRule",
            "xray:GetIndexingRules", "logs:PutResourcePolicy", "logs:DescribeResourcePolicies"} <= actions
    tpl = cfn(ROOT / template)
    st = tpl["Resources"]["InstanceRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    missing = sorted(a for a in actions if not _role_allows(st, a))
    assert not missing, missing


def _stmt(**kw):
    st = {"Effect": "Allow", "Principal": {"Service": "xray.amazonaws.com"}, "Action": "logs:PutLogEvents",
          "Resource": "arn:aws:logs:us-west-2:123456789012:log-group:aws/spans:*"}
    st.update(kw)
    return {"policyName": "P", "policyDocument": json.dumps({"Statement": [st]})}


@pytest.mark.parametrize("policy, found", [
    (_stmt(), True),
    (_stmt(Resource="*"), True),                                                    # wildcard resource
    (_stmt(Action=["logs:*"]), True),
    (_stmt(Principal="*"), False),                                                  # string principal: no crash
    ({"policyName": "L", "policyDocument": "[1, 2]"}, False),                       # JSON list document
    (_stmt(Resource="arn:aws:logs:us-west-2:999999999999:log-group:aws/spans:*"), False),  # other account
    (_stmt(Resource="arn:aws:logs:us-east-1:123456789012:log-group:aws/spans:*"), False),  # other Region
    (_stmt(Resource="arn:aws:logs:us-west-2:123456789012:log-group:aws/spans-old:*"), False),
    (_stmt(Action="logs:CreateLogStream"), False),                                  # cannot write events
    (_stmt(Effect="Deny"), False),
])
def test_policy_detection(policy, found):
    assert (observability._xray_policy(FakeLogs([[policy]]), "us-west-2", "123456789012") is not None) is found


def test_enable_completes_a_partly_configured_destination_without_claiming_it(env, capsys):
    zero = [{"Name": "Default", "Rule": {"Probabilistic": {"DesiredSamplingPercentage": 0}}}]
    x, logs = FakeXray([ACTIVE], rules=zero), FakeLogs([[]])
    env["use"](x, logs)
    assert observability.enable() == 0
    assert logs.put and ("update_indexing", {"Name": "Default", "Rule": {"Probabilistic": {"DesiredSamplingPercentage": 100}}}) in x.calls
    assert not [c for c in x.calls if c[0] == "update_destination"]
    assert env["state"]["observability"]["enabled_by_lab"] is False  # it was on before: never revert it


def test_enable_waits_for_a_pending_destination_without_claiming_it(env, capsys):
    x, logs = FakeXray([PENDING, PENDING, ACTIVE]), FakeLogs([[SPANS_POLICY]])
    env["use"](x, logs)
    assert observability.enable() == 0
    assert not [c for c in x.calls if isinstance(c, tuple)] and not logs.put
    assert env["state"]["observability"]["enabled_by_lab"] is False


def test_enable_records_nothing_when_switching_the_destination_fails(env, capsys):
    class Boom(FakeXray):
        def update_trace_segment_destination(self, **kw):
            raise RuntimeError("LimitExceededException")
    env["use"](Boom([OFF]), FakeLogs([[]]))
    with pytest.raises(RuntimeError):
        observability.enable()
    assert "observability" not in env["state"]
