"""The own-agent snippet (telemetry/snippets/a34_session_wrapper.py) works, and the own-agent guide
(docs/own-agent-guide.html, G.5) shows exactly that file.

Runs the snippet around a fake agent with an in-memory OTel exporter (no AWS). ADOT's
AGENT_OBSERVABILITY_ENABLED=true copies the baggage entry session.id onto every span; here the
same is done by the OTel BaggageSpanProcessor. Then our span adapter and the label-accuracy
evaluator read the captured spans, as AgentCore Evaluations would hand them over.
"""
import html
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SNIPPET = ROOT / "telemetry" / "snippets" / "a34_session_wrapper.py"
sys.path.insert(0, str(ROOT / "evaluators" / "tests"))
sys.path.insert(0, str(ROOT / "evaluators" / "shared"))

pytest.importorskip("opentelemetry.sdk")
pytest.importorskip("opentelemetry.processor.baggage")

from opentelemetry import baggage, trace  # noqa: E402
from opentelemetry.processor.baggage import ALLOW_ALL_BAGGAGE_KEYS, BaggageSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

import span_adapter  # noqa: E402
from s12_eval_helpers import event, load_pkg, output_from_gold  # noqa: E402


@pytest.fixture
def otel():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(BaggageSpanProcessor(ALLOW_ALL_BAGGAGE_KEYS))  # what ADOT does with session.id
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield provider, exporter
    provider.shutdown()


def _load_snippet(provider, agent):
    ns = {"__name__": "a34_snippet"}
    exec(compile(SNIPPET.read_text(encoding="utf-8"), str(SNIPPET), "exec"), ns)
    ns["tracer"] = provider.get_tracer("s12lab.triage")  # same tracer name, local provider (no global state)
    ns["my_agent"] = agent
    return ns["handle_one"]


def _as_dicts(spans):
    """ReadableSpan -> the dict shape AgentCore Evaluations passes to custom evaluators (sessionSpans)."""
    out = []
    for s in spans:
        out.append({"traceId": format(s.context.trace_id, "032x"), "spanId": format(s.context.span_id, "016x"),
                    "name": s.name, "scope": {"name": s.instrumentation_scope.name},
                    "startTimeUnixNano": s.start_time, "endTimeUnixNano": s.end_time,
                    "attributes": dict(s.attributes or {})})
    return out


def _gold():
    rows = [json.loads(line) for line in (ROOT / "data" / "golden" / "golden_set.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    return next(r for r in rows if r["golden_id"] == "G01")


def _fake_agent(provider, out):
    tracer = provider.get_tracer("fake.framework")

    def agent(item):  # stands in for any framework: it makes its own child spans
        with tracer.start_as_current_span("invoke_agent"):
            with tracer.start_as_current_span("chat"):
                pass
            with tracer.start_as_current_span("execute_tool find_similar_requests"):
                pass
        return out
    return agent


def test_snippet_tags_every_span_and_writes_the_output(otel):
    provider, exporter = otel
    g = _gold()
    out = output_from_gold(g)
    handle_one = _load_snippet(provider, _fake_agent(provider, out))
    assert handle_one({"id": g["feedback_id"], "title": "t", "content": "c"}) == out
    spans = exporter.get_finished_spans()
    assert len(spans) == 4
    sids = {s.attributes.get("session.id") for s in spans}
    assert len(sids) == 1 and next(iter(sids)).startswith("s12lab-")            # every span, one session
    assert len({s.context.trace_id for s in spans}) == 1                       # one trace per item
    wrapper = next(s for s in spans if s.name == "s12lab.triage")
    assert wrapper.attributes["s12lab.feedback_id"] == g["feedback_id"]
    assert json.loads(wrapper.attributes["s12lab.triage_output"]) == out
    assert baggage.get_baggage("session.id") is None                            # detached afterwards


def test_two_items_get_two_sessions_and_traces(otel):
    provider, exporter = otel
    g = _gold()
    handle_one = _load_snippet(provider, _fake_agent(provider, output_from_gold(g)))
    handle_one({"id": "FB-1"})
    handle_one({"id": "FB-2"})
    spans = exporter.get_finished_spans()
    assert len({s.attributes["session.id"] for s in spans}) == 2 and len({s.context.trace_id for s in spans}) == 2


def test_context_is_detached_when_the_agent_fails(otel):
    provider, _ = otel

    def broken(item):
        raise RuntimeError("model timeout")
    handle_one = _load_snippet(provider, broken)
    with pytest.raises(RuntimeError):
        handle_one({"id": "FB-1"})
    assert baggage.get_baggage("session.id") is None


def test_our_adapter_and_label_evaluator_read_the_captured_spans(otel):
    provider, exporter = otel
    g = _gold()
    out = output_from_gold(g)
    _load_snippet(provider, _fake_agent(provider, out))({"id": g["feedback_id"]})
    spans = _as_dicts(exporter.get_finished_spans())
    tid = spans[0]["traceId"]
    rec = span_adapter.extract_records(spans, tid)
    assert rec["trace_matched"] and len(rec["records"]) == 1
    r = rec["records"][0]
    assert r["source"] == "attribute" and r["feedback_id"] == g["feedback_id"]
    assert (r["output"]["important"], r["output"]["urgent"], r["output"]["decision"]) == (
        g["expected"]["important"], g["expected"]["urgent"], g["expected"]["decision"])
    res = load_pkg("s12_label_accuracy").handler(event(spans, trace=tid))
    assert res["label"] == "Pass" and res["value"] == 1.0, res


def test_guide_shows_exactly_the_snippet_file():
    page = (ROOT / "docs" / "own-agent-guide.html").read_text(encoding="utf-8")
    g5 = page.split('id="s-g-5"', 1)[1].split('id="s-g-6"', 1)[0]
    blocks = [html.unescape(b) for b in re.findall(r"<pre><code>(.*?)</code></pre>", g5, re.S)]
    shown = [b for b in blocks if "def handle_one" in b]
    assert len(shown) == 1
    assert shown[0].rstrip("\n") == SNIPPET.read_text(encoding="utf-8").rstrip("\n")
    assert 'data-repo="telemetry/snippets/a34_session_wrapper.py"' in g5
    # the on-site A3.4 explains the mock agent's own code and points to the guide instead
    a34 = (ROOT / "docs" / "a3-instrument.html").read_text(encoding="utf-8").split('id="s-a3-4"', 1)[1]
    a34 = a34.split('id="s-a3-5"', 1)[0]
    assert "def handle_one" not in a34 and 'href="own-agent-guide.html#s-g-5"' in a34


def test_feedback_id_key_variants_and_the_attribute_fallback(otel):
    provider, exporter = otel
    g = _gold()
    out = {k: v for k, v in output_from_gold(g).items() if k != "feedback_id"}   # agent output without the id
    _load_snippet(provider, _fake_agent(provider, out))({"feedback_id": g["feedback_id"]})  # other key name
    spans = _as_dicts(exporter.get_finished_spans())
    wrapper = next(s for s in spans if s["name"] == "s12lab.triage")
    assert wrapper["attributes"]["s12lab.feedback_id"] == g["feedback_id"]
    rec = span_adapter.extract_records(spans, spans[0]["traceId"])
    assert rec["records"][0]["feedback_id"] == g["feedback_id"]                # read from the span attribute
    _load_snippet(provider, _fake_agent(provider, {"x": object()}))({})       # no id, odd result: no exception
