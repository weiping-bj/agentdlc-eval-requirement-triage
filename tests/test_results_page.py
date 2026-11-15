"""labkit.results_page (45-results-page.sh), the tool-call extractor labkit.spans.tool_calls and its use in
labkit.evaluate. No AWS calls: pages are generated from tests/fixtures (all synthetic, see make_results_sample.py)
into tmp_path; tool calls are read from the real ADOT span sample telemetry/fixtures/local-adot-v0-sessions.json."""
import copy
import html
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from labkit import compare, results_page as rp, spans  # noqa: E402
from labkit import config as labcfg  # noqa: E402

FX = ROOT / "tests" / "fixtures"
V0, V1, V2 = FX / "eval-sample-v0.json", FX / "eval-sample-v1.json", FX / "eval-sample-v2-synthetic.json"
ADOT = ROOT / "telemetry" / "fixtures" / "local-adot-v0-sessions.json"
INJ = ['<script>alert("x")</script>', '"><img src=x onerror=alert(1)>', "</script><script>alert(2)</script>",
       "javascript:alert(3)", "<!--", "]]><svg onload=alert(4)>"]
ALLOWED_TAGS = {"html", "head", "meta", "title", "style", "body", "div", "span", "b", "main", "footer", "script", "h2",
                "h3", "h4", "p", "table", "thead", "tbody", "tr", "th", "td", "a", "button", "select", "option", "details",
                "summary", "section", "aside", "pre", "ul", "li", "svg", "line", "polyline", "circle", "g", "br"}
VOID = {"meta", "br"}


