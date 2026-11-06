"""A4 send: re-stamped spans and content records must not pre-date the log groups they land in.

CloudWatch Logs Insights cannot see events whose timestamp is earlier than the log group's
creation time. On a first send the wrapper creates the imported-logs group seconds before the
converter re-stamps the sessions to "ending ~2 min ago", so every content record was invisible:
12-A-check-telemetry showed 用户输入 0/5 and 31-run-eval had no inputs until a second send.
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from labkit import aws, convert_logs  # noqa: E402

GROUP = "/aws/bedrock-agentcore/s12lab-zz-imported-logs"
DAY = 86400


class FakeLogs:
    """describe_log_groups / create_log_group like CloudWatch Logs (creationTime in epoch ms)."""

    def __init__(self, created: dict):
        self.created = dict(created)          # group name -> creation time, epoch seconds
        self.exceptions = SimpleNamespace(ResourceAlreadyExistsException=type("Exists", (Exception,), {}))
        self.calls = []

    def describe_log_groups(self, logGroupNamePrefix):
        from opentelemetry.instrumentation.utils import is_instrumentation_enabled
        self.calls.append(("describe", logGroupNamePrefix, is_instrumentation_enabled()))
        return {"logGroups": [{"logGroupName": g, "creationTime": int(t * 1000)}
                              for g, t in sorted(self.created.items()) if g.startswith(logGroupNamePrefix)]}

    def create_log_group(self, logGroupName, tags=None):
        self.calls.append(("create", logGroupName))
        if logGroupName in self.created:
            raise self.exceptions.ResourceAlreadyExistsException()
        self.created[logGroupName] = time.time()

    def put_retention_policy(self, **kw):
        pass

    def create_log_stream(self, **kw):
        pass


class FakeLogger:
    def __init__(self):
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _send(tmp_path, monkeypatch, n, created, tools=0, capsys=None):
    """Run convert_logs.send() on n log lines with fake CloudWatch groups; return what it emitted."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    state, results = tmp_path / ".lab-state", tmp_path / "results"
    cfg = {"LAB_NAME": "s12lab-zz", "LAB_SUFFIX": "zz", "RESULTS_DIR": str(results), "STATE_DIR": str(state),
           "LAB_ROOT": str(tmp_path), "CONVERTED_SERVICE_NAME": "s12lab-zz-imported-logs",
           "CONVERTED_LOG_GROUP": GROUP}
    monkeypatch.setattr(aws, "cfg", lambda: cfg)
    logs = FakeLogs(created)
    monkeypatch.setattr(aws, "client", lambda name: logs)
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer", lambda name, *a, **k: provider.get_tracer(name))
    logger = FakeLogger()
    import opentelemetry._logs as otel_logs
    monkeypatch.setattr(otel_logs, "get_logger_provider", lambda: SimpleNamespace(get_logger=lambda name, *a, **k: logger))
    log = tmp_path / "logs.jsonl"
    tc = [{"name": "lookup", "input": {"q": "x"}, "output": "y"}] * tools
    log.write_text("".join(json.dumps({"session_id": f"s{i}", "timestamp": f"2026-10-12T09:{i % 60:02d}:00+08:00",
                                       "user_input": f"需求编号: FB-{i}", "agent_output": "ok", "tool_calls": tc}) + "\n"
                           for i in range(n)), encoding="utf-8")
    t0 = time.time()
    rc = convert_logs.send(str(log))
    t1 = time.time()
    spans = exporter.get_finished_spans()
    roots = sorted((s for s in spans if s.name.startswith("invoke_agent")), key=lambda s: s.start_time)
    runs = json.loads((state / "runs.json").read_text(encoding="utf-8")) if (state / "runs.json").exists() else []
    out = capsys.readouterr().out if capsys else ""
    return SimpleNamespace(rc=rc, spans=spans, roots=roots, records=logger.records, runs=runs, logs=logs, t0=t0, t1=t1,
                           results=results, out=out)


