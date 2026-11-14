"""10-check-env.sh: the closing "下一步" must match the Transaction Search state it just printed (bug: it always
pointed at ./11-enable-observability.sh, also when Workshop Studio's CloudFormation had already enabled it)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from labkit import config as labcfg  # noqa: E402


class _Fake:
    def __init__(self, dest):
        self.dest = dest

    def get_trace_segment_destination(self):
        return self.dest

    def get_caller_identity(self):
        return {"Arn": "arn:aws:sts::123456789012:assumed-role/adlc-ec2-ws-us-west-2/i-0abc"}


@pytest.fixture
def checkenv(monkeypatch, tmp_path):
    monkeypatch.setattr(labcfg, "LOCAL_FILE", tmp_path / "local.env")
    monkeypatch.delenv("ADLC_LOCAL_ENV", raising=False)
    for k in labcfg.KEYS:
        monkeypatch.delenv(k, raising=False)
        monkeypatch.delenv(f"_ADLC_EXP_{k}", raising=False)
    monkeypatch.setenv("LAB_SUFFIX", "ws")
    from labkit import aws, checkenv as ce
    aws.cfg.cache_clear()
    monkeypatch.setattr(ce, "imds_region", lambda: None)
    monkeypatch.setattr(ce, "ping_model", lambda mid: (True, "收到"))
    monkeypatch.setattr(ce.md, "version", lambda p: "1.0")
    yield ce, aws
    aws.cfg.cache_clear()


def _last_line(capsys):
    return [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()][-1]


def test_next_step_skips_enabling_when_transaction_search_is_on(checkenv, monkeypatch, capsys):
    ce, aws = checkenv
    monkeypatch.setattr(aws, "client", lambda name: _Fake({"Destination": "CloudWatchLogs", "Status": "ACTIVE"}))
    assert ce.main() == 0
    last = _last_line(capsys)
    assert "11-enable-observability" not in last and "第 2 章" in last, last


def test_next_step_points_at_enabling_when_transaction_search_is_off(checkenv, monkeypatch, capsys):
    ce, aws = checkenv
    monkeypatch.setattr(aws, "client", lambda name: _Fake({"Destination": "XRay", "Status": "ACTIVE"}))
    assert ce.main() == 0
    assert "./11-enable-observability.sh" in _last_line(capsys)
