"""Tests for labkit.failures (step 7.5: failed golden items + five-dimension tally).

Standard library + pytest only; no AWS calls.
"""
import html
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from labkit import failures, items  # noqa: E402

FX = ROOT / "tests" / "fixtures"
REAL = FX / "eval-A-v0-golden-sample.json"   # trimmed copy of a real A-path V0 golden evaluation


def _score(label, explanation=""):
    return {"value": 1.0 if label == "Pass" else 0.0, "label": label, "explanation": explanation,
            "errorCode": None, "errorMessage": None}


def _expl(fid, gid, parts):
    """The label_accuracy evaluator's explanation format (evaluators/s12_label_accuracy/label_scoring.explain)."""
    body = "；".join(f"{n} {'对' if ok else '错'}（期望 {e}，实际 {a}）" for n, ok, e, a in parts)
    return f"需求 {fid}（{gid}）：{sum(p[1] for p in parts)}/{len(parts)} 项正确。{body}。[来源 attribute，版本 v0]"


def _session(fid, acc, out=None, explanation="", per=None, **extra):
    scores = {}
    if acc is not None:
        scores["label_accuracy"] = acc if isinstance(acc, dict) else _score(acc, explanation)
    for k, v in (per or {}).items():
        scores[k] = _score(v)
    s = {"feedback_id": fid, "session_id": f"s-{fid}", "agent_output": out or {}, "agent_error": None, "scores": scores}
    s.update(extra)
    return s


GOLDEN = [
    {"golden_id": "G01", "feedback_id": "FB-1", "expected": {"important": "否", "urgent": "否", "decision": "拒绝"},
     "acceptable_decisions": ["拒绝"], "dimensions": ["认知", "质量"], "case_type": "应拒绝", "rationale": "超出平台定位"},
    {"golden_id": "G02", "feedback_id": "FB-2", "expected": {"important": "是", "urgent": "是", "decision": "迭代"},
     "acceptable_decisions": ["迭代"], "dimensions": ["责任"], "case_type": "简单", "rationale": "线上故障" * 40},
    {"golden_id": "G03", "feedback_id": "FB-3", "expected": {"important": "是", "urgent": "否", "decision": "待评估"},
     "acceptable_decisions": ["待评估", "迭代"], "dimensions": ["成本", "认知"], "case_type": "模糊", "rationale": "难度高"},
    {"golden_id": "G04", "feedback_id": "FB-4", "expected": {"important": "否", "urgent": "否", "decision": "迭代"},
     "dimensions": ["性能"], "case_type": "简单", "rationale": "x"},
    {"golden_id": "G10", "feedback_id": "FB-10", "expected": {"important": "是", "urgent": "否", "decision": "拒绝"},
     "acceptable_decisions": ["拒绝"], "dimensions": "认知、认知", "case_type": "应拒绝", "rationale": "已支持"},
    {"golden_id": "G11", "feedback_id": "FB-11", "expected": {"important": "是", "urgent": "是", "decision": "迭代"},
     "acceptable_decisions": ["迭代"], "dimensions": ["责任"], "case_type": "简单", "rationale": "安全"},
]

