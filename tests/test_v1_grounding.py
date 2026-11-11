"""V1 grounding (no AWS calls).

The reject-quality judge checks a rejection against a catalog excerpt built from each capability's
name / description / limits / workaround (evaluators/s12_reject_quality/lambda_function.capability_hints).
A platform fact the agent states from any other source -- e.g. the catalog's menu path how_to, or the
model's own knowledge -- cannot be verified there and is judged "编造平台并不存在的功能或配置路径" (a red
line, one-vote veto). So V1's capability tool hands the model only those fields, and the V1 prompt keeps
every platform fact in reject_reason / customer_reply to them. V0 stays the deliberately weak baseline.
"""
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

PATH_MARKS = ("→", "->")
FACT_FIELDS = ("name", "description", "limits", "workaround")


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


def test_v1_capability_search_gives_no_menu_paths():
    pytest.importorskip("strands")
    from s12agent import tools
    seen = set()
    for c in _catalog():
        for hit in json.loads(tools.search_capabilities._tool_func(c["name"])):
            seen.add(hit["id"])
            assert "how_to" not in hit, hit
            assert set(FACT_FIELDS) | {"id", "status"} <= set(hit), hit
            assert not any(m in str(v) for v in hit.values() for m in PATH_MARKS), hit
    with_path = {c["id"] for c in _catalog() if any(m in (c.get("how_to") or "") for m in PATH_MARKS)}
    assert with_path and with_path <= seen  # the loop really covered every capability that has a menu path


def test_every_v1_tool_fact_is_visible_to_the_judge():
    """Each fact the V1 tool returns about a capability appears in the judge's excerpt line for it
    (module / effort_hint are internal classification hints the agent does not quote)."""
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


def test_v1_prompt_keeps_platform_facts_to_catalog_fields():
    v1 = (ROOT / "agent" / "s12agent" / "prompts" / "v1.md").read_text(encoding="utf-8")
    assert "how_to" not in v1
    assert "不要写菜单路径" in v1 and "search_capabilities" in v1
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
