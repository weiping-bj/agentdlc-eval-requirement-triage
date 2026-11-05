"""span_adapter: every delivery shape we know of -> triage records."""
import json
import os

import pytest

from s12_eval_helpers import REPO, load_pkg, triage_span

sa = load_pkg("s12_label_accuracy", "span_adapter")
OUT = {"feedback_id": "FB-2610-001", "important": "是", "urgent": "否", "decision": "迭代", "level": None,
       "reasoning": "r", "reject_reason": "", "customer_reply": ""}
T = "a" * 32


def test_attribute_path_preferred():
    spans = [triage_span(OUT, fid="FB-2610-001"),
             {"traceId": T, "name": "chat", "startTimeUnixNano": 99,
              "events": [{"name": "gen_ai.choice", "body": {"message": {"content": [{"text": json.dumps(dict(OUT, decision="拒绝"), ensure_ascii=False)}]}}}]}]
    ex = sa.extract_records(spans, T)
    assert ex["records"][0]["source"] == "attribute"
    assert ex["records"][0]["output"]["decision"] == "迭代"


def test_split_mode_log_record_with_block_list_string():
    text = "```json\n" + json.dumps(OUT, ensure_ascii=False) + "\n```"
    rec = {"traceId": T, "spanId": "x", "scope": {"name": "strands.telemetry.tracer"}, "timeUnixNano": 5,
           "attributes": {"event.name": "strands.telemetry.tracer"},
           "body": {"input": {"messages": [{"role": "user", "content": {"content": "需求编号: FB-2610-001\n标题: t"}}]},
                    "output": {"messages": [{"role": "assistant", "content": {"message": json.dumps([{"text": text}], ensure_ascii=False),
                                                                             "finish_reason": "end_turn"}}]}}}
    ex = sa.extract_records([rec], T)
    assert ex["records"][0]["source"] == "message"
    assert ex["records"][0]["output"]["important"] == "是"
    assert ex["user_text"].startswith("需求编号")


def test_unified_mode_span_events_snake_case_and_tool_blocks_ignored():
    final = dict(OUT)
    final.pop("feedback_id")
    span = {"trace_id": T.upper(), "name": "invoke_agent s12lab_triage_v1", "start_time": "3",
            "attributes": {"s12lab.feedback_id": "FB-2610-009"},
            "span_events": [
                {"event_name": "gen_ai.user.message", "body": {"content": [{"text": "需求编号: FB-2610-009"}]}},
                {"name": "gen_ai.choice", "body": {"message": {"content": [{"toolUse": {"name": "search_capabilities", "input": {"q": "x"}}}]}}},
                {"name": "gen_ai.choice", "body": {"message": {"content": [{"text": "结论如下：" + json.dumps(final, ensure_ascii=False) + " 完毕"}]}}},
            ]}
    ex = sa.extract_records([span], T)
    r = ex["records"][0]
    assert r["feedback_id"] == "FB-2610-009"  # from attribute (output has no id)
    assert r["output"]["decision"] == "迭代"


def test_feedback_id_from_user_text_and_trace_filter():
    other = triage_span(dict(OUT, feedback_id="FB-2610-050"), trace="b" * 32)
    final = dict(OUT)
    final.pop("feedback_id")
    mine = {"traceId": T, "name": "chat", "startTimeUnixNano": 1,
            "attributes": {"gen_ai.prompt": "需求编号: FB-2610-007\n标题: x",
                           "gen_ai.completion": json.dumps(final, ensure_ascii=False)}}
    ex = sa.extract_records([other, mine], T)
    assert [r["feedback_id"] for r in ex["records"]] == ["FB-2610-007"]
    ex2 = sa.extract_records([other, mine], "c" * 32)
    assert ex2["trace_matched"] is False


def test_xray_style_trace_id_normalised():
    span = triage_span(OUT, trace="1-5759e988-bd862e3fe1be46a994272793")
    ex = sa.extract_records([span], "5759e988bd862e3fe1be46a994272793")
    assert ex["trace_matched"] and ex["records"]