def _all_ts_ns(r) -> list:
    return ([s.start_time for s in r.spans] + [s.end_time for s in r.spans]
            + [x.timestamp for x in r.records] + [x.observed_timestamp for x in r.records])


def test_first_send_never_stamps_before_the_log_group_was_created(tmp_path, monkeypatch):
    """The group was created 3 s ago (later than now - 120 - 10n): nothing may be stamped before it."""
    now = time.time()
    created = now - 3
    r = _send(tmp_path, monkeypatch, 5, {GROUP: created, "aws/spans": now - 30 * DAY}, tools=1)
    assert r.rc == 0 and len(r.roots) == 5 and len(r.records) == 5 * 3
    created_ns = int(created * 1000) * 1_000_000               # CloudWatch keeps creationTime in ms
    assert min(_all_ts_ns(r)) > created_ns
    starts = [s.start_time / 1e9 for s in r.roots]             # original order, 10 s apart
    assert [s.attributes["session.id"].rsplit("-", 1)[-1] for s in r.roots] == [f"s{i}" for i in range(5)]
    assert all(abs((b - a) - 10) < 1e-3 for a, b in zip(starts, starts[1:]))
    assert max(s.end_time for s in r.spans) / 1e9 < r.t1 + convert_logs.FUTURE_SAFE_S
    # D6: runs.json "created" is still the earliest re-stamped span start (second precision)
    first = datetime.fromtimestamp(starts[0], timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    assert r.runs[-1]["created"] == first


def test_a_group_created_long_ago_keeps_the_window_ending_two_minutes_ago(tmp_path, monkeypatch):
    now = time.time()
    r = _send(tmp_path, monkeypatch, 5, {GROUP: now - 3 * DAY, "aws/spans": now - 30 * DAY})
    assert r.rc == 0
    starts = [s.start_time / 1e9 for s in r.roots]
    assert r.t0 - 120 - 50 - 1 <= starts[0] <= r.t1 - 120 - 50 + 1
    assert starts[-1] <= r.t1 - 120 - 10 + 1


def test_a_newer_aws_spans_group_also_bounds_the_window(tmp_path, monkeypatch):
    """Spans land in aws/spans: if that group is younger than ours (fresh Transaction Search), it bounds too."""
    now = time.time()
    r = _send(tmp_path, monkeypatch, 4, {GROUP: now - 3 * DAY, "aws/spans": now - 4})
    assert r.rc == 0
    assert min(_all_ts_ns(r)) > int((now - 4) * 1000) * 1_000_000


def test_missing_group_is_created_before_the_window_is_computed(tmp_path, monkeypatch):
    """send() run without the wrapper: the group is created first, then its creation time is read."""
    r = _send(tmp_path, monkeypatch, 3, {"aws/spans": time.time() - 30 * DAY})
    assert r.rc == 0
    assert ("create", GROUP) in r.logs.calls
    assert min(_all_ts_ns(r)) > int(r.logs.created[GROUP] * 1000) * 1_000_000


def test_window_never_reaches_cloudwatchs_future_limit(tmp_path, monkeypatch):
    """CloudWatch rejects spans / log records more than 2 h in the future. A file too long for a
    group created just now is refused before anything is sent, instead of being half rejected."""
    now = time.time()
    n = int(convert_logs.FUTURE_SAFE_S / 10) + 20
    r = _send(tmp_path, monkeypatch, n, {GROUP: now - 3, "aws/spans": now - 30 * DAY})
    assert r.rc == 1
    assert not r.spans and not r.records and not r.runs and not r.results.exists()
    assert convert_logs.FUTURE_SAFE_S < 2 * 3600


def test_refusal_boundary_matches_the_row_count_it_suggests(tmp_path, monkeypatch, capsys):
    """Group created 3 s ago -> window starts 1 s ago; 360 rows end 3598 s ahead (accepted), 361 do not."""
    now = time.time()
    ok = _send(tmp_path / "a", monkeypatch, 360, {GROUP: now - 3, "aws/spans": now - 30 * DAY}, capsys=capsys)
    assert ok.rc == 0 and len(ok.roots) == 360
    assert max(s.end_time for s in ok.spans) / 1e9 < ok.t1 + convert_logs.FUTURE_SAFE_S
    now = time.time()
    no = _send(tmp_path / "b", monkeypatch, 361, {GROUP: now - 3, "aws/spans": now - 30 * DAY}, capsys=capsys)
    assert no.rc == 1 and "不超过 360 行" in no.out


def test_server_clock_ahead_of_local_clock_still_respects_creation(tmp_path, monkeypatch):
    """creationTime comes from the server: if it is 30 s ahead of this machine, stamps follow the server."""
    now = time.time()
    r = _send(tmp_path, monkeypatch, 5, {GROUP: now + 30, "aws/spans": now - 30 * DAY})
    assert r.rc == 0
    assert min(_all_ts_ns(r)) > int((now + 30) * 1000) * 1_000_000


def test_group_created_a_hundred_seconds_ago(tmp_path, monkeypatch):
    now = time.time()
    r = _send(tmp_path, monkeypatch, 5, {GROUP: now - 100, "aws/spans": now - 30 * DAY})
    assert r.rc == 0
    assert min(_all_ts_ns(r)) > int((now - 100) * 1000) * 1_000_000
    assert r.roots[0].start_time / 1e9 > now - 120 - 50 + 1      # clamped, not the plain now-170 window


def test_hint_only_when_sessions_reach_past_the_query_window(tmp_path, monkeypatch, capsys):
    """12-A / 31 query up to now + 60 s: the 5-row sample (ends ~+48 s) prints no hint, 12 rows (~+118 s) do."""
    now = time.time()
    five = _send(tmp_path / "a", monkeypatch, 5, {GROUP: now - 3, "aws/spans": now - 30 * DAY}, capsys=capsys)
    assert five.rc == 0 and "ℹ️  日志组" not in five.out
    now = time.time()
    twelve = _send(tmp_path / "b", monkeypatch, 12, {GROUP: now - 3, "aws/spans": now - 30 * DAY}, capsys=capsys)
    assert twelve.rc == 0 and f"ℹ️  日志组 {GROUP}" in twelve.out and "再运行 ./31-run-eval.sh" in twelve.out


def test_missing_aws_spans_warns_and_bounds_by_the_target_group(tmp_path, monkeypatch, capsys):
    now = time.time()
    r = _send(tmp_path, monkeypatch, 3, {GROUP: now - 3}, capsys=capsys)
    assert r.rc == 0 and "aws/spans 日志组还不存在" in r.out
    assert min(_all_ts_ns(r)) > int((now - 3) * 1000) * 1_000_000


def test_creation_lookups_are_not_traced_as_part_of_the_imported_service(tmp_path, monkeypatch):
    """The describe calls run inside the opentelemetry-instrument process; botocore spans for them
    would show up as extra traces under s12lab-<suffix>-imported-logs."""
    now = time.time()
    r = _send(tmp_path, monkeypatch, 2, {GROUP: now - 3, "aws/spans": now - 30 * DAY})
    describes = [c for c in r.logs.calls if c[0] == "describe"]
    assert describes and not any(enabled for _, _, enabled in describes)


@pytest.mark.parametrize("n", [1, 30])
def test_short_and_medium_files_stay_inside_the_safe_future_bound(tmp_path, monkeypatch, n):
    now = time.time()
    r = _send(tmp_path, monkeypatch, n, {GROUP: now - 2, "aws/spans": now - 30 * DAY})
    assert r.rc == 0 and len(r.roots) == n
    assert min(_all_ts_ns(r)) > int((now - 2) * 1000) * 1_000_000
    assert max(s.end_time for s in r.spans) / 1e9 < r.t1 + convert_logs.FUTURE_SAFE_S