EVAL = {"run": {"run_id": "A-v0-T"}, "sessions": [
    _session("FB-0", "Pass"),
    # 1) the evaluator's explanation is the source of truth (it scored a normalised output)
    _session("FB-1", "Fail", out={"important": "是", "urgent": "否", "decision": "待评估"},
             explanation=_expl("FB-1", "G01", [("是否重要", False, "否", "是"), ("是否紧急", True, "否", "否"),
                                               ("处理建议", False, "拒绝", "待评估")]),
             per={"label_important": "Fail", "label_urgent": "Fail", "label_decision": "Fail"}),
    _session("FB-2", "CriticalFail", out={"important": "是", "urgent": "否", "decision": "拒绝"},
             explanation=_expl("FB-2", "G02", [("是否重要", True, "是", "是"), ("是否紧急", False, "是", "否"),
                                               ("处理建议", False, "迭代", "拒绝")]) + "严重错误：……"),
    # 2) no explanation: compare with the golden labels; 迭代 is acceptable, so only 重要 is wrong
    _session("FB-3", "Fail", out={"important": "否", "urgent": "否", "decision": "迭代"}),
    # 2) no explanation, own-agent style keys / values: normalised like the evaluator (only 建议 is wrong)
    _session("FB-10", "Fail", out=json.dumps({"is_important": True, "紧急": "no", "action": "fix"})),
    # 2) no explanation and an output the script cannot read: every field shows "—"
    _session("FB-11", "Fail", out="not json"),
    # 3) not in the golden set: per-field evaluator labels decide
    _session("FB-99", "Fail", out={"important": "是", "urgent": "否", "decision": "迭代"},
             per={"label_important": "Fail", "label_urgent": "Pass", "label_decision": "Pass"}),
    # not scored as right/wrong: listed separately, not tallied
    _session("FB-4", "FormatError", explanation="no JSON"),
    _session("FB-5", None, agent_error="RuntimeClientError: boom"),
    _session("FB-6", None, error="no spans found in CloudWatch for this session"),
    _session("FB-7", {"errorCode": "CLIENT_ERROR", "errorMessage": "ThrottlingException"}),
    _session("FB-8", "Skipped"),
    "not a session",
]}


def _golden_idx():
    return {items.fid_of(r): r for r in GOLDEN}


def test_analyze_splits_wrong_and_other_and_tallies():
    res = failures.analyze(EVAL, _golden_idx())
    assert [w["fid"] for w in res["wrong"]] == ["FB-1", "FB-2", "FB-3", "FB-10", "FB-11", "FB-99"]
    assert [o["fid"] for o in res["other"]] == ["FB-4", "FB-5", "FB-6", "FB-7", "FB-8"]
    assert res["total"] == 12                                       # the non-dict entry is ignored
    by = {w["fid"]: w for w in res["wrong"]}
    # explanation wins over per-field evaluators that (in an old version) repeated the whole-item label
    assert by["FB-1"]["fields"] == [("重要", "否", "是"), ("建议", "拒绝", "待评估")]
    assert by["FB-2"]["fields"] == [("紧急", "是", "否"), ("建议", "迭代", "拒绝")]
    assert by["FB-3"]["fields"] == [("重要", "是", "否")]
    assert by["FB-10"]["fields"] == [("建议", "拒绝", "迭代")]      # is_important=True -> 是, no -> 否, fix -> 迭代
    assert by["FB-10"]["dims"] == ["认知"]                           # string dimensions split, duplicates once
    assert by["FB-11"]["fields"] == [("重要", "是", "—"), ("紧急", "是", "—"), ("建议", "迭代", "—")]
    assert by["FB-99"]["in_golden"] is False and by["FB-99"]["dims"] == []
    assert by["FB-99"]["fields"] == [("重要", "?", "是")]
    # each dimension of each wrong item counted once; FB-4 (FormatError) not counted although it has 性能
    assert res["dims"] == {"认知": 3, "质量": 1, "责任": 2, "成本": 1, "性能": 0}
    assert res["fields"] == {"重要": 4, "紧急": 2, "建议": 4}


def test_explanation_parser_and_level_scheme():
    text = "需求 FB-9（G09）：1/2 项正确。优先级 错（期望 P1，实际 None）；处理建议 对（期望 迭代/待评估，实际 迭代）。"
    assert failures.from_explanation(text) == [("优先级", "P1", "—")]
    assert failures.from_explanation("trace 中有 Agent 回复，但解析不出约定的 JSON") is None
    ev = {"sessions": [_session("FB-1", "Fail", explanation=text)]}
    res = failures.analyze(ev, _golden_idx())
    assert res["fields"] == {"建议": 0, "优先级": 1}                 # only the fields that scheme scores


