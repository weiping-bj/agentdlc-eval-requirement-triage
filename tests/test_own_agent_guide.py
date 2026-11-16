"""The own-agent guide (docs/own-agent-guide.html) and the on-site A path stay consistent with the code.

- A3.4's code excerpts are real lines of the files they cite.
- The run record the guide tells people to write by hand has every field our scripts read.
- The guide's Logs Insights query uses the span name / attributes the snippet writes.
- The A path never recommends custom service names (online eval would not score them).
- The route-switch buttons are gone, and no rehearsal / "own agent on site" wording is left.
"""
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
GUIDE = DOCS / "own-agent-guide.html"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _section(page: str, sid: str, next_sid: str) -> str:
    return page.split(f'id="{sid}"', 1)[1].split(f'id="{next_sid}"', 1)[0]


def test_a34_code_excerpts_are_real_source_lines():
    a34 = _section(_read(DOCS / "a3-instrument.html"), "s-a3-4", "s-a3-5")
    blocks = re.findall(r'<pre class="out" data-src="([^"]+)">(.*?)</pre>', a34, re.S)
    assert {src for src, _ in blocks} == {"10-A-run-local.sh", "labkit/batch.py", "agent/s12agent/core.py"}
    for src, body in blocks:
        source = [ln.strip() for ln in _read(ROOT / src).splitlines()]
        # each run of lines between "…" markers must appear contiguously, and the runs in order
        pos = 0
        for chunk in re.split(r"\n\s*…\s*\n", html.unescape(body).strip("\n")):
            want = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
            hit = next((i for i in range(pos, len(source) - len(want) + 1) if source[i:i + len(want)] == want), None)
            assert hit is not None, f"{src}: excerpt not found in order: {want}"
            pos = hit + len(want)


def _pre_blocks(sec: str) -> list:
    return [html.unescape(b) for b in re.findall(r'<pre class="out">(.*?)</pre>', sec, re.S)]


def test_manual_run_record_has_every_field_the_scripts_read():
    g9 = _section(_read(GUIDE), "s-g-9", "s-g-10")
    blocks = _pre_blocks(g9)
    entry = json.loads(next(b for b in blocks if '"file":' in b).replace("<后缀>", "x"))
    jsonl_first = next(b for b in blocks if '"session_id"' in b).splitlines()[0]
    line_keys = set(re.findall(r'"(\w+)":', jsonl_first.split('"result"', 1)[0])) | {"result"}
    for mod in ("evaluate", "check_telemetry"):
        src = _read(ROOT / "labkit" / f"{mod}.py")
        assert set(re.findall(r"run\[['\"](\w+)['\"]\]", src)) <= set(entry), mod
        assert set(re.findall(r"rec\[['\"](\w+)['\"]\]", src)) <= line_keys, mod
        assert "run.get(\"log_group\")" in src and "log_group" in entry
    datetime.strptime(entry["created"], "%Y%m%dT%H%M%SZ")   # the format evaluate.py / check_telemetry.py parse
    # the search windows the guide explains (10 min for 31, 60 min for 12)
    assert "往前 10 分钟" in g9 and "往前 60 分钟" in g9
    assert 'timestamp()) - 600' in _read(ROOT / "labkit" / "evaluate.py")
    assert '.timestamp()) - 3600' in _read(ROOT / "labkit" / "check_telemetry.py")
    assert entry["file"] == f"results/run-{entry['run_id']}.jsonl" and entry["mode"] == "own"
    assert entry["run_id"] in jsonl_first


def test_guide_query_uses_what_the_snippet_writes():
    g9 = _section(_read(GUIDE), "s-g-9", "s-g-10")
    query = html.unescape(re.search(r"<pre><code>(fields .*?)</code></pre>", g9, re.S).group(1))
    snippet = _read(ROOT / "telemetry" / "snippets" / "a34_session_wrapper.py")
    assert 'name = "s12lab.triage"' in query and '"s12lab.triage"' in snippet
    for attr in ("session.id", "s12lab.feedback_id", "s12lab.triage_output"):
        assert f"attributes.{attr}" in query and f'"{attr}"' in snippet
    assert "resource.attributes.service.name" in query and "-local-agent" in query


def test_no_page_recommends_custom_service_names():
    a3 = _read(DOCS / "a3-instrument.html")
    assert "otel_env.sh my-agent" not in a3
    guide = _read(GUIDE)
    g4 = _section(guide, "s-g-4", "s-g-5")
    assert "source ./00-config.sh &amp;&amp; source telemetry/otel_env.sh</code>" in g4
    warn = re.search(r'<div class="warn"><b>不要改服务名和日志组。</b>.*?</div>', g4, re.S).group(0)
    assert guide.count("otel_env.sh my-agent") == warn.count("otel_env.sh my-agent") == 1
    online = _read(ROOT / "labkit" / "online.py")
    assert 'c["LOCAL_LOG_GROUP"]' in online and 'c["LOCAL_SERVICE_NAME"]' in online
    assert '"LOCAL_SERVICE_NAME": f"{dash}-local-agent"' in _read(ROOT / "labkit" / "config.py")


