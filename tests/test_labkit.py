"""Unit tests for the lab plumbing (no AWS calls)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

from labkit import config as labcfg  # noqa: E402
from labkit import convert_logs, items  # noqa: E402


def test_config_derives_valid_names(monkeypatch):
    monkeypatch.setenv("LAB_SUFFIX", "zhang3")
    c = labcfg.load()
    import re
    name_re = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,47}$")
    for k in ("RUNTIME_NAME", "EVALUATOR_LABEL_NAME", "EVALUATOR_JUDGE_NAME", "ONLINE_EVAL_NAME"):
        assert name_re.match(c[k]), (k, c[k])
    assert c["RUNTIME_NAME"] == "s12lab_zhang3_agent"
    assert c["LAB_NAME"] == "s12lab-zhang3"
    for k in ("RUNTIME_ROLE_NAME", "EVAL_LAMBDA_ROLE_NAME", "ONLINE_EVAL_ROLE_NAME"):
        assert len(c[k]) <= 64 and c[k].startswith("s12lab-zhang3-")
        assert not c[k].startswith("s12lab-ec2-"), "s12lab-ec2-* roles are protected by the EC2 role's Deny"


def test_config_rejects_bad_suffix(monkeypatch):
    monkeypatch.setenv("LAB_SUFFIX", "Bad-Suffix")
    with pytest.raises(labcfg.ConfigError):
        labcfg.load()


def test_env_overrides_file(monkeypatch):
    monkeypatch.setenv("LAB_SUFFIX", "abc")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    assert labcfg.load()["AWS_REGION"] == "us-east-1"


def test_parse_env_file(tmp_path):
    p = tmp_path / "x.env"
    p.write_text("# c\nA=1\nexport B='two'\nC=\"3\"\n\nbad line\n", encoding="utf-8")
    assert labcfg.parse_env_file(p) == {"A": "1", "B": "two", "C": "3"}


def test_golden_rows_have_input_and_expected():
    rows = items.load("golden")
    assert len(rows) >= 20
    for r in rows:
        it = items.item_of(r)
        assert items.fid_of(r) and it.get("title") and it.get("content")
        exp = items.expected_of(r)
        assert exp["important"] in ("是", "否") and exp["urgent"] in ("是", "否")
        assert exp["decision"] in ("迭代", "拒绝", "待评估")


def test_triage_json_aliases_and_text():
    assert convert_logs.triage_json('前缀 {"是否重要":"是","是否紧急":"否","处理建议":"拒绝"} 后缀')["decision"] == "拒绝"
    assert convert_logs.triage_json("纯文本，没有 JSON") is None
    assert convert_logs.triage_json({"important": "否"})["important"] == "否"


def test_sample_customer_logs_pass_check(capsys):
    rc = convert_logs.check(str(ROOT / "telemetry" / "samples" / "customer-agent-logs.jsonl"))
    assert rc == 0
    assert "字段检查通过" in capsys.readouterr().out


def test_check_flags_missing_required(tmp_path, capsys):
    p = tmp_path / "bad.jsonl"
    p.write_text(json.dumps({"session_id": "s1", "timestamp": "2026-10-01T00:00:00Z"}) + "\nnot json\n", encoding="utf-8")
    assert convert_logs.check(str(p)) == 1
    out = capsys.readouterr().out
    assert "缺少必填字段" in out and "不是合法 JSON" in out


def test_agent_parse_and_format():
    pytest.importorskip("strands")
    from s12agent import core
    out = core.parse_output('```json\n{"feedback_id":"FB-1","important":"是","urgent":"否","decision":"迭代"}\n```')
    assert out["decision"] == "迭代" and "_warnings" not in out
    bad = core.parse_output('{"important":"maybe","urgent":"否","decision":"x"}')
    assert len(bad["_warnings"]) == 2
    assert "_parse_error" in core.parse_output("no json here")
    text = core.format_input({"id": "FB-1", "title": "t", "content": "c", "feedback_type": "功能缺陷"})
    assert text.splitlines()[0] == "需求编号: FB-1" and "反馈类型: 功能缺陷" in text


def test_v0_v1_prompts_and_toolsets():
    pytest.importorskip("strands")
    from s12agent import core, tools
    assert set(tools.TOOLSETS) == {"v0", "v1"}
    assert len(tools.TOOLSETS["v1"]) > len(tools.TOOLSETS["v0"])
    v0, v1 = core.system_prompt("v0"), core.system_prompt("v1")
    assert len(v1) > 2 * len(v0)
    for key in ("feedback_id", "important", "urgent", "decision", "reject_reason", "customer_reply"):
        assert key in v0 and key in v1


def test_v1_tools_find_catalog_and_similar_requests():
    pytest.importorskip("strands")
    from s12agent import tools
    caps = json.loads(tools.search_capabilities._tool_func("列表页批量编辑自定义字段"))
    assert any("批量" in c.get("name", "") for c in caps)
    sim = json.loads(tools.find_similar_requests._tool_func("列表页批量修改自定义字段"))
    assert sim["distinct_customers"] >= 2
