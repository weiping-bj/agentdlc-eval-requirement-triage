"""V1 grounding (no AWS calls).

The reject-quality judge checks a rejection against a catalog excerpt built from each capability's
name / description / limits / workaround / how_to (menu path) (evaluators/s12_reject_quality/lambda_function.capability_hints).
A platform fact the agent states from any other source -- e.g. a menu path it made up or rephrased, or the
model's own knowledge -- cannot be verified there (unless the golden reply points happen to list it) and
tends to be judged "编造平台并不存在的功能或配置路径" (a red line, one-vote veto). So V1's capability tool hands the
model exactly those fields, and the V1 prompt keeps every platform fact in reject_reason / customer_reply to them
(a menu path only as the verbatim how_to). V0 stays the deliberately weak baseline.
"""
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

PATH_MARKS = ("→", "->")
FACT_FIELDS = ("name", "description", "limits", "workaround", "how_to")


def _catalog() -> list:
    return json.loads((ROOT / "data" / "capability_catalog.json").read_text(encoding="utf-8"))["capabilities"]


def _judge_hints():
    """capability_hints of the reject-quality evaluator package, imported in isolation."""
    d = str(ROOT / "evaluators" / "s12_reject_quality")
    for m in ("lambda_function", "span_adapter", "golden_store", "judge", "rubric", "pillars"):
        sys.modules.pop(m, None)
    sys.path.insert(0, d)
    try:
        return importlib.import_module("lambda_function").capability_hints
    finally:
        sys.path.remove(d)
        for m in ("lambda_function", "span_adapter", "golden_store", "judge", "rubric", "pillars"):
            sys.modules.pop(m, None)


def test_v1_capability_search_returns_the_catalog_menu_path():
    pytest.importorskip("strands")
    from s12agent import tools
    caps = {c["id"]: c for c in _catalog()}
    seen = set()
    for c in _catalog():
        for hit in json.loads(tools.search_capabilities._tool_func(c["name"])):
            seen.add(hit["id"])
            assert set(FACT_FIELDS) | {"id", "status"} <= set(hit), hit
            assert hit["how_to"] == (caps[hit["id"]].get("how_to") or ""), hit  # verbatim catalog path, nothing else
            assert not any(m in str(v) for f, v in hit.items() if f != "how_to" for m in PATH_MARKS), hit
    with_path = {c["id"] for c in _catalog() if any(m in (c.get("how_to") or "") for m in PATH_MARKS)}
    assert with_path and with_path <= seen  # the loop really covered every capability that has a menu path


def test_v1_tool_fields_match_the_judge_excerpt_line():
    """Each fact field the V1 tool returns for a capability is the same text the judge's excerpt line shows for
    it, once that capability is in the excerpt (module / effort_hint are internal hints the agent does not quote).
    Whether it gets into the top-5 excerpt is the citation test below."""
    pytest.importorskip("strands")
    from s12agent import tools
    hints = _judge_hints()
    for c in _catalog():
        hit = next(h for h in json.loads(tools.search_capabilities._tool_func(c["name"])) if h["id"] == c["id"])
        line = next((l for l in hints(c["name"] + " " + c["description"], k=50).splitlines()
                     if l.startswith(f"- {c['id']} ")), "")
        assert line, c["id"]
        for f, v in hit.items():
            if f not in ("id", "module", "effort_hint") and v:
                assert str(v) in line, (c["id"], f, v)


def test_citing_capabilities_in_reject_reason_puts_them_in_the_judge_excerpt():
    """v1.md asks reject_reason to name every capability the reply relies on, with its catalog wording. The
    judge's excerpt is the top 5 (default k) by overlap with requirement + reject_reason, so a citation in that
    form brings each golden capability into the excerpt; without it some fall outside the top 5. (A looser
    citation in a real run can still miss: this checks the rule's intent, the runs check the behaviour.)"""
    pytest.importorskip("strands")
    v1 = (ROOT / "agent" / "s12agent" / "prompts" / "v1.md").read_text(encoding="utf-8")
    assert "话术里用到的每一项平台能力，都在这里写出编号、名称和依据" in v1
    from s12agent.core import format_input
    hints = _judge_hints()
    caps = {c["id"]: c for c in _catalog()}
    rows = [json.loads(l) for l in (ROOT / "data" / "golden" / "golden_set.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()]
    rejects = [g for g in rows if g["expected"]["decision"] == "拒绝" and g.get("matched_capabilities")]
    assert rejects
    missing_without, missing_with = [], []
    for g in rejects:
        req = format_input(g["input"])
        cited = "；".join(f"{i} {caps[i]['name']}（{caps[i]['status']}）：{caps[i]['description']} 限制：{caps[i]['limits'] or '无'}"
                         f" 替代：{caps[i]['workaround'] or '无'}" for i in g["matched_capabilities"])
        plain, with_cite = hints(req), hints(req + " " + cited)
        missing_without += [(g["golden_id"], i) for i in g["matched_capabilities"] if f"- {i} " not in plain]
        missing_with += [(g["golden_id"], i) for i in g["matched_capabilities"] if f"- {i} " not in with_cite]
    assert missing_with == []
    assert missing_without  # the citation rule does real work for at least one golden rejection


def test_v1_prompt_keeps_platform_facts_to_catalog_fields():
    v1 = (ROOT / "agent" / "s12agent" / "prompts" / "v1.md").read_text(encoding="utf-8")
    assert "菜单路径只能原样引用本次 `search_capabilities` 结果里该功能的 how_to" in v1  # never an invented path
    assert "不增减、不改写其中任何一级" in v1 and "就不写路径" in v1
    assert "不要写菜单路径" not in v1
    assert "不出现能力编号" in v1 and "相似需求的客户数" in v1  # internal ids / demand counts stay out of replies
    assert "暗示以后会做" in v1  # no "not yet" wording for capabilities the catalog marks as not planned
    for f in FACT_FIELDS:
        assert f in v1
    for key in ("feedback_id", "important", "urgent", "decision", "level", "reasoning", "reject_reason", "customer_reply"):
        assert f'"{key}"' in v1  # output JSON contract unchanged


def test_v0_baseline_tool_still_returns_names_only():
    pytest.importorskip("strands")
    from s12agent import tools
    assert tools.TOOLSETS["v0"] == [tools.lookup_capability]
    names = json.loads(tools.lookup_capability._tool_func("审批"))
    assert names and all(isinstance(n, str) for n in names)
