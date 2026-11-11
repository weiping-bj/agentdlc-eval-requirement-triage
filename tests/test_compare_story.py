"""The four story cards of the chapter 9 compare page (labkit/compare_story.py), the change notes they show,
and the chapter text that asks for / describes them. Standard library + pytest only; no AWS calls."""
import difflib
import html
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from labkit import compare, compare_story, failures  # noqa: E402
from labkit import config as labcfg  # noqa: E402
from test_compare_report import INJ, INJ_ESC, V0, V1, well_formed  # noqa: E402

PROMPTS = ROOT / "agent" / "s12agent" / "prompts"
FIELDS = ("important", "urgent", "decision")


def _page(tmp_path, before=V0, after=V1) -> str:
    out = tmp_path / "c.html"
    assert compare.main(["--before", str(before), "--after", str(after), "--out", str(out)]) == 0
    return out.read_text(encoding="utf-8")


def _card(page: str, cid: str) -> str:
    i = page.index(f'id="{cid}"')
    j = page.find('<div class="card', i)
    return page[i:j if j > 0 else len(page)]


def _plain(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment)))


def _golden() -> dict:
    return failures.load_golden(ROOT / "data" / "golden" / "golden_set.jsonl")


def _right_after_veto(session: dict, golden_row: dict) -> bool:
    """Independent re-implementation of the gate rule for one session: every single-label evaluator is 1, the
    agent produced output, no misreject (CriticalFail, or 拒绝 on a can_auto_reject=false item) and no reply red flag."""
    sc = session.get("scores") or {}
    if session.get("agent_error"):
        return False
    labels = [str((sc.get(n) or {}).get("label") or "") for n in
              ("label_accuracy", "label_important", "label_urgent", "label_decision")]
    out = session.get("agent_output") or {}
    if "CriticalFail" in labels or (out.get("decision") == "拒绝" and golden_row.get("can_auto_reject") is False):
        return False
    if "红线：" in str((sc.get("reject_quality") or {}).get("explanation") or ""):
        return False
    return all((sc.get(f"label_{k}") or {}).get("value") == 1 for k in FIELDS)


def test_page_has_the_four_cards_in_story_order(tmp_path):
    page = _page(tmp_path)
    pos = [page.index(f'id="{c}"') for c in ("story-evaluators", "story-problems", "story-changes", "story-flips",
                                               "pillars", "cases")]
    assert pos == sorted(pos)
    nav = page[page.index('class="story-nav"'):]
    for anchor in ("#story-evaluators", "#story-problems", "#story-changes", "#story-flips", "#pillars", "#cases"):
        assert f'href="{anchor}"' in nav[:2000]
    assert well_formed(page).scripts == 1


def test_evaluator_card_names_every_evaluator_and_its_pillar(tmp_path):
    card = _card(_page(tmp_path), "story-evaluators")
    rows = {m.group(1): _plain(m.group(2)) for m in
            re.finditer(r'<tr><td><b class="mono">([^<]+)</b></td>(.*?)</tr>', card)}
    names = {k for p in (V0, V1) for s in json.loads(p.read_text(encoding="utf-8"))["sessions"] for k in s["scores"]}
    assert set(rows) == names
    assert "红线" in rows["label_accuracy"] and "门禁" not in rows["label_accuracy"].split("红线")[0]
    for n in ("label_important", "label_urgent", "label_decision"):
        assert "门禁" in rows[n] and "≥ 70%" in rows[n]
    assert all(p in rows["reject_quality"] for p in ("红线", "门禁", "观测"))
    for n in ("reject_quality_llm", "Builtin.Correctness", "Builtin.Helpfulness"):
        assert "观测" in rows[n] and "门禁" not in rows[n] and "不进结论" in rows[n]


def test_problem_card_is_the_7_5_analysis_of_the_before_run(tmp_path):
    card = _plain(_card(_page(tmp_path), "story-problems"))
    res = failures.analyze(json.loads(V0.read_text(encoding="utf-8")), _golden())
    assert res["wrong"] and any(res["dims"].values())
    assert f"判错 {len(res['wrong'])} 条" in card and f"其他未通过 {len(res['other'])} 条" in card
    for short, n in res["fields"].items():
        assert f"{compare_story.FIELD_FULL[short]} {n}" in card
    for d, n in res["dims"].items():
        symptom, where = failures.DIM_HINTS[d]
        assert f"{d} {n} {symptom} {where}" in card
    for w in res["wrong"]:
        assert w["fid"] in card