def test_batch_explanation_uses_only_this_items_block():
    text = ("批量输出 2 条：" + _expl("FB-2610-002", "G02", [("是否重要", False, "否", "是"), ("处理建议", False, "拒绝", "待评估")])
            + _expl("FB-1", "G01", [("是否重要", True, "否", "否"), ("是否紧急", False, "否", "是")]))
    assert failures.from_explanation(text, "FB-1") == [("紧急", "否", "是")]
    assert failures.from_explanation(text, "FB-404") is None             # not in the batch -> fallback
    single = _expl("FB-1", "G01", [("是否紧急", False, "否", "是")])
    assert failures.from_explanation(single, "other-id") == [("紧急", "否", "是")]   # one block: used as is


def test_fallback_normalisation_and_odd_dimensions():
    g = {"FB-3": GOLDEN[2]}                                           # acceptable 待评估 / 迭代
    s = _session("FB-3", "Fail", out={"important": "yes", "urgent": "N", "decision": "建议迭代"})
    assert failures.wrong_fields(s, g["FB-3"]) == []                   # substring 迭代, like the evaluator
    s = _session("FB-3", "Fail", out={"important": "是", "urgent": "否", "decision": "不建议迭代"})
    assert failures.wrong_fields(s, g["FB-3"]) == [("建议", "待评估/迭代", "不建议迭代")]   # negated: not 迭代
    assert failures._dims({"dimensions": 3}) == ["3"]
    assert failures._dims({"dimensions": [{"a": 1}, "认知", " 认知 "]}) == ["{'a': 1}", "认知"]
    assert failures._dims({}) == []


def test_other_reasons_name_the_cause():
    res = failures.analyze(EVAL, _golden_idx())
    why = {o["fid"]: o["reason"] for o in res["other"]}
    assert why["FB-4"].startswith("FormatError")
    assert why["FB-5"].startswith("Agent 报错") and "RuntimeClientError" in why["FB-5"]
    assert why["FB-6"].startswith("评估出错")
    assert "CLIENT_ERROR" in why["FB-7"] and "Throttling" in why["FB-7"]
    assert why["FB-8"].startswith("Skipped")


def test_render_contents_and_width(tmp_path):
    text = failures.render(EVAL, failures.analyze(EVAL, _golden_idx()), tmp_path / "e.json", tmp_path / "g.jsonl")
    assert "FB-1（G01）  五维：认知、质量  类型：应拒绝" in text
    assert "错（期望→实际）：重要 否→是；建议 拒绝→待评估" in text
    assert "⚠ 严重" in text.split("FB-2（G02）", 1)[1].splitlines()[0]
    assert "FB-99  （黄金集里没有这条" in text
    assert "认知 3 条 · 质量 1 条 · 责任 2 条 · 成本 1 条 · 性能 0 条" in text
    assert "重要 4 条 · 紧急 2 条 · 建议 4 条" in text
    assert "其他未通过（5 条，不计入下面的统计）" in text
    long_line = next(x for x in text.splitlines() if x.startswith("  理由：线上故障"))
    assert long_line.endswith("…") and failures.width(long_line) <= 90
    assert max(failures.width(x) for x in text.splitlines()[2:]) <= 100   # first two lines: (tmp) file paths


def test_fail_without_any_field_detail_says_so():
    ev = {"sessions": [_session("FB-99", "Fail", explanation="")]}
    text = failures.render(ev, failures.analyze(ev, {}), Path("e.json"), Path("g.jsonl"))
    assert "  错：（评估器没有给出逐项结果）" in text


def _real():
    ev = json.loads(REAL.read_text(encoding="utf-8"))
    golden = failures.load_golden(ROOT / "data" / "golden" / "golden_set.jsonl")
    return ev, golden, failures.analyze(ev, golden)


