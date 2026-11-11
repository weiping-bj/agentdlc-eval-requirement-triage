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