def test_dim_hints_are_the_chapter_7_5_table():
    page = (ROOT / "docs" / "07-evaluate.html").read_text(encoding="utf-8")
    rows = re.findall(r'<tr><td><span class="pill \w">(\S+?)</span></td><td>(.*?)</td><td>(.*?)</td></tr>', page)
    assert {d: (s, w) for d, s, w in rows} == failures.DIM_HINTS


def test_change_card_diffs_prompt_tools_and_model(tmp_path):
    card = _card(_page(tmp_path), "story-changes")
    lb = (PROMPTS / "v0.md").read_text(encoding="utf-8").splitlines()
    la = (PROMPTS / "v1.md").read_text(encoding="utf-8").splitlines()
    diff = list(difflib.unified_diff(lb, la, lineterm=""))
    plus = sum(1 for x in diff if x.startswith("+") and not x.startswith("+++"))
    minus = sum(1 for x in diff if x.startswith("-") and not x.startswith("---"))
    text = _plain(card)
    assert f"prompts/v0.md（{len(lb)} 行）→ prompts/v1.md（{len(la)} 行）：新增 {plus} 行、删去 {minus} 行" in text
    assert "lookup_capability" in text and "search_capabilities" in text and "find_similar_requests" in text
    assert "去掉： lookup_capability" in text
    assert "未变： us.anthropic.claude-sonnet-5-5" in text
    assert "能力目录" in text and "无法自动比较" in text
    # the shipped V1 change notes, rendered as a table
    assert "agent/s12agent/prompts/v1.changes.md" in text
    assert "<th>失败原因（评估发现）</th><th>修改（文件）</th><th>预期影响的指标</th>" in card
    assert "修 V1 红线（第一步）" in text and "评审加入菜单路径（第二步）" in text


def test_v1_change_notes_carry_the_two_follow_up_changes():
    notes = (PROMPTS / "v1.changes.md").read_text(encoding="utf-8")
    for s in ("修 V1 红线", "评审加入菜单路径", "从 8 条降到 2 条", "9 次都通过", "0.80 → 0.84", "12% → 36%",
              "| 失败原因（评估发现） | 修改（文件） | 预期影响的指标 |"):
        assert s in notes, s


def test_flip_card_counts_match_an_independent_count(tmp_path):
    card = _plain(_card(_page(tmp_path), "story-flips"))
    golden = _golden()
    b = {s["feedback_id"]: s for s in json.loads(V0.read_text(encoding="utf-8"))["sessions"]}
    a = {s["feedback_id"]: s for s in json.loads(V1.read_text(encoding="utf-8"))["sessions"]}
    kinds = {"fixed": 0, "broke": 0, "still": 0, "kept": 0}
    for fid in b:
        rb, ra = _right_after_veto(b[fid], golden.get(fid, {})), _right_after_veto(a[fid], golden.get(fid, {}))
        kinds[{(False, True): "fixed", (True, False): "broke", (False, False): "still", (True, True): "kept"}[(rb, ra)]] += 1
    assert kinds["fixed"] > 0 and kinds["broke"] > 0
    for title, k in (("错 → 对", "fixed"), ("对 → 错", "broke"), ("仍不对", "still"), ("保持对", "kept")):
        assert f"{title} {kinds[k]} 条" in card, (title, kinds[k])


def test_flip_per_field_counts_add_up_to_the_gates():
    fb = compare.feedback_index()
    b, a = compare.EvalRun.load(V0), compare.EvalRun.load(V1)
    specs = compare.label_specs(b, a)
    pb, pa = compare.pillars_of(b, fb, 0.7, specs), compare.pillars_of(a, fb, 0.7, specs)
    golden, _src = compare_story.load_golden()
    d = compare_story.flip_data(b, a, pb, pa, specs, failures.analyze(b.data, golden))
    for k in FIELDS:
        c = d["per_field"][k]
        assert c[(True, True)] + c[(False, True)] == next(g["ok"] for g in pa["gates"] if g["key"] == k)
        assert c[(True, True)] + c[(True, False)] == next(g["ok"] for g in pb["gates"] if g["key"] == k)
    assert sum(len(v) for v in d["items"].values()) == len(b.sessions)