def test_guide_is_linked_from_index_and_the_chapters():
    for name in ("index", "a3-instrument", "a4-logs", "a5-check", "06-evaluators", "07-evaluate",
                 "08-optimize", "09-compare", "appendix"):
        assert 'href="own-agent-guide.html' in _read(DOCS / f"{name}.html"), name
    assert 'file: "own-agent-guide.html"' in _read(DOCS / "assets" / "site.js")
    assert "docs/own-agent-guide.html" in _read(ROOT / "README.md")


def test_route_switch_buttons_are_gone():
    js, css = _read(DOCS / "assets" / "site.js"), _read(DOCS / "assets" / "style.css")
    for s in ("data-set-path", "setPath", "s12lab-path", "我的路线"):
        assert s not in js
    assert "body.path-a" not in css and "path-switch" not in css
    for p in DOCS.glob("*.html"):
        assert "我的路线" not in _read(p), p.name


def test_no_rehearsal_or_own_agent_on_site_wording():
    files = (sorted(DOCS.glob("*.html")) + [ROOT / "README.md", ROOT / "data" / "HANDOFF.md"]
             + sorted(ROOT.glob("*.sh")) + sorted((ROOT / "telemetry").glob("*.sh"))
             + sorted((ROOT / "labkit").glob("*.py")) + sorted((ROOT / "data" / "tools").glob("*.py")))
    banned = ("彩排", "扮演", "讲师笔记本", "自有 Agent", "接入自有", "lead 的", "lead 已", "the lead",
              "不要在现场硬改")
    bad = [(f.name, b) for f in files for b in banned if b in _read(f)]
    assert not bad, bad
    from labkit import compare
    assert all("自有" not in label for _, label in compare.PATHS.values())


def test_two_imported_evaluations_only_pair_by_falling_back(monkeypatch):
    """Guide G.14: imported runs are never picked as 'after'; with nothing else the default is the last two."""
    from labkit import compare
    evals = [{"run_id": "A-imported-1", "file": "results/eval-A-imported-1.json", "version": "imported", "mode": "imported"},
             {"run_id": "A-imported-2", "file": "results/eval-A-imported-2.json", "version": "imported", "mode": "imported"}]
    monkeypatch.setattr(compare, "load_state_list", lambda name: evals)
    before, after = compare.pick_default()
    assert before.name == "eval-A-imported-1.json" and after.name == "eval-A-imported-2.json"
    assert compare.path_of("own")[0] == "A" and compare.path_of("imported")[0] == "A"


def test_imported_run_window_covers_a_long_log_file(tmp_path, monkeypatch):
    """A 60-line send re-stamps its first session ~12 min before the send ends; the registered
    'created' must not be later than that, or 31-run-eval.sh (created - 10 min) misses it."""
    import time
    from labkit import aws, convert_logs
    state, results = tmp_path / ".lab-state", tmp_path / "results"
    cfg = {"LAB_NAME": "s12lab-zz", "RESULTS_DIR": str(results), "STATE_DIR": str(state), "LAB_ROOT": str(tmp_path),
           "CONVERTED_SERVICE_NAME": "s12lab-zz-imported-logs",
           "CONVERTED_LOG_GROUP": "/aws/bedrock-agentcore/s12lab-zz-imported-logs"}
    monkeypatch.setattr(aws, "cfg", lambda: cfg)
    old = int((time.time() - 3 * 86400) * 1000)  # both log groups created days ago: window not clamped
    logs = type("L", (), {"describe_log_groups": lambda self, logGroupNamePrefix: {"logGroups": [
        {"logGroupName": logGroupNamePrefix, "creationTime": old}]}})()
    monkeypatch.setattr(aws, "client", lambda name: logs)
    log = tmp_path / "logs.jsonl"
    log.write_text("".join(json.dumps({"session_id": f"s{i}", "timestamp": "2026-10-12T09:30:00+08:00",
                                       "user_input": f"需求编号: FB-{i}", "agent_output": "ok"}) + "\n"
                           for i in range(60)), encoding="utf-8")
    t0 = time.time()
    assert convert_logs.send(str(log)) == 0
    run = json.loads((state / "runs.json").read_text(encoding="utf-8"))[-1]
    created = datetime.strptime(run["created"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
    first_span = t0 - 120 - 10 * 60
    assert created - 600 <= first_span + 1          # evaluate.py's search window reaches the first session
    assert run["run_id"].startswith("A-imported-") and run["mode"] == run["version"] == "imported"


def test_31_without_run_takes_the_last_registered_run(monkeypatch):
    """Guide G.8: without --run, 31-run-eval.sh evaluates runs.json's last entry, e.g. a later mock run."""
    from labkit import aws, evaluate
    runs = [{"run_id": "A-own-v0-20261106T113000Z", "mode": "own"}, {"run_id": "A-v1-20261106T120000Z", "mode": "local"}]
    monkeypatch.setattr(aws, "load_state", lambda name, default=None: runs if name == "runs" else default)
    assert evaluate.pick_run(None)["run_id"] == "A-v1-20261106T120000Z"
    assert evaluate.pick_run("A-own-v0-20261106T113000Z")["mode"] == "own"
    g8 = _section(_read(GUIDE), "s-g-8", "s-g-9")
    assert "pick_run()" in g8 and "一律带 <code>--run &lt;run_id&gt;</code>" in g8