class Audit(HTMLParser):
    """Every tag, every attribute, script bodies; nesting balance."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags, self.attrs, self.scripts, self.stack, self.errors = set(), [], [], [], []
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        self.attrs += [(tag, k, v) for k, v in attrs]
        if tag == "script":
            self._in_script = True
            self.scripts.append("")
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.tags.add(tag)
        self.attrs += [(tag, k, v) for k, v in attrs]

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False
        if tag in VOID:
            return
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.errors.append(f"</{tag}> with open {self.stack[-3:]}")

    def handle_data(self, data):
        if self._in_script:
            self.scripts[-1] += data


def audit(page: str) -> Audit:
    a = Audit()
    a.feed(page)
    a.close()
    assert not a.errors, a.errors[:5]
    assert a.stack == [], a.stack[-5:]
    return a


def assert_self_contained_and_inert(page: str) -> Audit:
    a = audit(page)
    assert a.tags <= ALLOWED_TAGS, a.tags - ALLOWED_TAGS
    assert len(a.scripts) == 1 and a.scripts[0].strip() == (compare.FILTER_JS + "\n" + rp.JS).strip()
    for tag, k, v in a.attrs:
        assert not k.startswith("on"), (tag, k, v)                      # no inline event handlers
        assert "://" not in (v or "") or (k == "xmlns" and v == "http://www.w3.org/2000/svg"), (tag, k, v)
        assert k not in ("src", "srcdoc", "action", "formaction", "style") or (k == "style" and "url(" not in (v or "")), (tag, k)
        if k == "href":
            assert (v or "").startswith("#"), v                         # only in-page anchors
        if k == "xmlns":
            assert v == "http://www.w3.org/2000/svg"
    css = "".join(re.findall(r"<style>(.*?)</style>", page, re.S))
    for code in (css, a.scripts[0]):                                    # nothing the browser would fetch
        assert not re.search(r"https?://|//cdn|@import|@font-face|url\(", code, re.I)
    assert not re.search(r"<(link|iframe|img|object|embed|base|form|input)\b", page, re.I)
    return a


@pytest.fixture(scope="module")
def page3(tmp_path_factory):
    out = tmp_path_factory.mktemp("rp") / "p.html"
    assert rp.main([str(V0), str(V1), str(V2), "--out", str(out)]) == 0
    return out.read_text(encoding="utf-8")


def _row(page: str, fid: str) -> str:
    m = re.search(r'<tr class="rp-row" data-item="(\d+)" data-tags="([^"]*)" tabindex="0"><td><span class="mono">'
                  + re.escape(fid) + "<", page)
    assert m, fid
    return m.group(2)


# ------------------------------------------------------------------------------------------ page structure


def test_page_is_self_contained_with_one_inert_script(page3):
    a = assert_self_contained_and_inert(page3)
    for i in ("overview", "trend", "list", "items", "rp-drawer", "rp-backdrop", "rp-target", "rp-base"):
        assert f'id="{i}"' in page3, i
    assert len(page3.encode()) < 8 * 1024 * 1024
    assert {"details", "section", "aside", "select", "svg"} <= a.tags


def test_page_embeds_every_item_with_requirement_golden_outputs_tools_and_explanations(page3):
    v2 = json.loads(V2.read_text(encoding="utf-8"))
    n = len(v2["sessions"])
    assert page3.count('<tr class="rp-row"') == n and page3.count('<section class="rp-item"') == n
    for k in range(n):
        assert f'id="item-{k}"' in page3
    s = next(x for x in v2["sessions"] if x["agent_output"].get("customer_reply"))
    assert html.escape(s["agent_output"]["customer_reply"], quote=True) in page3          # agent output
    assert html.escape(s["scores"]["reject_quality"]["explanation"], quote=True) in page3  # evaluator explanation
    call = v2["sessions"][0]["tool_calls"][0]
    assert call["name"] == "search_capabilities" and html.escape(call["output"], quote=True) in page3  # tool call
    raw = next(json.loads(x)["raw"] for x in (FX / "run-runtime-v2-20261105T170000Z.jsonl").read_text("utf-8").splitlines())
    assert html.escape(raw, quote=True) in page3                                       # raw output joined from the run file
    assert "我们在客户列表页想批量修改自定义字段" in page3                                     # requirement text (golden input)
    assert "能力目录 CAP-002 批量编辑仅支持标准字段" in page3                                 # golden rationale
    assert "这份评估结果没有记录工具调用" in page3                                             # V0 / V1 evaluations have none
    assert "这一条没有调用工具" in page3                                                    # V2 FB-2610-014: empty list


def test_overview_and_trend_use_the_three_pillar_verdict(page3):
    fb = compare.feedback_index()
    evs = [compare.EvalRun.load(p) for p in (V0, V1, V2)]
    specs = compare.label_specs(*evs)
    ps = [compare.pillars_of(ev, fb, 0.70, specs) for ev in evs]
    assert [p["verdict"] for p in ps] == ["fail", "pass", "pass"]
    trend = page3[page3.index('id="trend"'):page3.index('id="list"')]
    for key in ("important", "urgent", "decision", "reject_quality"):
        for p in ps:
            assert compare.pct(compare._gate(p, key)["actual"]) in trend, key
    assert "V0 → V1 参考版 → V2" in page3 and "stroke-dasharray" in trend and "<title>V2：" in trend
    assert page3.count('<polyline') >= 6  # one line per metric row


def test_row_tags_drive_the_filters(page3):
    t1, t5 = _row(page3, "FB-2610-001").split(), _row(page3, "FB-2610-005").split()
    assert {"v1:ok", "v2:bad", "v2:f-urgent"} <= set(t1)          # regressed in the synthetic V2
    assert {"v1:bad", "v2:ok"} <= set(t5)                         # fixed in the synthetic V2
    assert any(t.startswith("dim-") for t in t1)
    v0 = json.loads(V0.read_text(encoding="utf-8"))
    crit = [s["feedback_id"] for s in v0["sessions"] if (s["scores"].get("label_accuracy") or {}).get("label") == "CriticalFail"]
    assert crit and all("v0:red" in _row(page3, f).split() for f in crit)
    for tok in re.findall(r'data-tags="([^"]*)"', page3):
        assert re.fullmatch(r"[a-z0-9:\- ]*", tok), tok           # only generated tokens reach the script


def test_single_version_page_has_no_comparison_controls(tmp_path):
    out = tmp_path / "one.html"
    assert rp.main([str(V1), "--out", str(out)]) == 0
    page = out.read_text(encoding="utf-8")
    assert_self_contained_and_inert(page)
    assert "现在只有一个版本" in page and 'data-v="fixed"' not in page and 'data-v="broke"' not in page
    assert '<option value="-1">（不对照）</option></select>' in page


# ------------------------------------------------------------------------------------------ untrusted text


def _poison(tmp_path: Path) -> list:
    v2 = json.loads(V2.read_text(encoding="utf-8"))
    bad = " ".join(INJ)
    s = v2["sessions"][0]
    s["feedback_id"] = "FB-<b>x</b>" + INJ[1]
    s["agent_output"].update(decision="拒绝", customer_reply=bad, reasoning=bad, reject_reason=bad, level=bad, extra=bad)
    s["agent_output"]["_warnings"] = [bad]
    s["agent_error"] = None
    s["scores"]["label_urgent"]["explanation"] = bad
    s["scores"]["label_urgent"]["label"] = INJ[0]
    s["scores"][INJ[1]] = {"value": 1, "label": "Pass", "explanation": bad}
    s["scores"]["reject_quality"] = {"value": 0.1, "label": "Fail", "explanation": "拒绝话术评审 0.1：x。红线：" + INJ[2] + "。"}
    s["tool_calls"] = [{"name": INJ[0], "input": bad, "output": bad, "status": INJ[1], "duration_ms": "1e9", "call_id": bad}]
    s["session_id"] = bad
    s["trace_id"] = bad
    s["error"] = None
    v2["sessions"][1]["error"] = bad
    v2["run"].update(model=bad, version='v9"><script>alert(5)</script>', run_id="r-" + INJ[0], file=None, mode=bad)
    v2["evaluators"] = {INJ[0]: bad}
    v2["region"] = bad
    p = tmp_path / "eval-poison.json"
    p.write_text(json.dumps(v2, ensure_ascii=False), encoding="utf-8")
    run = tmp_path / ("run-r-" + INJ[0] + ".jsonl")
    try:
        run.write_text(json.dumps({"session_id": bad, "feedback_id": s["feedback_id"], "raw": bad}, ensure_ascii=False) + "\n",
                       encoding="utf-8")
    except OSError:
        pass
    return [p]


def test_untrusted_text_cannot_inject_markup_or_script(tmp_path, monkeypatch):
    bad = " ".join(INJ)
    real_fb = compare.feedback_index()
    fb = copy.deepcopy(real_fb)
    fb["FB-2610-002"].update(title=bad, dimensions=[bad, "认知"], golden_id=INJ[0])
    real_req = rp.requirement_index()
    req = copy.deepcopy(real_req)
    g = req["FB-2610-002"]["golden"]
    g.update(rationale=bad, reject_points=[bad], reply_points=[bad], acceptable_decisions=[bad, "拒绝"], golden_id=INJ[0],
             matched_capabilities=[bad], case_type=bad)
    req["FB-2610-002"]["item"].update(content=bad, title=bad, customer=bad)
    monkeypatch.setattr(compare, "feedback_index", lambda *a, **k: fb)
    monkeypatch.setattr(rp, "requirement_index", lambda: req)
    paths = _poison(tmp_path)
    out = tmp_path / "poison.html"
    assert rp.main([str(V0), *map(str, paths), "--out", str(out)]) == 0
    page = out.read_text(encoding="utf-8")
    assert_self_contained_and_inert(page)
    for s in INJ:
        if s not in ("javascript:alert(3)",):
            assert s not in page, s
    assert html.escape(INJ[0], quote=True) in page and html.escape(INJ[1], quote=True) in page
    assert "FB-&lt;b&gt;x&lt;/b&gt;" in page
    assert "<b>x</b>" not in page


def test_script_only_toggles_visibility():
    js = rp.JS
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "fetch(",
                "XMLHttpRequest", "location.href", "location =", "setTimeout(", "setInterval(", "createElement", "textContent ="):
        if bad == "textContent =":
            assert js.count("textContent =") == 1 and "'显示 ' + shown" in js  # the only text it writes: a count
            continue
        assert bad not in js, bad
    assert "</" not in js


# ------------------------------------------------------------------------------------------ defaults / CLI


def _lab(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(labcfg, "ROOT", tmp_path)
    (tmp_path / "results").mkdir()
    (tmp_path / ".lab-state").mkdir()
    return tmp_path


def _copy_eval(root: Path, src: Path, run_id: str, version: str, mode: str = "runtime") -> dict:
    d = json.loads(src.read_text(encoding="utf-8"))
    d["run"].update(run_id=run_id, version=version, mode=mode)
    p = root / "results" / f"eval-{run_id}.json"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return {"run_id": run_id, "file": f"results/eval-{run_id}.json", "version": version, "mode": mode}


def test_default_is_the_latest_evaluation_of_each_version_in_version_order(tmp_path, monkeypatch, capsys):
    root = _lab(tmp_path, monkeypatch)
    evals = [_copy_eval(root, V0, "r-v0-old", "v0"), _copy_eval(root, V1, "r-v1ref", "v1-ref"),
             _copy_eval(root, V1, "r-v1", "v1"), _copy_eval(root, V0, "r-v0-new", "v0"),
             _copy_eval(root, V1, "r-imported", "v1", mode="imported"),
             {"run_id": "gone", "file": "results/eval-gone.json", "version": "v2", "mode": "runtime"}]
    (root / ".lab-state" / "evals.json").write_text(json.dumps(evals), encoding="utf-8")
    assert [p.name for p in rp.pick_default()] == ["eval-r-v0-new.json", "eval-r-v1.json", "eval-r-v1ref.json"]
    assert "找不到 results/eval-gone.json" in capsys.readouterr().err
    assert rp.main([]) == 0
    out = capsys.readouterr().out.strip().splitlines()[-1]
    assert out == "results/results-page-r-v0-new-to-r-v1ref.html" and (root / out).is_file()


def test_without_evaluations_it_says_what_to_run(tmp_path, monkeypatch):
    _lab(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as e:
        rp.main([])
    assert "./43-run-eval.sh" in str(e.value)
    with pytest.raises(SystemExit) as e:
        rp.main(["no-such-run"])
    assert "找不到文件或 run_id" in str(e.value)


def test_too_many_versions_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as e:
        rp.main([str(V0)] * (rp.MAX_VERSIONS + 1))
    assert e.value.code == 2 and "最多" in capsys.readouterr().err


def test_same_version_twice_gets_distinct_labels(tmp_path):
    out = tmp_path / "x.html"
    assert rp.main([str(V1), str(V1), "--out", str(out)]) == 0
    assert "V1 参考版（1）" in out.read_text(encoding="utf-8") and "V1 参考版（2）" in out.read_text(encoding="utf-8")


def test_wrapper_script_publishes_like_the_compare_page():
    sh = (ROOT / "45-results-page.sh").read_text(encoding="utf-8")
    assert '-m labkit.results_page "$@" | tail -1' in sh and '-m labkit.publish "$OUT"' in sh
    assert "45-results-page.sh" in (ROOT / "labkit" / "publish.py").read_text(encoding="utf-8")


# ------------------------------------------------------------------------------------------ tool calls


def test_tool_calls_from_the_real_adot_span_sample():
    sessions = json.loads(ADOT.read_text(encoding="utf-8"))
    got = {s["feedback_id"]: spans.tool_calls(s["docs"], trace_id=spans.trace_ids(s["docs"])[-1]) for s in sessions}
    assert {k: len(v) for k, v in got.items()} == {"FB-2610-001": 3, "FB-2610-002": 7, "FB-2610-003": 2}
    first = got["FB-2610-001"][0]
    assert first["name"] == "lookup_capability" and first["status"] == "success"
    assert json.loads(first["input"]) == {"keyword": "批量编辑"}
    assert json.loads(first["output"]) == ["列表页批量编辑", "离职交接与负责人批量转移", "审批流"]
    assert first["call_id"] == "tooluse_HVaAIWL4L02UmA7V1p1nnO" and first["duration_ms"] == 6.5
    assert [c["input"] for c in got["FB-2610-001"]] == ['{"keyword": "批量编辑"}', '{"keyword": "自定义字段"}',
                                                        '{"keyword": "批量导入更新"}']   # call order
    assert spans.tool_calls(sessions[0]["docs"], trace_id="another-trace") == []


def test_tool_calls_fall_back_to_tooluse_blocks_and_tolerate_odd_docs():
    docs = [
        {"name": "execute_tool search", "traceId": "t", "spanId": "s1", "startTimeUnixNano": 2, "endTimeUnixNano": 2000002,
         "attributes": {"gen_ai.tool.call.id": "c1"}, "status": {"code": "ERROR"}},
        {"traceId": "t", "spanId": "chat", "body": {"output": {"messages": [{"content": {"message": json.dumps(
            [{"toolUse": {"toolUseId": "c1", "name": "search", "input": {"q": "x" * 5000}}},
             {"toolResult": {"toolUseId": "c1", "status": "error", "content": [{"text": "boom"}]}}])}}]}}},
        {"name": "execute_tool", "attributes": None, "status": "weird", "durationNano": "n/a"},
        "not a dict", {"body": "str body"}, {},
    ]
    calls = spans.tool_calls(docs)
    assert calls[0]["name"] == "search" and calls[0]["status"] == "error" and calls[0]["output"] == "boom"  # toolResult status
    assert len(calls[0]["input"]) == spans.TOOL_INPUT_MAX and calls[0]["input"].endswith("…")
    assert calls[0]["duration_ms"] == 2.0
    assert calls[1] == {"name": "tool", "input": "", "output": "", "status": None, "duration_ms": None, "call_id": None}


class _FakeEval:
    def __init__(self):
        self.calls = []

    def evaluate(self, **kw):
        self.calls.append(kw)
        return {"evaluationResults": [{"value": 1.0, "label": "Pass", "explanation": "ok"}]}


def test_evaluate_records_the_tool_calls_of_the_evaluated_trace(monkeypatch):
    from labkit import aws, evaluate
    s = json.loads(ADOT.read_text(encoding="utf-8"))[0]
    fake = _FakeEval()
    monkeypatch.setattr(spans, "session_docs", lambda *a, **k: s["docs"])
    monkeypatch.setattr(aws, "client", lambda name: fake)
    rec = {"feedback_id": s["feedback_id"], "session_id": s["session_id"], "result": {"decision": "迭代"}}
    out = evaluate.evaluate_session(rec, {}, [("label_accuracy", "id-1")], "lg", 0)
    assert [c["name"] for c in out["tool_calls"]] == ["lookup_capability"] * 3
    assert out["scores"]["label_accuracy"]["label"] == "Pass" and len(fake.calls) == 1

    def boom(*a, **k):
        raise RuntimeError("bad span")

    monkeypatch.setattr(spans, "tool_calls", boom)
    out = evaluate.evaluate_session(rec, {}, [("label_accuracy", "id-1")], "lg", 0)
    assert "tool_calls" not in out and out["scores"]["label_accuracy"]["label"] == "Pass" and "error" not in out


def test_synthetic_fixture_is_marked_and_current():
    d = json.loads(V2.read_text(encoding="utf-8"))
    assert d["_synthetic"].startswith("合成样例（synthetic）") and d["run"]["version"] == "v2"
    assert "make_results_sample.py" in d["_synthetic"]
    assert all(isinstance(s.get("tool_calls"), list) for s in d["sessions"])


# ------------------------------------------------------------------------------------------ handbook


def _ws(rel: str) -> str:
    return (ROOT / "workshop-studio" / rel).read_text(encoding="utf-8")


def _section(page: str, anchor: str) -> str:
    start = page.index("{#" + anchor + "}")
    nxt = page.find("\n## ", start)
    return page[start:nxt if nxt != -1 else len(page)]


def test_handbook_runs_the_results_page_after_the_eval_and_again_after_v1():
    ch4, ch5 = _ws("content/04-evaluate/index.zh.md"), _ws("content/05-optimize/index.zh.md")
    s45, s47 = _section(ch4, "s-4-5"), _section(ch4, "s-4-7")
    assert "```bash\n./45-results-page.sh\n```" in s45 and "| 4.5 | 按需评估，生成结果页 | 6 分钟（含等待） |" in ch4
    # the expected output is what labkit.results_page / labkit.publish print, for the recorded V0 run of 4.4
    run_id = "runtime-v0-20261114T134711Z"
    assert f"▶ 结果页：V0 {run_id}" in s45 and "✅ 结果页已生成（本地文件，可直接用浏览器打开）：" in s45
    ev = compare.EvalRun({"run": {"run_id": run_id, "version": "v0"}}, Path(f"eval-{run_id}.json"))
    assert f"results/{rp.out_name([ev])}" in s45 and f"reports/{rp.out_name([ev])}" in s45
    src = (ROOT / "labkit" / "results_page.py").read_text(encoding="utf-8")
    assert '"▶ 结果页："' in src and "✅ 结果页已生成（本地文件，可直接用浏览器打开）：" in src
    assert "./46-try-one.sh FB-2610-009" in s47 and "04-results-page-drawer.png" in s47 and "FB-2610-009" in s47
    assert "```bash\n./45-results-page.sh\n```" in _section(ch5, "s-5-5")
    assert "./46-try-one.sh FB-2610-004" in _section(ch5, "s-5-4")


def test_new_screenshot_is_planned_and_scripts_are_listed():
    shot = _ws("SHOTLIST.md")
    assert re.search(r"^\| `04-results-page-drawer\.png` \| 4 · 4\.7 \| \*\*未拍\*\* \|", shot, re.M)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "`45-results-page.sh`" in readme and "`46-try-one.sh`" in readme
    home = _ws("content/index.zh.md")
    assert "| **合计** | | | **160 分钟** |" in home


# ------------------------------------------------------------------------------------------ review regressions


def _graded_copy(tmp_path: Path, src: Path, run_id: str) -> Path:
    """The evaluation re-scored in the graded scheme: a label_level result on every session."""
    d = json.loads(src.read_text(encoding="utf-8"))
    d["run"].update(run_id=run_id)
    for s in d["sessions"]:
        if s.get("scores"):
            s["scores"]["label_level"] = {"value": 1.0, "label": "Pass", "explanation": "优先级 对"}
    p = tmp_path / f"eval-{run_id}.json"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return p


def test_each_version_is_scored_under_its_own_label_scheme(tmp_path):
    """A graded V0 next to binary V1 / V2 (the e5 label-scheme extension): the binary ones keep the verdict
    ./43-run-eval.sh printed for them, with no 优先级 gate and no '? 无评分' cells."""
    g0 = _graded_copy(tmp_path, V0, "runtime-v0-graded")
    out = tmp_path / "mix.html"
    assert rp.main([str(g0), str(V1), str(V2), "--out", str(out)]) == 0
    page = out.read_text(encoding="utf-8")
    fb = compare.feedback_index()
    for path in (V1, V2):
        ev = compare.EvalRun.load(path)
        assert compare.pillars_of(ev, fb, 0.70)["verdict"] == "pass"   # what 43-run-eval.sh says (own specs)
    ov = page[page.index('id="overview"'):page.index('id="trend"')]
    assert ov.count("✅ 通过") == 2 and "优先级准确率" in ov and ov.count("优先级准确率") == 1
    assert "标签口径不同" in page
    v1_cells = re.findall(r'<td data-col="1">(.*?)</td>', page[page.index('id="items"'):])
    assert v1_cells and not any("无评分" in c for c in v1_cells)
    assert 'data-v="level"' in page                                     # the 优先级 field filter exists (graded V0)


def test_repeated_feedback_ids_stay_separate_rows(tmp_path):
    d = json.loads(V1.read_text(encoding="utf-8"))
    d["sessions"].append(copy.deepcopy(d["sessions"][0]))
    d["sessions"][-1]["agent_output"]["reasoning"] = "第二次运行的理由"
    p = tmp_path / "eval-dup.json"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "dup.html"
    assert rp.main([str(p), "--out", str(out)]) == 0
    page = out.read_text(encoding="utf-8")
    assert page.count('<tr class="rp-row"') == len(d["sessions"]) == page.count('<section class="rp-item"')
    fid = d["sessions"][0]["feedback_id"]
    assert f"{fid}（第 2 条）" in page and "第二次运行的理由" in page


def test_run_file_is_read_only_inside_results_or_next_to_the_eval(tmp_path, monkeypatch):
    (tmp_path / "lab").mkdir()
    root = _lab(tmp_path / "lab", monkeypatch)
    secret = tmp_path / "elsewhere" / "notes.jsonl"
    secret.parent.mkdir()
    v2 = json.loads(V2.read_text(encoding="utf-8"))
    s0 = v2["sessions"][0]
    secret.write_text(json.dumps({"session_id": s0["session_id"], "raw": "SECRET-OUTSIDE"}) + "\n", encoding="utf-8")
    for f in (str(secret), "../" * 12 + str(secret).lstrip("/"), "results/../../elsewhere/notes.jsonl"):
        v2["run"]["file"] = f
        p = root / "results" / "eval-x.json"
        p.write_text(json.dumps(v2, ensure_ascii=False), encoding="utf-8")
        ev = compare.EvalRun.load(p)
        assert rp.run_records(ev) == ({"sid": {}, "fid": {}}, None), f
    # inside results/: read, and matched by session id only (another session's raw is never shown)
    run = root / "results" / "run-ok.jsonl"
    run.write_text(json.dumps({"session_id": "other-session", "feedback_id": s0["feedback_id"], "raw": "WRONG-SESSION"})
                   + "\n", encoding="utf-8")
    v2["run"]["file"] = "results/run-ok.jsonl"
    p.write_text(json.dumps(v2, ensure_ascii=False), encoding="utf-8")
    ver = rp.Version(compare.EvalRun.load(p), 0, "V2", compare.feedback_index(), 0.7)
    assert ver.run_file == "results/run-ok.jsonl" and ver.run_record(s0["feedback_id"]) is None


def test_requirement_text_follows_adlc_data_dir_like_the_titles(tmp_path, monkeypatch):
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "feedback_last_month.jsonl").write_text(json.dumps(
        {"id": "FB-2610-006", "title": "场景包里的标题", "content": "场景包里的需求原文"}, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setenv("ADLC_DATA_DIR", str(pack))
    req = rp.requirement_index()
    assert req["FB-2610-006"]["item"]["content"] == "场景包里的需求原文"
    assert compare.feedback_index()["FB-2610-006"]["title"] == "场景包里的标题"
    assert req["FB-2610-001"]["golden"]["golden_id"] == "G01"


def test_default_falls_back_to_an_older_evaluation_whose_file_still_exists(tmp_path, monkeypatch, capsys):
    root = _lab(tmp_path, monkeypatch)
    evals = [_copy_eval(root, V0, "r-v0-old", "v0"), _copy_eval(root, V1, "r-v1", "v1"),
             {"run_id": "r-v0-gone", "file": "results/eval-r-v0-gone.json", "version": "v0", "mode": "runtime"}]
    (root / ".lab-state" / "evals.json").write_text(json.dumps(evals), encoding="utf-8")
    assert [p.name for p in rp.pick_default()] == ["eval-r-v0-old.json", "eval-r-v1.json"]
    assert "r-v0-gone" in capsys.readouterr().err


def test_reject_tag_follows_the_gate_and_field_filter_reads_the_wrong_side(page3):
    v0 = json.loads(V0.read_text(encoding="utf-8"))
    fb = compare.feedback_index()
    ver = rp.Version(compare.EvalRun.load(V0), 0, "V0", fb, 0.7)
    for i, s in enumerate(v0["sessions"]):
        it = ver.items[rp.fid_of(s, i)]
        rej = it.get("reject") or {}
        gate_fail = rej.get("status") == "judged" and not (rej.get("passed") and not compare.pillars.red_line_kinds(it))
        assert ver.state(rp.fid_of(s, i))["rej_fail"] == gate_fail, s["feedback_id"]
    assert "var fv = st === 'fixed' ? b : t;" in rp.JS and "'v' + fv + ':f-' + cur.field" in rp.JS


def test_tool_calls_survive_unhashable_span_ids_and_odd_messages():
    docs = [{"name": "execute_tool x", "spanId": {"a": 1}, "status": {"code": "UNSET"}},
            {"spanId": {"a": 1}, "body": {"input": {"messages": True}, "output": {"messages": "x"}}},
            {"spanId": ["l"], "body": {"output": {"messages": [{"content": {"message": json.dumps(
                [{"toolResult": {"toolUseId": "c", "status": "success", "content": [{"text": "ok"}]}}])}}]}}},
            {"name": "execute_tool y", "attributes": {"gen_ai.tool.call.id": "c"}, "status": {"code": "UNSET"},
             "startTimeUnixNano": True, "durationNano": True}]
    calls = spans.tool_calls(docs)
    assert [c["status"] for c in calls] == [None, "success"]       # UNSET is no status; the toolResult's wins
    assert calls[1]["output"] == "ok" and calls[1]["duration_ms"] is None


def test_shared_helpers_do_not_overflow_on_bogus_values():
    assert compare.fmt_time("0001-01-01T00:00:00+01:00") == "0001-01-01T00:00:00+01:00"
    assert compare.case_of({"scores": {"label_accuracy": {"value": 1e308, "label": "Fail"}}})["n"] == 3