def test_real_eval_tallies_match_the_evaluator():
    ev, golden, res = _real()
    wrong = [s for s in ev["sessions"] if s["scores"]["label_accuracy"]["label"] in ("Fail", "CriticalFail")]
    assert len(res["wrong"]) == len(wrong) == 22 and len(res["other"]) == 1
    # field tally == what the per-field evaluators of this (current-version) run scored as wrong
    for _k, ev_name, name, _cn in failures.FIELDS:
        assert res["fields"][name] == sum(1 for s in wrong if s["scores"][ev_name]["label"] == "Fail")
    expect = Counter(d for s in wrong for d in set(golden[s["feedback_id"]]["dimensions"]))
    assert {d: n for d, n in res["dims"].items() if n} == dict(expect)
    text = failures.render(ev, res, REAL, ROOT / "data" / "golden" / "golden_set.jsonl")
    assert max(failures.width(x) for x in text.splitlines()) <= 100


def test_manual_walkthrough_numbers_match_real_output():
    _ev, _g, res = _real()
    page = (ROOT / "docs" / "07-evaluate.html").read_text(encoding="utf-8").split('id="s-7-5"', 1)[1]
    top_field = max(res["fields"], key=res["fields"].get)
    top_dim = max(res["dims"], key=res["dims"].get)
    assert (top_field, res["fields"][top_field]) == ("建议", 16) and (top_dim, res["dims"][top_dim]) == ("认知", 10)
    assert "错得最多的是“处理建议”（16 条），维度上认知最多（10 条）" in page
    # 7.2 counts a FormatError as wrong on all three fields: wrong here + the other list = 7.2's misses
    assert [res["fields"][k] + len(res["other"]) for k in ("重要", "紧急", "建议")] == [10, 5, 17]   # 67% / 83% / 43%


def test_manual_excerpt_matches_real_output():
    """The 7.5 预期输出 block in the manual is real output of this script (lines in order, … = skipped)."""
    ev, golden, res = _real()
    text = failures.render(ev, res, ROOT / "results" / "eval-A-v0-20261105T142551Z.json",
                           ROOT / "data" / "golden" / "golden_set.jsonl")
    page = (ROOT / "docs" / "07-evaluate.html").read_text(encoding="utf-8")
    sec = page.split('id="s-7-5"', 1)[1]
    block = html.unescape(re.search(r'<pre class="out">(.*?)</pre>', sec, re.S).group(1))
    want = [x for x in block.splitlines() if x.strip() and x.strip() != "…"]
    got = [x for x in text.splitlines() if x.strip()]
    pos = 0
    for line in want:
        assert line in got[pos:], f"manual line not found (in order) in real output: {line!r}"
        pos = got.index(line, pos) + 1


def test_cli_defaults_to_latest_eval(tmp_path, monkeypatch, capsys):
    (tmp_path / "results").mkdir()
    old, new = tmp_path / "results" / "eval-A-v0-old.json", tmp_path / "results" / "eval-A-v0-new.json"
    old.write_text(json.dumps({"run": {"run_id": "OLD"}, "sessions": []}), encoding="utf-8")
    new.write_text(json.dumps(EVAL, ensure_ascii=False), encoding="utf-8")
    t = time.time()
    os.utime(old, (t - 100, t - 100))
    gp = tmp_path / "golden.jsonl"
    gp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in GOLDEN) + "\n{broken\n", encoding="utf-8")
    monkeypatch.setattr(failures, "ROOT", tmp_path)
    assert failures.main(["--golden", str(gp)]) == 0
    cap = capsys.readouterr()
    assert "results/eval-A-v0-new.json" in cap.out and "run A-v0-T" in cap.out
    assert "第 7 行不是合法 JSON" in cap.err
    assert failures.main([str(old), "--golden", str(gp)]) == 0
    assert "run OLD" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="无法读取黄金集"):
        failures.main([str(old), "--golden", str(tmp_path / "missing.jsonl")])
