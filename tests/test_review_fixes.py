"""Regression tests for the adversarial review findings (see code-review.md). No AWS calls."""
import collections
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from labkit import config as labcfg  # noqa: E402


@pytest.fixture
def cfg_env(monkeypatch, tmp_path):
    local = tmp_path / "local.env"
    monkeypatch.setattr(labcfg, "LOCAL_FILE", local)
    for k in labcfg.KEYS:
        monkeypatch.delenv(k, raising=False)
        monkeypatch.delenv(f"_S12_EXP_{k}", raising=False)
    monkeypatch.setenv("LAB_SUFFIX", "zhang3")
    return local


def test_r2_sourced_config_does_not_freeze_local_env(cfg_env, monkeypatch):
    """Finding 2: after `source ./00-config.sh`, 40-switch-version.sh must still take effect."""
    monkeypatch.setenv("AGENT_VERSION", "v0")          # exported by 00-config.sh
    monkeypatch.setenv("_S12_EXP_AGENT_VERSION", "v0")  # its snapshot
    cfg_env.write_text("AGENT_VERSION=v1\n", encoding="utf-8")
    assert labcfg.load()["AGENT_VERSION"] == "v1"


def test_r2_deliberate_env_override_still_wins(cfg_env, monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("_S12_EXP_AWS_REGION", "us-west-2")
    assert labcfg.load()["AWS_REGION"] == "us-east-1"


def test_r5_role_names_carry_region(cfg_env, monkeypatch):
    """Finding 5: IAM is global but the role policies are region-scoped."""
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    c = labcfg.load()
    for k in ("RUNTIME_ROLE_NAME", "EVAL_LAMBDA_ROLE_NAME", "ONLINE_EVAL_ROLE_NAME"):
        assert c[k].endswith("-us-east-1") and len(c[k]) <= 64


def test_r10_runtime_session_id_min_length():
    """Finding 10: Runtime requires runtimeSessionId >= 33 chars, even for short ids."""
    from labkit import batch
    assert len(batch._session_id("s12lab-ab", "v0", "1")) >= 33


def test_r12_imported_session_ids_unique_per_send():
    """Finding 12: re-sending the same log file must not reuse session ids."""
    from labkit import convert_logs
    a = convert_logs.imported_session_id("s12lab-ab", "20261105T100000Z", "sess-1")
    b = convert_logs.imported_session_id("s12lab-ab", "20261105T100500Z", "sess-1")
    long_id = "c" * 40
    assert a != b and len(a) >= 33
    assert convert_logs.imported_session_id("s12lab-ab", "T1", long_id) != convert_logs.imported_session_id("s12lab-ab", "T2", long_id)


def test_r6_feedback_pool_honours_s12_data_dir(monkeypatch, tmp_path):
    """Finding 6: customer data (S12_DATA_DIR) must replace the mock feedback pool."""
    from labkit import items
    (tmp_path / "feedback_last_month.jsonl").write_text(json.dumps({"id": "C-1", "title": "t", "content": "c"}) + "\n",
                                                         encoding="utf-8")
    monkeypatch.setenv("S12_DATA_DIR", str(tmp_path))
    assert items.feedback_path() == tmp_path / "feedback_last_month.jsonl"
    assert items.load("feedback")[0]["id"] == "C-1"


def test_r7_compare_never_pairs_with_imported(monkeypatch):
    """Finding 7: an imported-logs evaluation is not an 'after' version."""
    from labkit import compare
    evals = [{"run_id": "A-v0-1", "file": "results/eval-A-v0-1.json", "version": "v0", "mode": "local"},
             {"run_id": "A-v1-2", "file": "results/eval-A-v1-2.json", "version": "v1", "mode": "local"},
             {"run_id": "A-imported-3", "file": "results/eval-A-imported-3.json", "version": "imported", "mode": "imported"}]
    monkeypatch.setattr(compare, "load_state_list", lambda name: evals)
    before, after = compare.pick_default()
    assert before.name == "eval-A-v0-1.json" and after.name == "eval-A-v1-2.json"


def test_r9_one_failing_session_does_not_abort_evaluation(monkeypatch, cfg_env):
    """Finding 9: a CloudWatch query error is recorded per session instead of raising."""
    from labkit import evaluate, spans

    def boom(*a, **k):
        raise RuntimeError("LimitExceededException: too many concurrent queries")
    monkeypatch.setattr(spans, "session_docs", boom)
    out = evaluate.evaluate_session({"session_id": "s" * 40, "feedback_id": "FB-1", "result": {}}, {}, [], "lg", 0)
    assert "LimitExceeded" in out["error"] and out["scores"] == {}


class _Denied(Exception):
    pass


class _Pager:
    def __init__(self, deny=False):
        self.deny = deny

    def paginate(self, **kw):
        if self.deny:
            raise _Denied("AccessDeniedException: not authorized to perform lambda:ListFunctions")
        yield collections.defaultdict(list)


class _Client:
    def __init__(self, name):
        self.name = name

    def get_paginator(self, op):
        return _Pager(deny=(self.name == "lambda"))

    def list_buckets(self):
        return {"Buckets": [{"Name": "s12lab-zhang3-000000000000-us-west-2"},
                            {"Name": "s12lab-zhang3-000000000000-us-east-1"}]}

    def __getattr__(self, op):
        return lambda **kw: collections.defaultdict(list)


@pytest.fixture
def fake_aws(monkeypatch, cfg_env):
    from labkit import aws
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setattr(aws, "client", lambda name: _Client(name))
    from labkit import cleanup
    return cleanup


def test_r1_cleanup_survives_access_denied_listers(fake_aws):
    """Finding 1: one AccessDenied lister must not crash cleanup/verify."""
    cl = fake_aws.Cleaner(False, True)
    assert cl.verify() == 3  # completes, flags the incomplete listing


def test_r5_cleanup_buckets_only_current_region(fake_aws):
    cl = fake_aws.Cleaner(False, True)
    assert cl.buckets() == ["s12lab-zhang3-000000000000-us-west-2"]


def test_r8_account_level_requires_all(fake_aws, monkeypatch):
    """Finding 8: participants must not be able to revert account-level settings."""
    def no_cleaner(*a, **k):
        raise AssertionError("Cleaner must not be constructed")
    monkeypatch.setattr(fake_aws, "Cleaner", no_cleaner)
    assert fake_aws.main(["--account-level"]) == 1
    assert fake_aws.main(["--service-linked-roles", "--dry-run"]) == 1