def test_batch_results_and_latest_wins():
    batch = {"results": [OUT, dict(OUT, feedback_id="FB-2610-002", decision="拒绝"), dict(OUT, decision="待评估")]}
    ex = sa.extract_records([triage_span(batch)], T)
    by = {r["feedback_id"]: r["output"]["decision"] for r in ex["records"]}
    assert by == {"FB-2610-001": "待评估", "FB-2610-002": "拒绝"}


def test_normalize_aliases_and_invalid_values():
    n = sa.normalize_output({"需求编号": "X", "是否重要": True, "是否紧急": "no", "处理建议": "建议拒绝", "优先级": "p1", "话术": "hi"})
    assert (n["feedback_id"], n["important"], n["urgent"], n["decision"], n["level"], n["customer_reply"]) == ("X", "是", "否", "拒绝", "P1", "hi")
    bad = sa.normalize_output({"id": "Y", "important": "maybe", "urgent": "是", "decision": "xyz"})
    assert bad["important"] is None and bad["decision"] is None and len(bad["_issues"]) == 2


def test_no_output_vs_unparsable():
    ex = sa.extract_records([{"traceId": T, "name": "chat", "attributes": {}}], T)
    assert ex["records"] == [] and not ex["has_assistant_text"]
    ex = sa.extract_records([{"traceId": T, "name": "chat", "attributes": {"gen_ai.completion": "我觉得应该迭代"}}], T)
    assert ex["records"] == [] and ex["has_assistant_text"]


def test_spans_as_json_strings():
    ex = sa.extract_records([json.dumps(triage_span(OUT), ensure_ascii=False), "garbage", 42], T)
    assert ex["records"]


def test_otel_sdk_event_attributes_and_event_time_ordering():
    """Shape written by labkit/convert_logs.py / the OTel SDK: events carry `attributes`, not `body`."""
    final = json.dumps(dict(OUT, decision="待评估"), ensure_ascii=False)
    draft = json.dumps(dict(OUT, decision="拒绝"), ensure_ascii=False)
    root = {"traceId": T, "name": "invoke_agent triage_agent", "startTimeUnixNano": 100,
            "attributes": {"s12lab.feedback_id": "FB-2610-001"},
            "events": [{"name": "gen_ai.user.message", "timeUnixNano": 100,
                        "attributes": {"content": json.dumps([{"text": "需求编号: FB-2610-001"}], ensure_ascii=False)}},
                       {"name": "gen_ai.choice", "timeUnixNano": 900, "attributes": {"message": final, "finish_reason": "end_turn"}}]}
    child = {"traceId": T, "name": "chat", "startTimeUnixNano": 200,
             "events": [{"name": "gen_ai.choice", "timeUnixNano": 300, "attributes": {"message": draft}}]}
    ex = sa.extract_records([root, child], T)
    assert ex["records"][0]["output"]["decision"] == "待评估"  # later event wins, not later span start
    assert ex["user_text"].startswith("需求编号")


REAL_FIXTURE = os.path.join(REPO, "telemetry", "fixtures", "local-adot-v0-sessions.json")


@pytest.mark.skipif(not os.path.exists(REAL_FIXTURE), reason="real ADOT fixture not present")
@pytest.mark.parametrize("strip_attr", [False, True])
def test_real_adot_split_mode_fixture(strip_attr):
    """Real spans from Strands 1.64 + ADOT 0.22 (split mode), as passed in evaluationInput.sessionSpans."""
    sessions = json.load(open(REAL_FIXTURE, encoding="utf-8"))
    assert sessions
    for s in sessions:
        docs = json.loads(json.dumps(s["docs"]))
        if strip_attr:  # force the message-parsing fallback (customer agents without the triage span)
            for d in docs:
                (d.get("attributes") or {}).pop("s12lab.triage_output", None)
        tids = {sa.trace_id_of(d) for d in docs if sa.trace_id_of(d)}
        found = []
        for tid in tids:
            ex = sa.extract_records(docs, tid)
            found += ex["records"]
        assert [r["feedback_id"] for r in found] == [s["feedback_id"]]
        r = found[0]
        assert r["source"] == ("message" if strip_attr else "attribute")
        assert r["output"]["decision"] in ("迭代", "拒绝", "待评估") and r["output"]["important"] in ("是", "否")
