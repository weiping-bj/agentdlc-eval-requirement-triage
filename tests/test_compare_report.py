"""Tests for the static page generators labkit.compare (V0 vs V1) and labkit.report (triage report).

Standard library + pytest only; no AWS calls. Pages are generated from tests/fixtures into tmp_path.
"""
import html
import json
import shutil
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from labkit import compare, report  # noqa: E402
from labkit import config as labcfg  # noqa: E402

FX = ROOT / "tests" / "fixtures"
V0 = FX / "eval-sample-v0.json"
V1 = FX / "eval-sample-v1.json"
RUN = FX / "run-sample-feedback.jsonl"
V0_ID, V1_ID, RUN_ID = "B-v0-20261105T130000Z", "B-v1-20261105T150000Z", "B-v1-20261105T160000Z"
INJ = '<script>alert("xss")</script>'
INJ_ESC = html.escape(INJ, quote=True)
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
SINGLE = ("label_important", "label_urgent", "label_decision")


class TagBalance(HTMLParser):
    """Strict-enough well-formedness check: every non-void element is closed in order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.errors, self.scripts = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.scripts += 1
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.errors.append(f"unexpected </{tag}>, open: {self.stack[-4:]}")


def well_formed(page: str) -> TagBalance:
    p = TagBalance()
    p.feed(page)
    p.close()
    assert not p.errors, p.errors[:5]
    assert p.stack == [], p.stack
    assert page.lstrip().startswith("<!DOCTYPE html>")
    return p


def last_line(capsys) -> str:
    return capsys.readouterr().out.strip().splitlines()[-1]


def correct_count(session: dict):
    """Independent re-implementation: number of single-label evaluators with value == 1 (agent error = 0)."""
    if session.get("agent_error"):
        return 0
    vals = [(session.get("scores") or {}).get(k, {}).get("value") for k in SINGLE]
    return sum(1 for v in vals if v == 1) if all(v is not None for v in vals) else None


def expected_changes() -> dict:
    b = {s["feedback_id"]: correct_count(s) for s in json.loads(V0.read_text(encoding="utf-8"))["sessions"]}
    a = {s["feedback_id"]: correct_count(s) for s in json.loads(V1.read_text(encoding="utf-8"))["sessions"]}
    out = {"fixed": 0, "regressed": 0, "wrong": 0, "kept": 0}
    for fid, na in a.items():
        nb = b[fid]
        if na == 3:
            out["kept" if nb == 3 else "fixed"] += 1
        elif na < nb:
            out["regressed"] += 1
        else:
            out["wrong"] += 1
    return out


def mean_of(path: Path, name: str) -> float:
    vals = [s["scores"][name]["value"] for s in json.loads(path.read_text(encoding="utf-8"))["sessions"]
            if name in s.get("scores", {}) and isinstance(s["scores"][name].get("value"), (int, float))]
    return sum(vals) / len(vals)


# ------------------------------------------------------------------------------------------ compare


def test_compare_page_from_fixtures(tmp_path, capsys):
    out = tmp_path / "compare.html"
    assert compare.main(["--before", str(V0), "--after", str(V1), "--out", str(out)]) == 0
    assert Path(last_line(capsys)) == out.resolve()
    page = out.read_text(encoding="utf-8")
    for s in ("是否重要", "是否紧急", "处理建议", "70%", V0_ID, V1_ID, "三项全对率", "拒绝话术质量", "误拒（CriticalFail）",
              "Builtin.Correctness", "Builtin.Helpfulness", "路线 B", "us.anthropic.claude-sonnet-5-5", "us-west-2",
              "数据为合成数据；分数来自 AgentCore Evaluations（按需评估）", "stroke-dasharray", "<svg"):
        assert s in page, s
    for name in SINGLE:  # KPI values come from the single-label evaluator means
        assert f"{mean_of(V0, name) * 100:.1f}%" in page and f"{mean_of(V1, name) * 100:.1f}%" in page
    ch = expected_changes()
    assert ch["fixed"] > 0 and ch["regressed"] > 0
    assert f"修复 {ch['fixed']} 条" in page and f"退步 {ch['regressed']} 条" in page
    assert f"仍错误 {ch['wrong']} 条" in page and f"保持正确 {ch['kept']} 条" in page
    assert page.count('data-tags="fixed') == ch["fixed"]
    assert page.count('data-tags="regressed') == ch["regressed"]
    assert "✓ 达标" in page                       # V1 meets the target in the fixture
    assert "⚠ V0 误拒" in page                    # CriticalFail highlighted in the per-case matrix
    assert "不适用" in page                       # non-rejected items: gray not-applicable pill
    assert "期望 " in page                        # expected labels joined from the golden set
    assert well_formed(page).scripts == 1


def test_compare_escapes_injected_text(tmp_path):
    v1 = json.loads(V1.read_text(encoding="utf-8"))
    s0 = v1["sessions"][0]
    s0["agent_output"].update(decision="拒绝", customer_reply=INJ, reasoning=INJ, reject_reason=INJ)
    s0["feedback_id"] = "FB-<b>x</b>"
    s0["scores"]["label_urgent"]["explanation"] = INJ
    s0["scores"]["reject_quality"] = {"value": 0.8, "label": "Pass", "explanation": INJ}
    v1["run"]["model"] = INJ
    v1["evaluators"]["label_accuracy"] = INJ
    after = tmp_path / "eval-inj.json"
    after.write_text(json.dumps(v1, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "c.html"
    assert compare.main(["--before", str(V0), "--after", str(after), "--out", str(out)]) == 0
    page = out.read_text(encoding="utf-8")
    assert INJ not in page and "<b>x</b>" not in page
    assert INJ_ESC in page and "FB-&lt;b&gt;x&lt;/b&gt;" in page
    assert well_formed(page).scripts == 1  # only the page's own filter script


def test_compare_tolerates_missing_keys_and_error_entries(tmp_path):
    before = {"sessions": [
        {"feedback_id": "FB-1", "agent_output": {"important": "是"},
         "scores": {"label_important": {"errorCode": "CLIENT_ERROR", "errorMessage": "ThrottlingException: Rate exceeded"},
                    "label_accuracy": {"value": "0.67", "label": "Fail"}}},
        {"feedback_id": "FB-2", "agent_output": '{"decision": "拒绝", "customer_reply": "好的"}',
         "scores": {"label_decision": 1, "reject_quality": {"label": "不适用"}}},
        {"feedback_id": "FB-3", "agent_output": None, "agent_error": "ReadTimeoutError"},
        {"feedback_id": "FB-4", "agent_output": ["not", "a", "dict"], "scores": None},
        {"session_id": "no-feedback-id"},
        "garbage",
    ]}
    after = {"run": {"run_id": "R-after", "version": "v1", "mode": "local"},
             "summary": {"metrics": {"Builtin.Correctness": {"mean": 0.9, "n": 10}}, "three_labels": {"是否紧急": 0.75}},
             "sessions": [
                 {"feedback_id": "FB-1", "scores": {
                     "label_important": {"value": 1.0, "label": "Pass"}, "label_urgent": {"value": 0.0},
                     "label_decision": {"value": None, "label": "Skipped"},
                     "label_accuracy": {"value": 0.0, "label": "FormatError"},
                     "reject_quality_llm": {"value": 0.0, "label": "不适用"}}},
                 {"feedback_id": "FB-9", "agent_output": {"decision": "拒绝"},
                  "scores": {"label_accuracy": {"value": 0.67, "label": "CriticalFail"}}},
             ]}
    pb, pa = tmp_path / "eval-b.json", tmp_path / "eval-a.json"
    pb.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")
    pa.write_text(json.dumps(after, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "c.html"
    assert compare.main(["--before", str(pb), "--after", str(pa), "--out", str(out)]) == 0
    page = out.read_text(encoding="utf-8")
    assert "R-after" in page and "A 版" in page
    assert "评估对比 ? vs V1" in page             # missing version -> "?", run_id falls back to the file name
    assert "格式错误" in page and "⚠ V1 误拒" in page
    well_formed(page)
    # an empty object is still a renderable (if useless) eval file
    empty = tmp_path / "eval-empty.json"
    empty.write_text("{}", encoding="utf-8")
    assert compare.main(["--before", str(empty), "--after", str(empty), "--out", str(tmp_path / "e.html")]) == 0
    well_formed((tmp_path / "e.html").read_text(encoding="utf-8"))


def test_metric_excludes_not_applicable_skipped_and_errors():
    sessions = [{"scores": {"reject_quality": e}} for e in (
        {"value": 0.8, "label": "Pass"}, {"value": 0.6, "label": "Fail"}, {"value": None, "label": "不适用"},
        {"value": 0.0, "label": "不适用"}, {"value": 0.0, "label": "Skipped"}, {"errorCode": "CLIENT_ERROR", "errorMessage": "x"},
        {"label": "Pass"})] + [{"scores": {}}, {}]
    m = compare.metric(compare.EvalRun({"sessions": sessions}, "eval-x.json"), "reject_quality")
    assert m["mean"] == pytest.approx(0.7) and m["n"] == 2 and m["errors"] == 1 and m["skipped"] == 4
    # falls back to summary.metrics when no session carries the evaluator
    ev = compare.EvalRun({"summary": {"metrics": {"Builtin.Helpfulness": {"mean": 0.5, "n": 4}}}}, "eval-y.json")
    assert compare.metric(ev, "Builtin.Helpfulness") == {"mean": 0.5, "n": 4, "errors": 0, "skipped": 0}


def test_target_accuracy_default(monkeypatch):
    assert compare.target_accuracy() == pytest.approx(float(labcfg.load(require_suffix=False)["TARGET_ACCURACY"]))

    def boom(**_kw):
        raise RuntimeError("no config")

    monkeypatch.setattr(labcfg, "load", boom)
    assert compare.target_accuracy() == pytest.approx(0.70)


def _lab_root(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(labcfg, "ROOT", tmp_path)
    (tmp_path / "results").mkdir()
    (tmp_path / ".lab-state").mkdir()
    return tmp_path


def test_compare_defaults_from_lab_state(tmp_path, monkeypatch, capsys):
    root = _lab_root(tmp_path, monkeypatch)
    shutil.copy(V0, root / "results" / f"eval-{V0_ID}.json")
    shutil.copy(V1, root / "results" / f"eval-{V1_ID}.json")
    late = json.loads(V0.read_text(encoding="utf-8"))
    late["run"]["run_id"] = "B-v0-late"
    (root / "results" / "eval-B-v0-late.json").write_text(json.dumps(late, ensure_ascii=False), encoding="utf-8")
    evals = [{"run_id": V0_ID, "file": f"results/eval-{V0_ID}.json", "version": "v0", "mode": "runtime"},
             {"run_id": V1_ID, "file": f"results/eval-{V1_ID}.json", "version": "v1", "mode": "runtime"},
             {"run_id": "B-v0-late", "file": "results/eval-B-v0-late.json", "version": "v0", "mode": "runtime"}]
    (root / ".lab-state" / "evals.json").write_text(json.dumps(evals), encoding="utf-8")
    assert compare.main([]) == 0
    rel = f"results/compare-{V0_ID}-vs-{V1_ID}.html"
    assert last_line(capsys) == rel          # repo-relative: the 32-compare.sh wrapper greps results/...html
    page = (root / rel).read_text(encoding="utf-8")
    assert V0_ID in page and V1_ID in page and "B-v0-late" not in page
    assert "#topbar" in page                  # fallback CSS when docs/assets/style.css is absent
    well_formed(page)
    # run_id arguments resolve through evals.json too
    assert compare.main(["--before", "B-v0-late", "--after", V1_ID, "--out", str(root / "x.html")]) == 0
    assert "B-v0-late" in (root / "x.html").read_text(encoding="utf-8")


def test_compare_falls_back_to_last_two_entries(tmp_path, monkeypatch, capsys):
    root = _lab_root(tmp_path, monkeypatch)
    for rid in ("B-v1-a", "B-v1-b"):
        d = json.loads(V1.read_text(encoding="utf-8"))
        d["run"]["run_id"] = rid
        (root / "results" / f"eval-{rid}.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    evals = [{"run_id": r, "file": f"results/eval-{r}.json", "version": "v1"} for r in ("B-v1-a", "B-v1-b")]
    (root / ".lab-state" / "evals.json").write_text(json.dumps(evals), encoding="utf-8")
    assert compare.main([]) == 0
    assert last_line(capsys) == "results/compare-B-v1-a-vs-B-v1-b.html"
    page = (root / "results" / "compare-B-v1-a-vs-B-v1-b.html").read_text(encoding="utf-8")
    assert "V1（前）" in page and "V1（后）" in page


def test_compare_without_state_explains_what_to_do(tmp_path, monkeypatch):
    _lab_root(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as e:
        compare.main([])
    assert "evals.json" in str(e.value)


# ------------------------------------------------------------------------------------------- report


def _run_counts() -> dict:
    c = {"迭代": 0, "拒绝": 0, "待评估": 0, "err": 0}
    for line in RUN.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        res = r.get("result") or {}
        if r.get("error") or not res or res.get("_parse_error"):
            c["err"] += 1
        else:
            c[res["decision"]] += 1
    return c


def test_report_from_fixture(tmp_path, capsys):
    fb = tmp_path / "feedback.jsonl"
    fb.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in (
        {"id": "FB-2610-002", "title": "<img src=x onerror=alert(1)>首页嵌入", "customer": "示例公司（示例）",
         "customer_tier": "战略", "module": "对象与字段"},
        {"id": "FB-2610-048", "title": "导出 Excel 日期变成数字", "module": "数据导入导出", "feedback_type": "功能缺陷"},
    )), encoding="utf-8")
    out = tmp_path / "report.html"
    assert report.main(["--run", str(RUN), "--feedback", str(fb), "--out", str(out)]) == 0
    assert Path(last_line(capsys)) == out.resolve()
    page = out.read_text(encoding="utf-8")
    c = _run_counts()
    total = sum(c.values())
    assert total == 60 and c["err"] == 2
    assert (f"本次共分诊 {total} 条需求反馈：建议迭代 {c['迭代']} 条、自动拒绝 {c['拒绝']} 条（已附客户沟通话术）、"
            f"待人工评估 {c['待评估']} 条；{c['err']} 条处理失败") in page
    for s in (RUN_ID, "重要 × 紧急 矩阵", "重要且紧急", "不重要不紧急", "按模块分布", "数据导入导出", "自动拒绝清单",
              "待评估清单", "处理失败", "FB-2610-047", "ReadTimeoutError", "输出解析失败", "⚠ 需人工复核：功能缺陷"):
        assert s in page, s
    rejected = [json.loads(x)["result"] for x in RUN.read_text(encoding="utf-8").splitlines()
                if (json.loads(x).get("result") or {}).get("decision") == "拒绝"]
    for r in rejected:  # full reject_reason + customer_reply text, escaped
        assert html.escape(r["customer_reply"].strip(), quote=True) in page
        assert html.escape(r["reject_reason"].strip(), quote=True) in page
    assert "<img src=x" not in page and "&lt;img src=x onerror=alert(1)&gt;" in page
    assert "数据为合成数据" in page
    assert well_formed(page).scripts == 1


def test_report_escapes_and_tolerates_bad_records(tmp_path):
    rows = [
        {"run_id": "R1", "feedback_id": "FB-X1", "version": "v1", "mode": "local",
         "result": {"important": "是", "urgent": "否", "decision": "拒绝", "reject_reason": INJ, "customer_reply": INJ,
                    "reasoning": INJ}},
        {"run_id": "R1", "feedback_id": "FB-X2", "result": json.dumps({"important": True, "urgent": "no", "decision": "迭代"})},
        {"run_id": "R1", "feedback_id": "FB-X3", "result": None},
        {"run_id": "R1", "feedback_id": "FB-X4", "result": {"important": "maybe", "decision": "以后再说"}},
        {"run_id": "R1", "result": {"feedback_id": "FB-X5", "是否重要": "否", "是否紧急": "是", "处理建议": "待评估",
                                    "level": None}, "latency_ms": "abc"},
        {"run_id": "R1", "feedback_id": "FB-X6", "result": {"important": "是", "urgent": "是", "decision": "待评估"},
         "raw": INJ, "latency_ms": 1200},
    ]
    run = tmp_path / "run-R1.jsonl"
    run.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\nnot json\n[1, 2]\n\n", encoding="utf-8")
    out = tmp_path / "r.html"
    assert report.main(["--run", str(run), "--out", str(out), "--feedback", str(tmp_path / "missing.jsonl")]) == 0
    page = out.read_text(encoding="utf-8")
    assert INJ not in page and INJ_ESC in page
    for s in ("FB-X5", "处理建议取值无效：以后再说", "Agent 没有返回结果", "不是合法 JSON", "不是 JSON 对象", "A 版"):
        assert s in page, s
    assert well_formed(page).scripts == 1


def test_report_defaults_to_latest_feedback_run(tmp_path, monkeypatch, capsys):
    root = _lab_root(tmp_path, monkeypatch)
    shutil.copy(RUN, root / "results" / f"run-{RUN_ID}.jsonl")
    (root / "results" / "run-OLD.jsonl").write_text(
        json.dumps({"run_id": "OLD", "feedback_id": "FB-1", "result": {"important": "否", "urgent": "否", "decision": "拒绝"}}) + "\n",
        encoding="utf-8")
    runs = [{"run_id": "OLD", "file": "results/run-OLD.jsonl", "source": "feedback", "mode": "local", "version": "v0"},
            {"run_id": RUN_ID, "file": f"results/run-{RUN_ID}.jsonl", "source": "feedback", "mode": "runtime",
             "version": "v1", "model": "us.anthropic.claude-sonnet-5-5", "created": "20261105T160000Z"},
            {"run_id": "GOLD", "file": "results/run-GOLD.jsonl", "source": "golden", "mode": "runtime", "version": "v1"}]
    (root / ".lab-state" / "runs.json").write_text(json.dumps(runs), encoding="utf-8")
    assert report.main([]) == 0
    rel = f"results/triage-report-{RUN_ID}.html"
    assert last_line(capsys) == rel
    page = (root / rel).read_text(encoding="utf-8")
    assert RUN_ID in page and "OLD" not in page
    assert "未找到需求明细文件" in page        # no data/ dir under the temporary root
    well_formed(page)


def test_report_without_feedback_run_explains_what_to_do(tmp_path, monkeypatch):
    root = _lab_root(tmp_path, monkeypatch)
    (root / ".lab-state" / "runs.json").write_text(
        json.dumps([{"run_id": "GOLD", "file": "results/run-GOLD.jsonl", "source": "golden"}]), encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        report.main([])
    assert "source=feedback" in str(e.value)


# ------------------------------------------------------------------------------------- sample page


def test_sample_compare_page_is_well_formed():
    p = ROOT / "docs" / "compare" / "sample-compare.html"
    assert p.exists(), "regenerate: python -m labkit.compare --before tests/fixtures/eval-sample-v0.json " \
                       "--after tests/fixtures/eval-sample-v1.json --out docs/compare/sample-compare.html"
    page = p.read_text(encoding="utf-8")
    assert V0_ID in page and V1_ID in page and "数据为合成数据" in page
    well_formed(page)