# --------------------------------------------------------------------------- V2 change notes (attendee-written)


def _v2_root(tmp_path, monkeypatch, notes: str | None) -> Path:
    monkeypatch.setattr(labcfg, "ROOT", tmp_path)
    p = tmp_path / "agent" / "s12agent" / "prompts"
    p.mkdir(parents=True)
    shutil.copy(PROMPTS / "v1.md", p / "v1.md")
    (p / "v2.md").write_text((PROMPTS / "v1.md").read_text(encoding="utf-8") + "\n## 新增口径\n- 一条新规则\n",
                             encoding="utf-8")
    shutil.copy(ROOT / "agent" / "s12agent" / "tools.py", tmp_path / "agent" / "s12agent" / "tools.py")
    if notes is not None:
        (p / "v2.changes.md").write_text(notes, encoding="utf-8")
    for name, ver in (("v1", "v1"), ("v2", "v2")):
        d = json.loads(V1.read_text(encoding="utf-8"))
        d["run"].update(version=ver, run_id=f"B-{ver}-x", mode="runtime")
        (tmp_path / f"eval-{name}.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_v2_change_notes_are_shown_and_escaped(tmp_path, monkeypatch):
    notes = (f"V2 针对 V1 的 3 条失败修改 **口径**。\n\n| 失败原因 | 修改（文件） | 预期影响的指标 |\n|---|---|---|\n"
             f"| 紧急被带偏 | `prompts/v2.md` 加事实标准 | 是否紧急准确率 |\n| {INJ} | a \\| b | <img src=x onerror=alert(1)> |\n\n"
             f"- 列表项 {INJ}\n")
    root = _v2_root(tmp_path, monkeypatch, notes)
    page = _page(tmp_path, root / "eval-v1.json", root / "eval-v2.json")
    card = _card(page, "story-changes")
    assert "agent/s12agent/prompts/v2.changes.md" in card
    assert "<td>紧急被带偏</td><td><code>prompts/v2.md</code> 加事实标准</td><td>是否紧急准确率</td>" in card
    assert "<b>口径</b>" in card and "a | b" in card
    assert INJ not in page and "<img src=x" not in page and INJ_ESC in card
    assert "TOOLSETS 里没有 v2，沿用 v1 的工具" in card and "两个版本的工具相同" in card
    assert "prompts/v1.md" in card and "新增 3 行、删去 0 行" in _plain(card)
    assert well_formed(page).scripts == 1


def test_v2_without_change_notes_is_graceful(tmp_path, monkeypatch):
    root = _v2_root(tmp_path, monkeypatch, None)
    page = _page(tmp_path, root / "eval-v1.json", root / "eval-v2.json")
    card = _plain(_card(page, "story-changes"))
    assert "没有找到 agent/s12agent/prompts/v2.changes.md" in card and "不影响对比" in card
    assert "prompts/v1.md" in card            # the prompt / tool diff is still there
    well_formed(page)


def test_untrusted_version_never_becomes_a_path(tmp_path, monkeypatch):
    root = _v2_root(tmp_path, monkeypatch, None)
    (tmp_path / "secret.md").write_text("TOP-SECRET-CONTENT", encoding="utf-8")
    d = json.loads((root / "eval-v2.json").read_text(encoding="utf-8"))
    d["run"]["version"] = "../../../secret"
    (root / "eval-bad.json").write_text(json.dumps(d), encoding="utf-8")
    page = _page(tmp_path, root / "eval-v1.json", root / "eval-bad.json")
    assert "TOP-SECRET-CONTENT" not in page
    assert "版本名不是 v0 / v1 / v2" in _plain(_card(page, "story-changes"))


def test_own_agent_runs_do_not_show_mock_agent_files(tmp_path, monkeypatch):
    root = _v2_root(tmp_path, monkeypatch, "| a | b | c |")
    for n in ("eval-v1.json", "eval-v2.json"):
        d = json.loads((root / n).read_text(encoding="utf-8"))
        d["run"]["mode"] = "own"
        (root / n).write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    page = _page(tmp_path, root / "eval-v1.json", root / "eval-v2.json")
    card = _plain(_card(page, "story-changes"))
    assert "不对比 mock agent 的提示词和工具文件" in card and "G.15" in card
    assert "v2.changes.md" not in card and "prompts/v1.md" not in card


def test_story_survives_minimal_eval_files(tmp_path):
    empty = tmp_path / "eval-empty.json"
    empty.write_text("{}", encoding="utf-8")
    page = _page(tmp_path, empty, empty)
    for cid in ("story-evaluators", "story-problems", "story-changes", "story-flips"):
        assert f'id="{cid}"' in page
    well_formed(page)


def test_render_notes_subset():
    out = compare_story.render_notes("# 标题\n段落 `a<b>` 和 **粗**\n\n1. 一\n2. 二\n- 甲\n\n| x | y |\n| z |\n")
    assert "<h3>标题</h3>" in out and "<code>a&lt;b&gt;</code>" in out and "<b>粗</b>" in out
    assert "<ol><li>一</li><li>二</li></ol><ul><li>甲</li></ul>" in out
    assert "<table><tbody><tr><td>x</td><td>y</td></tr><tr><td>z</td><td></td></tr></tbody></table>" in out
    assert "<script" not in compare_story.render_notes(INJ) and "**" in compare_story.render_notes("`**`")


# ------------------------------------------------------------------------------------------ chapter text


def test_chapter_8_2_asks_the_coding_agent_for_the_change_notes():
    page = (ROOT / "docs" / "08-optimize.html").read_text(encoding="utf-8")
    prompt = page[page.index('id="s-8-2"'):page.index('id="s-8-3"')]
    assert "7. 把总结写进 agent/s12agent/prompts/v2.changes.md" in prompt
    assert "| 失败原因（评估发现） | 修改（文件） | 预期影响的指标 |" in prompt
    assert "v1.changes.md" in prompt.split("6. ")[1].split("\n")[0]   # step 6 protects the shipped V1 notes


def test_chapter_9_2_describes_the_four_cards():
    page = (ROOT / "docs" / "09-compare.html").read_text(encoding="utf-8")
    sec = page[page.index('id="s-9-2"'):page.index('id="s-9-3"')]
    for s in ("① 评估器清单", "② V0 评估发现的问题", "③ 改了什么", "④ 效果验证", "v2.changes.md"):
        assert s in sec, s
    for img in re.findall(r'src="(img/compare-story-[^"]+)"', sec):
        assert (ROOT / "docs" / img).exists(), img
    assert len(re.findall(r'src="img/compare-story-', sec)) == 4


# ------------------------------------------------------------------- adversarial review round 1 (regressions)


def _write(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _row(card: str, fid: str) -> str:
    m = re.search(r'<tr><td><span class="mono">' + re.escape(fid) + r'</span>.*?</tr>', card)
    assert m, fid
    return _plain(m.group(0))


def test_flip_rows_name_only_fields_that_changed_and_the_red_line(tmp_path):
    v1 = json.loads(V1.read_text(encoding="utf-8"))
    s = next(x for x in v1["sessions"] if x["feedback_id"] == "FB-2610-002")   # right in V0 and V1, a 拒绝
    s["scores"]["reject_quality"] = {"value": 0.2, "label": "Fail",
                                     "explanation": "拒绝话术评审 0.2：理由专业性 3。红线：编造配置路径。（红线一票否决，整条不通过）"}
    card = _card(_page(tmp_path, V0, _write(tmp_path / "eval-red.json", v1)), "story-flips")
    broke = card[card.index("对 → 错"):card.index("错 → 对</span>")]
    row = _row(broke, "FB-2610-002")
    assert "话术红线：编造配置路径" in row
    assert "否→否" not in row and "拒绝→拒绝" not in row          # unchanged answers are not listed as changes
    fixed = _row(card[card.index("错 → 对</span>"):], "FB-2610-053")  # V0 misreject, V1 iterates
    assert "建议 拒绝→迭代（期望 迭代）" in fixed and "消除红线" in fixed and "是→是" not in fixed


def test_change_notes_follow_the_version_chain(tmp_path, monkeypatch):
    root = _v2_root(tmp_path, monkeypatch, "基于 v1；依据 results/eval-B-v1-x.json\n\n| a | b | c |\n|-|-|-|\n| 1 | 2 | 3 |\n")
    shutil.copy(PROMPTS / "v0.md", root / "agent" / "s12agent" / "prompts" / "v0.md")
    shutil.copy(PROMPTS / "v1.changes.md", root / "agent" / "s12agent" / "prompts" / "v1.changes.md")
    d = json.loads(V0.read_text(encoding="utf-8"))
    d["run"].update(mode="runtime")
    _write(root / "eval-v0.json", d)
    card = _plain(_card(_page(tmp_path, root / "eval-v0.json", root / "eval-v2.json"), "story-changes"))
    i1, i2 = card.index("V0 → V1 来自 agent/s12agent/prompts/v1.changes.md"), card.index("V1 → V2 来自 agent/s12agent/prompts/v2.changes.md")
    assert i1 < i2 and "基于 v1；依据 results/eval-B-v1-x.json" in card
    assert "a b c 1 2 3" in card                                     # |-|-| is a header separator (GFM)
    for before, after in (("eval-v1.json", "eval-v1.json"), ("eval-v2.json", "eval-v1.json")):
        card = _plain(_card(_page(tmp_path, root / before, root / after), "story-changes"))
        assert "没有“改了什么”的说明" in card and "changes.md" not in card


def test_sessions_without_ids_are_keyed_like_the_pillars(tmp_path):
    v0, v1 = (json.loads(p.read_text(encoding="utf-8")) for p in (V0, V1))
    for d in (v0, v1):
        for s in d["sessions"][:6]:
            s.pop("feedback_id"), s.pop("session_id")
    page = _page(tmp_path, _write(tmp_path / "eval-a.json", v0), _write(tmp_path / "eval-b.json", v1))
    card = _plain(_card(page, "story-flips"))
    assert "—→—" not in card
    fb = compare.feedback_index()
    b, a = compare.EvalRun(v0, "eval-a.json"), compare.EvalRun(v1, "eval-b.json")
    specs = compare.label_specs(b, a)
    pb, pa = compare.pillars_of(b, fb, 0.7, specs), compare.pillars_of(a, fb, 0.7, specs)
    res = failures.analyze(v0, _golden())
    right_after = {k for k, it in pa["_items"].items() if compare_story._right(it, [k2 for _n, k2, _c in specs])}
    expected = sum(1 for w in res["wrong"] if w["fid"] in right_after)
    assert {w["fid"] for w in res["wrong"]} <= set(pb["_items"])     # same keys as the pillar items
    assert f"判错的 {len(res['wrong'])} 条，V1 整条答对 {expected} 条" in card


def test_gate_column_uses_the_gate_counts_when_a_run_lacks_an_item(tmp_path):
    v1 = json.loads(V1.read_text(encoding="utf-8"))
    v1["sessions"] = v1["sessions"][1:]
    page = _page(tmp_path, V0, _write(tmp_path / "eval-short.json", v1))
    fb = compare.feedback_index()
    b, a = compare.EvalRun.load(V0), compare.EvalRun(v1, "eval-short.json")
    specs = compare.label_specs(b, a)
    pb, pa = compare.pillars_of(b, fb, 0.7, specs), compare.pillars_of(a, fb, 0.7, specs)
    card = _plain(_card(page, "story-flips"))
    gb, ga = compare._gate(pb, "important"), compare._gate(pa, "important")
    assert f"{gb['ok']}/{gb['n']} → {ga['ok']}/{ga['n']}" in card and ga["n"] == 29
    assert "只在一次评估里出现 1 条" in card


def test_graded_scheme_shows_the_level_and_says_how_it_counts(tmp_path):
    v0, v1 = (json.loads(p.read_text(encoding="utf-8")) for p in (V0, V1))
    for d, lv, ok in ((v0, "P2", 0.0), (v1, "P1", 1.0)):
        for s in d["sessions"]:
            if not isinstance(s.get("agent_output"), dict):   # an agent error has no output to grade
                continue
            s["agent_output"]["level"] = lv
            s["scores"]["label_level"] = {"value": ok, "label": "Pass" if ok else "Fail"}
    card = _plain(_card(_page(tmp_path, _write(tmp_path / "a.json", v0), _write(tmp_path / "b.json", v1)), "story-flips"))
    assert "优先级 P2→P1" in card and "分级模式下“对”还要求优先级也对" in card


def test_prompt_diff_counts_lines_that_look_like_headers(tmp_path, monkeypatch):
    monkeypatch.setattr(labcfg, "ROOT", tmp_path)
    p = tmp_path / "agent" / "s12agent" / "prompts"
    p.mkdir(parents=True)
    (p / "v1.md").write_text("a\n---\nb\n", encoding="utf-8")
    (p / "v2.md").write_text("a\nb\n++ y\n", encoding="utf-8")
    summary, diff_html = compare_story.prompt_diff("v1", "v2")
    assert summary.endswith("新增 1 行、删去 1 行")
    assert '<span class="del">----</span>' in diff_html and '<span class="add">+++ y</span>' in diff_html


def test_toolsets_reads_later_assignments_and_flags_unreadable_lists(tmp_path, monkeypatch):
    monkeypatch.setattr(labcfg, "ROOT", tmp_path)
    (tmp_path / "agent" / "s12agent").mkdir(parents=True)
    (tmp_path / "agent" / "s12agent" / "tools.py").write_text(
        "def a():\n    '''工具 A。'''\n\ndef b():\n    pass\n\nV1_TOOLS = [a]\n"
        "TOOLSETS: dict = {'v0': [a], 'v1': V1_TOOLS}\nTOOLSETS['v2'] = [a, mod.b]\nTOOLSETS['v3'] = make()\n", encoding="utf-8")
    sets, docs = compare_story.toolsets()
    assert sets == {"v0": ["a"], "v1": ["a"], "v2": ["a", "b"], "v3": None} and docs["a"] == "工具 A。"
    assert compare_story._tools_of(sets, "v3") == (None, "读不到 v3 的工具列表（不是直接写出的列表）")
    assert compare_story._tools_of(sets, "v4")[1] == "TOOLSETS 里没有 v4，沿用 v1 的工具"


def test_problem_card_names_ties_and_lists_red_lines(tmp_path):
    assert compare_story._most({"重要": 1, "紧急": 0, "建议": 1}) == (["重要", "建议"], 1)
    assert compare_story._most({"重要": 0}) == ([], 0)
    card = _plain(_card(_page(tmp_path), "story-problems"))
    res = compare.pillars_of(compare.EvalRun.load(V0), compare.feedback_index(), 0.7)
    assert res["red_line"]["count"] > 0
    assert f"⛔ 红线 {res['red_line']['count']} 条" in card
    for x in res["red_line"]["items"]:
        assert x["feedback_id"] in card


def test_render_notes_fences_and_single_dash_separators():
    out = compare_story.render_notes("```\n# not a heading\n- not a list\n<b>\n```\n| x |\n|:-:|\n| y |\n")
    assert "<pre># not a heading\n- not a list\n&lt;b&gt;</pre>" in out
    assert "<thead><tr><th>x</th></tr></thead><tbody><tr><td>y</td></tr></tbody>" in out
    assert compare_story.render_notes("```\nopen fence <i>").startswith("<pre>open fence &lt;i&gt;</pre>")


def test_sample_compare_page_is_current(tmp_path):
    out = tmp_path / "sample.html"
    assert compare.main(["--before", str(V0), "--after", str(V1), "--out", str(out)]) == 0
    stamp = re.compile(r"生成时间 [^<]*")
    fresh = stamp.sub("", out.read_text(encoding="utf-8"))
    committed = stamp.sub("", (ROOT / "docs" / "compare" / "sample-compare.html").read_text(encoding="utf-8"))
    assert fresh == committed, ("regenerate: python -m labkit.compare --before tests/fixtures/eval-sample-v0.json "
                                "--after tests/fixtures/eval-sample-v1.json --out docs/compare/sample-compare.html")
