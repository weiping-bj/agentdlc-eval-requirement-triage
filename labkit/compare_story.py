"""Chapter 9 compare page: the four cards that tell the optimization story, above the score details.

  1. 评估器清单 - every evaluator found in the two evaluations, what it measures and which pillar it feeds
     (红线 / 门禁 / 观测, the rule in evaluators/shared/pillars.py).
  2. 优化前评估发现的问题 - the 7.5 analysis (labkit.failures.analyze) of the "before" evaluation: wrong items
     by field and by golden five-dimension label, next to the 7.5 reading table (典型失败现象 / 多半是哪里的问题).
  3. 改了什么 - model (run records), prompt (line diff of agent/s12agent/prompts/<v>.md), tools (TOOLSETS in
     agent/s12agent/tools.py, read with ast, no import), capability catalog (not recorded per run: the card says
     so) and the change notes agent/s12agent/prompts/<after version>.changes.md: shipped for v1, written by the
     coding agent in 8.2 for v2, optional (the card says what is missing).
  4. 效果验证 - per-item and per-field flips between the two evaluations. An item is right when every label gate
     is right after the red-line veto (the gates' own rule), so the per-field counts add up to the gate numbers;
     the "before" failures of card 2 are followed per dimension.

Only the mock agent (mode local / runtime on both sides) gets the prompt / tool diff and the change notes: an
own agent or imported logs carry version names that do not refer to these files. A version string is used in
a file path only when it is v<digits>. Everything read from files or evaluation results is escaped.
Standard library only; build_compare imports this module when it renders (no import cycle with failures).
"""
from __future__ import annotations

import ast
import difflib
import re
from collections import Counter
from pathlib import Path

from . import compare as cmp
from . import failures as fl
from . import items as labitems

VERSION_RE = re.compile(r"v\d{1,3}")
MOCK_MODES = {"local", "runtime"}
PROMPTS_DIR = Path("agent") / "s12agent" / "prompts"
TOOLS_FILE = Path("agent") / "s12agent" / "tools.py"
CHANGES_SUFFIX = ".changes.md"
MAX_NOTES = 20000    # characters of a change-notes file shown on the page
MAX_DIFF_LINES = 400

PILLAR_PILL = {"red": ("红线", "r"), "gate": ("门禁", "o"), "obs": ("观测", "n")}
# evaluator -> (pillars it feeds, what it does for the verdict; {t} = the gate threshold)
ROLES = {
    "label_accuracy": (("red", "obs"), "误拒了不可自动拒绝的需求时返回 CriticalFail，算红线（误拒）；"
                                       "格式错误、整条通过率、五维准确率这些观测也从它读"),
    "label_important": (("gate",), "“是否重要”准确率门禁，≥ {t} 才达标"),
    "label_urgent": (("gate",), "“是否紧急”准确率门禁，≥ {t} 才达标"),
    "label_decision": (("gate",), "“处理建议”准确率门禁，≥ {t} 才达标"),
    "label_level": (("gate",), "“优先级”准确率门禁（分级模式），≥ {t} 才达标"),
    "reject_quality": (("red", "gate", "obs"), "评审列出的红线（编造功能或配置路径、承诺上线时间等）算话术红线；"
                                               "评审分 ≥ 0.7 且无红线的拒绝占比是“拒绝话术通过率”门禁（≥ {t}）；"
                                               "五个分项和均分是观测"),
    "reject_quality_llm": (("obs",), "同一套评分标准的原生评审（可挂在线评估），只看平均分，不进结论"),
    "Builtin.Correctness": (("obs",), "只看平均分，不进结论"),
    "Builtin.Helpfulness": (("obs",), "只看平均分，不进结论"),
}
DEFAULT_ROLE = (("obs",), "这一页不把它算进结论，只列出平均分")
EXTRA_DESC = {"label_level": ("自定义 · 代码", "“优先级”单项对错（0/1，分级模式），平均分即准确率")}
FIELD_SHORT = {"important": "重要", "urgent": "紧急", "decision": "建议", "level": "优先级"}
FIELD_FULL = {"重要": "是否重要", "紧急": "是否紧急", "建议": "处理建议", "优先级": "优先级"}  # labkit.failures short names

STORY_CSS = """
.story-nav { font-size: 13px; margin: 12px 0 0; color: #3b4652; }
.story-nav a { margin: 0 2px; }
.story h3 { margin: 14px 0 4px; font-size: 15px; }
.story .lead2 { margin: 2px 0 8px; }
.story details { margin: 8px 0; }
.story summary { cursor: pointer; color: var(--blue); font-size: 13px; }
.story .notes { border: 1px solid var(--line); border-radius: 8px; padding: 8px 16px; margin: 8px 0; }
.story .notes h3, .story .notes h4 { margin: 10px 0 4px; }
.story .notes p { margin: 6px 0; }
.story pre.diff { background: #f7f9fb; color: #23303d; border: 1px solid var(--line); white-space: pre-wrap;
  word-break: break-word; max-height: 520px; overflow: auto; }
.story pre.diff .add { color: #1f7a45; background: #e6f6ec; display: block; }
.story pre.diff .del { color: #b02a2a; background: #fdeaea; display: block; }
.story pre.diff .hunk { color: #6b7785; display: block; }
.story td.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.story th.n { text-align: right; white-space: nowrap; }
.story .split { display: grid; grid-template-columns: minmax(200px, 1fr) minmax(360px, 2.6fr); gap: 18px; }
@media (max-width: 760px) { .story .split { grid-template-columns: 1fr; } }
.story .zero { color: var(--muted); }
.story table.flips td { padding: 5px 10px; }
"""
NAV = (("story-evaluators", "① 评估器清单"), ("story-problems", "② 优化前评估发现的问题"), ("story-changes", "③ 改了什么"),
       ("story-flips", "④ 效果验证"), ("pillars", "三大支柱明细"), ("cases", "逐条对比"))


def nav_html() -> str:
    links = " → ".join(f'<a href="#{i}">{cmp.esc(t)}</a>' for i, t in NAV)
    return f'<p class="story-nav"><b>这一页怎么读：</b>{links}</p>'


def _pills(keys) -> str:
    return "".join(cmp.pill(*PILLAR_PILL[k]) for k in keys)


def _n(v) -> str:
    return f'<td class="n{" zero" if not v else ""}">{cmp.esc(v)}</td>'


# ---------------------------------------------------------------------------------------- 1. evaluators


def evaluators_card(b, a, target: float) -> str:
    names = [n for n in cmp.EVAL_ORDER if n in (b.names() | a.names())]
    names += sorted((b.names() | a.names()) - set(names))
    t = cmp.pct_short(target)
    rows = []
    for name in names:
        typ, desc = cmp.EVAL_DESC.get(name) or EXTRA_DESC.get(name) or ("自定义", "")
        keys, role = ROLES.get(name, DEFAULT_ROLE)
        rows.append(f'<tr><td><b class="mono">{cmp.esc(name)}</b></td><td class="small">{cmp.esc(typ)}</td>'
                    f'<td class="small">{cmp.esc(desc) or "—"}</td><td>{_pills(keys)}</td>'
                    f'<td class="small">{cmp.esc(role.format(t=t))}</td></tr>')
    body = "".join(rows) or '<tr><td colspan="5" class="muted">两次评估结果里都没有评估器分数。</td></tr>'
    return ('<div class="card story" id="story-evaluators"><h2>① 评估器清单：用了哪些评估器，各管什么</h2>'
            f'<p class="small muted lead2">下表是这两次评估结果里出现的评估器。结论按三大支柱判：{_pills(("red",))}一条就不通过，'
            f'{_pills(("gate",))}全部 ≥ {cmp.esc(t)} 才通过，{_pills(("obs",))}只看趋势、不影响结论。</p>'
            '<table><thead><tr><th style="width:17%">评估器</th><th style="width:12%">类型</th><th>衡量什么</th>'
            '<th style="width:12%">属于哪个支柱</th><th style="width:30%">怎么用进结论</th></tr></thead>'
            f'<tbody>{body}</tbody></table>'
            '<p class="small muted">误拒还有一个来源：Agent 判“拒绝”而黄金集标为不可自动拒绝（<code>can_auto_reject = false</code>），'
            '即使评估器没有标出，也算红线。评估器说明见 evaluators/README.md 和手册第 6 章。</p></div>')


# ------------------------------------------------------------------------------------ 2. problems found


def load_golden() -> tuple:
    """({feedback_id: golden row}, repo-relative path) from the first golden file that exists, else ({}, None)."""
    for c in labitems.GOLDEN_CANDIDATES:
        p = cmp.root() / c
        if p.exists():
            try:
                return fl.load_golden(p), c
            except SystemExit:
                return {}, None
    return {}, None


def problems_card(b, pb: dict, analysis: dict, golden_src) -> str:
    vb = b.vlabel
    wrong, other = analysis["wrong"], analysis["other"]
    red = pb["red_line"]["count"]
    fields = analysis["fields"]
    dims = analysis["dims"]
    lead = (f"{vb} 共 {analysis['total']} 条：判错 {len(wrong)} 条（标签评估器 label_accuracy 判为 Fail / CriticalFail），"
            f"其他未通过 {len(other)} 条（格式错误、没评上分，不计入下面的统计），红线 {red} 条（明细见下方三大支柱）。")
    top = []
    if wrong and fields:
        f_name, f_n = max(fields.items(), key=lambda kv: kv[1])
        if f_n:
            top.append(f"错得最多的是“{FIELD_FULL.get(f_name, f_name)}”（{f_n} 条）")
    if wrong and any(dims.values()):
        d_name, d_n = max(dims.items(), key=lambda kv: kv[1])
        top.append(f"维度上{d_name}最多（{d_n} 条）")
    frows = "".join(f"<tr><td>{cmp.esc(FIELD_FULL.get(k, k))}</td>{_n(v)}</tr>" for k, v in fields.items())
    drows = []
    for d in dict.fromkeys([*fl.DIMS, *dims]):
        symptom, where = fl.DIM_HINTS.get(d, ("", ""))
        drows.append(f'<tr><td>{cmp.pill(d, "b")}</td>{_n(dims.get(d, 0))}<td class="small">{cmp.esc(symptom)}</td>'
                     f'<td class="small">{cmp.esc(where)}</td></tr>')
    no_golden = ("" if golden_src else
                 '<div class="warn">找不到黄金集（data/golden/golden_set.jsonl），没有五维标签和标注理由，只能按字段统计。</div>')
    tables = (f'<div class="split"><div><h3>错在哪一项</h3><table><thead><tr><th>字段</th><th class="n">判错条数</th></tr></thead>'
              f'<tbody>{frows}</tbody></table></div>'
              '<div><h3>按五维归因（黄金集标注；一条有多个维度时各计一次）</h3><table><thead><tr><th>维度</th><th class="n">判错条数</th>'
              f'<th>典型失败现象</th><th>多半是哪里的问题</th></tr></thead><tbody>{"".join(drows)}</tbody></table></div></div>')
    wrows = []
    for w in wrong:
        gid = f' <span class="small muted">{cmp.esc(w["gid"])}</span>' if w.get("gid") else ""
        fx = "；".join(f"{n} {e}→{a}" for n, e, a in w["fields"]) or cmp.clip(w.get("explanation") or "—", 120)
        sev = " " + cmp.pill("⛔ 误拒", "r") if w["label"] == "CriticalFail" else ""
        dim_txt = "、".join(w["dims"]) or ("—" if w["in_golden"] else "黄金集里没有这条")
        wrows.append(f'<tr><td><span class="mono">{cmp.esc(w["fid"])}</span>{gid}</td><td>{cmp.esc(fx)}{sev}</td>'
                     f'<td class="small">{cmp.esc(dim_txt)} · {cmp.esc(w.get("case_type") or "—")}</td>'
                     f'<td class="small">{cmp.esc(cmp.clip(w.get("rationale") or "", 90))}</td></tr>')
    orows = "".join(f'<tr><td><span class="mono">{cmp.esc(o["fid"])}</span></td><td colspan="3" class="small">'
                    f'{cmp.esc(cmp.clip(o["reason"], 160))}</td></tr>' for o in other)
    detail = ""
    if wrong or other:
        detail = (f'<details><summary>展开逐条明细：判错 {len(wrong)} 条、其他未通过 {len(other)} 条</summary>'
                  '<table><thead><tr><th style="width:18%">需求</th><th>错在哪（期望→实际）</th><th style="width:16%">五维 · 类型</th>'
                  f'<th style="width:34%">标注理由</th></tr></thead><tbody>{"".join(wrows)}{orows}</tbody></table></details>')
    summary = f'<p><b>{cmp.esc("；".join(top))}。</b></p>' if top else ""
    return ('<div class="card story" id="story-problems">'
            f'<h2>② {cmp.esc(vb)} 评估发现的问题：错在哪一项、属于哪一维</h2>'
            f'<p class="small muted lead2">和手册 7.5 的 <code>labkit.failures</code> 是同一个分析：每条判错的需求按黄金集标的五维'
            '（认知 / 质量 / 责任 / 成本 / 性能）归类，右表的“多半是哪里的问题”就是第 8 章决定改哪里的依据。</p>'
            f'<p>{cmp.esc(lead)}</p>{summary}{no_golden}{tables}{detail}</div>')


# ----------------------------------------------------------------------------------------- 3. changes


def version_of(ev) -> str | None:
    v = (ev.version or "").strip().lower()
    return v if VERSION_RE.fullmatch(v) else None


def is_mock(ev) -> bool:
    return str(ev.run.get("mode") or "").lower() in MOCK_MODES


def _read(path: Path, limit: int | None = None) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit) if limit else f.read()
    except OSError:
        return None


def toolsets() -> tuple:
    """({version: [tool names]}, {tool name: first docstring line}) from tools.py without importing it."""
    src = _read(cmp.root() / TOOLS_FILE)
    if src is None:
        return None, {}
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None, {}
    docs, sets = {}, None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node) or ""
            docs[node.name] = doc.strip().splitlines()[0].strip() if doc.strip() else ""
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TOOLSETS" for t in node.targets) \
                and isinstance(node.value, ast.Dict):
            sets = {}
            for k, v in zip(node.value.keys, node.value.values):
                if isinstance(k, ast.Constant) and isinstance(k.value, str) and isinstance(v, (ast.List, ast.Tuple)):
                    sets[k.value] = [e.id for e in v.elts if isinstance(e, ast.Name)]
    return sets, docs


def _tools_of(sets: dict, v: str) -> tuple:
    """(tool names, note): agent/s12agent/core.py falls back to the v1 tools for a version TOOLSETS lacks."""
    if v in sets:
        return sets[v], ""
    if "v1" in sets:
        return sets["v1"], f"TOOLSETS 里没有 {v}，沿用 v1 的工具"
    return [], f"TOOLSETS 里没有 {v}"


def _tool_list(names, docs, other) -> str:
    out = []
    for n in names:
        tag = "" if n in other else " " + cmp.pill("新增", "g")
        d = docs.get(n) or ""
        out.append(f'<code>{cmp.esc(n)}</code>{tag}' + (f'<span class="small muted">：{cmp.esc(d)}</span>' if d else ""))
    return "<br>".join(out) or "—"


def prompt_diff(vb: str, va: str) -> tuple:
    """(summary text, diff html) of prompts/<vb>.md -> prompts/<va>.md, or (reason, "") when a file is missing."""
    pdir = cmp.root() / PROMPTS_DIR
    tb, ta = _read(pdir / f"{vb}.md"), _read(pdir / f"{va}.md")
    missing = [f"{PROMPTS_DIR.as_posix()}/{v}.md" for v, t in ((vb, tb), (va, ta)) if t is None]
    if missing:
        return "找不到 " + "、".join(missing) + "，无法对比提示词", ""
    lb, la = tb.splitlines(), ta.splitlines()
    diff = list(difflib.unified_diff(lb, la, f"prompts/{vb}.md", f"prompts/{va}.md", n=1, lineterm=""))
    plus = sum(1 for x in diff if x.startswith("+") and not x.startswith("+++"))
    minus = sum(1 for x in diff if x.startswith("-") and not x.startswith("---"))
    summary = (f"prompts/{vb}.md（{len(lb)} 行）→ prompts/{va}.md（{len(la)} 行）："
               + (f"新增 {plus} 行、删去 {minus} 行" if diff else "内容相同"))
    if not diff:
        return summary, ""
    shown = diff[:MAX_DIFF_LINES]
    lines = []
    for x in shown:
        cls = "hunk" if x.startswith("@@") or x.startswith("---") or x.startswith("+++") else \
              ("add" if x.startswith("+") else ("del" if x.startswith("-") else ""))
        lines.append(f'<span class="{cls}">{cmp.esc(x)}</span>' if cls else cmp.esc(x) + "\n")
    more = f'\n…（只显示前 {MAX_DIFF_LINES} 行）' if len(diff) > MAX_DIFF_LINES else ""
    return summary, (f'<details><summary>展开提示词差异（+ 新增 / − 删去）</summary>'
                     f'<pre class="diff">{"".join(lines)}{cmp.esc(more)}</pre></details>')


_TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")
_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _inline(s: str) -> str:
    """Escaped text with `code` and **bold**; nothing else of Markdown is interpreted."""
    parts = _CODE.split(s)
    out = []
    for i, p in enumerate(parts):
        if i % 2:
            out.append(f"<code>{cmp.esc(p)}</code>")
        else:
            out.append(_BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", cmp.esc(p)))
    return "".join(out)


def _cells(row: str) -> list:
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", row)]


def render_notes(text: str) -> str:
    """A small, safe Markdown subset: headings, paragraphs, - / 1. lists, | tables |, `code`, **bold**."""
    out, para, lst, table = [], [], [], []
    state = {"ordered": False}

    def flush_para():
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()

    def flush_list():
        if lst:
            tag = "ol" if state["ordered"] else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(x)}</li>" for x in lst) + f"</{tag}>")
            lst.clear()

    def flush_table():
        if not table:
            return
        rows = [r for r in table if not _TABLE_SEP.match(r)]
        has_head = len(table) > 1 and bool(_TABLE_SEP.match(table[1]))
        head, body = (rows[0], rows[1:]) if has_head and rows else (None, rows)
        width = max([len(_cells(r)) for r in rows] or [1])
        html_ = "<table>"
        if head is not None:
            hc = _cells(head) + [""] * (width - len(_cells(head)))
            html_ += "<thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in hc) + "</tr></thead>"
        html_ += "<tbody>"
        for r in body:
            cs = _cells(r) + [""] * (width - len(_cells(r)))
            html_ += "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cs) + "</tr>"
        out.append(html_ + "</tbody></table>")
        table.clear()

    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("|"):
            flush_para()
            flush_list()
            table.append(s)
            continue
        flush_table()
        if not s:
            flush_para()
            flush_list()
            continue
        m = re.match(r"(#{1,6})\s+(.*)", s)
        if m:
            flush_para()
            flush_list()
            tag = "h3" if len(m.group(1)) <= 2 else "h4"
            out.append(f"<{tag}>{_inline(m.group(2).strip())}</{tag}>")
            continue
        m = re.match(r"(?:[-*+]|(\d+)[.)])\s+(.*)", s)
        if m:
            flush_para()
            ordered = m.group(1) is not None
            if lst and ordered != state["ordered"]:
                flush_list()
            state["ordered"] = ordered
            lst.append(m.group(2))
            continue
        flush_list()
        para.append(s)
    flush_table()
    flush_para()
    flush_list()
    return "".join(out)


def notes_html(va: str | None, after_label: str) -> str:
    if va is None:
        return ""
    rel = (PROMPTS_DIR / f"{va}{CHANGES_SUFFIX}").as_posix()
    text = _read(cmp.root() / rel, MAX_NOTES + 1)
    if text is None:
        return (f'<div class="note">没有找到 <code>{cmp.esc(rel)}</code>，这里没有改动说明（不影响对比）。'
                f'8.2 第 7 步会让 coding agent 把“失败原因 → 修改 → 预期影响的指标”写进这个文件；'
                f'{cmp.esc(after_label)} 是你们自己改的，也可以照这个格式手写一份，再重新生成对比页。</div>')
    cut = len(text) > MAX_NOTES
    body = render_notes(text[:MAX_NOTES]) or '<p class="muted">（文件是空的）</p>'
    more = f'<p class="small muted">（文件较长，只显示前 {MAX_NOTES} 个字符）</p>' if cut else ""
    return (f'<h3>改动说明：失败原因 → 修改 → 预期影响的指标</h3>'
            f'<p class="small muted">来自 <code>{cmp.esc(rel)}</code>（原样显示，按生成对比页时的文件）。</p>'
            f'<div class="notes">{body}{more}</div>')


def changes_card(b, a) -> str:
    vb_l, va_l = b.vlabel, a.vlabel
    mb, ma = str(b.run.get("model") or ""), str(a.run.get("model") or "")
    if mb and mb == ma:
        model = f'未变：<span class="mono">{cmp.esc(ma)}</span>'
    elif mb or ma:
        model = (f'<span class="mono">{cmp.esc(mb or "—")}</span> → <span class="mono">{cmp.esc(ma or "—")}</span> '
                 + cmp.pill("已改", "o"))
    else:
        model = '<span class="muted">运行记录里没有模型</span>'
    rows = [("模型", model)]
    vb, va = version_of(b), version_of(a)
    mock = is_mock(b) and is_mock(a)
    diff_html, extra = "", ""
    if mock and vb and va:
        summary, diff_html = prompt_diff(vb, va)
        rows.append(("提示词", cmp.esc(summary)))
        sets, docs = toolsets()
        if sets is None:
            rows.append(("工具", '<span class="muted">读不到 agent/s12agent/tools.py 的 TOOLSETS</span>'))
        else:
            tb, nb = _tools_of(sets, vb)
            ta, na = _tools_of(sets, va)
            removed = [t for t in tb if t not in ta]
            cell = (f'<div class="grid2"><div><b>{cmp.esc(vb_l)}</b>{(" · " + cmp.esc(nb)) if nb else ""}<br>'
                    f'{_tool_list(tb, docs, tb)}</div><div><b>{cmp.esc(va_l)}</b>{(" · " + cmp.esc(na)) if na else ""}<br>'
                    f'{_tool_list(ta, docs, tb)}</div></div>')
            if removed:
                cell += '<div class="small muted">去掉：' + "、".join(f"<code>{cmp.esc(t)}</code>" for t in removed) + "</div>"
            if tb == ta:
                cell += '<div class="small muted">两个版本的工具相同</div>'
            rows.append(("工具", cell))
        extra = notes_html(va, va_l)
    else:
        why = ("这两次运行不都是 mock agent（自带 Agent 或导入的日志）" if not mock else
               "版本名不是 v0 / v1 / v2 … 这种形式")
        rows.append(("提示词与工具", f'<span class="muted">{cmp.esc(why)}，这里不对比 mock agent 的提示词和工具文件。'
                                     '改了什么请自己记录（自带 Agent 见《自带 Agent 接入指南》G.15）。</span>'))
    rows.append(("能力目录", '<span class="muted">运行记录里没有记下能力目录（data/capability_catalog.json）的版本，'
                            '这里无法自动比较；改过它的话，写进改动说明。</span>'))
    eb, ea = b.evaluators, a.evaluators
    if eb and ea and eb == ea:
        ev_txt = "两次用的是同一组评估器（评估器 ID 相同；ID 相同时 Lambda 代码仍可能原地更新过，见改动说明）"
    elif eb or ea:
        changed = sorted(n for n in set(eb) | set(ea) if eb.get(n) != ea.get(n))
        ev_txt = "评估器不完全相同：" + "；".join(f"{n} {eb.get(n) or '—'} → {ea.get(n) or '—'}" for n in changed[:8]) \
                 + "。打分规则不同时，前后分数不能直接比"
    else:
        ev_txt = "评估结果里没有评估器 ID"
    rows.append(("评估器", cmp.esc(ev_txt)))
    trs = "".join(f'<tr><td style="width:12%"><b>{cmp.esc(k)}</b></td><td>{v}</td></tr>' for k, v in rows)
    return ('<div class="card story" id="story-changes">'
            f'<h2>③ {cmp.esc(vb_l)} → {cmp.esc(va_l)} 改了什么</h2>'
            '<p class="small muted lead2">提示词和工具按生成对比页时仓库里的文件对比；模型和评估器来自两次运行的记录。'
            '优化 Agent 时不改黄金集；评估器要改时，前后两个版本都要用改过的评估器重新打分，分数才可比。</p>'
            f'<table><tbody>{trs}</tbody></table>{diff_html}{extra}</div>')


# ----------------------------------------------------------------------------------------- 4. flips


def _vetoed(it: dict, f: str):
    """Label f of one pillar item after the veto (same rule as the gates in pillars.build)."""
    if it.get("misreject") or cmp.pillars.red_line_kinds(it) or it.get("no_output"):
        return False
    return (it.get("fields") or {}).get(f)


def _right(it: dict, keys) -> bool | None:
    vals = [_vetoed(it, k) for k in keys]
    if any(v is None for v in vals):
        return None
    return all(vals)


def flip_data(b, a, pb: dict, pa: dict, specs, analysis: dict) -> dict:
    keys = [k for _n, k, _c in specs]
    ib, ia = pb["_items"], pa["_items"]
    order = list(ib) + [k for k in ia if k not in ib]
    items_ = {"fixed": [], "broke": [], "still": [], "kept": [], "unknown": [], "one_side": []}
    per_field = {k: Counter() for k in keys}
    for fid in order:
        x, y = ib.get(fid), ia.get(fid)
        if x is None or y is None:
            items_["one_side"].append(fid)
            continue
        for k in keys:
            ob, oa = _vetoed(x, k), _vetoed(y, k)
            if ob is not None and oa is not None:
                per_field[k][(ob, oa)] += 1
        rb, ra = _right(x, keys), _right(y, keys)
        if rb is None or ra is None:
            items_["unknown"].append(fid)
        else:
            items_[{(False, True): "fixed", (True, False): "broke", (False, False): "still",
                    (True, True): "kept"}[(rb, ra)]].append(fid)
    after_right = set(items_["fixed"]) | set(items_["kept"])
    dims = {}
    for w in analysis["wrong"]:
        for d in w["dims"] or ["（无五维标签）"]:
            c = dims.setdefault(d, [0, 0])
            c[0] += 1
            c[1] += int(w["fid"] in after_right)
    other = [o["fid"] for o in analysis["other"]]
    return {"keys": keys, "items": items_, "per_field": per_field, "dims": dims,
            "wrong": (len(analysis["wrong"]), sum(1 for w in analysis["wrong"] if w["fid"] in after_right)),
            "other": (len(other), sum(1 for f in other if f in after_right))}


def _shown_value(c, k) -> str:
    """The agent's label as shown in the flip lists; 无输出 / 格式错误 when there was nothing to read."""
    if not c:
        return ""
    v = cmp.text(c.get("labels", {}).get(k, (None,))[0])
    if v:
        return v
    if c.get("agent_error"):
        return "无输出"
    return "格式错误" if c.get("format_error") else ""


def _changes_of(fid, x, y, keys, cases_b, cases_a, fb, want) -> str:
    """'建议 待评估→拒绝（期望 拒绝）' for the fields whose (before, after) after-veto state is `want`."""
    exp = cmp.as_dict(cmp.as_dict(fb.get(fid)).get("expected"))
    cb, ca = cases_b.get(fid), cases_a.get(fid)
    out = []
    for k in keys:
        if (_vetoed(x, k), _vetoed(y, k)) != want:
            continue
        vb, va = _shown_value(cb, k), _shown_value(ca, k)
        e = f"（期望 {exp.get(k)}）" if exp.get(k) not in (None, "") else ""
        out.append(f"{FIELD_SHORT.get(k, k)} {vb or '—'}→{va or '—'}{e}")
    return "；".join(out)


def _red_txt(it, tag) -> str:
    ks = cmp.pillars.red_line_kinds(it) if it else []
    return "；".join(f"{tag} {'误拒' if k['kind'] == 'misreject' else '话术红线'}：{cmp.clip(k['detail'], 80)}" for k in ks)


def flips_card(b, a, pb: dict, pa: dict, specs, analysis: dict, fb: dict) -> str:
    vb, va = b.vlabel, a.vlabel
    d = flip_data(b, a, pb, pa, specs, analysis)
    it = d["items"]
    keys = d["keys"]
    ib, ia = pb["_items"], pa["_items"]
    cases_b = {cmp.case_of(s)["fid"]: cmp.case_of(s) for s in b.sessions}
    cases_a = {cmp.case_of(s)["fid"]: cmp.case_of(s) for s in a.sessions}
    tiles = (("错 → 对", "fixed", f"{vb} 不对、{va} 整条答对", "up"), ("对 → 错", "broke", f"{vb} 整条答对、{va} 不对", "down"),
             ("仍不对", "still", "两次都没有整条答对", "flat"), ("保持对", "kept", "两次都整条答对", "flat"))
    head = '<div class="kpis">' + "".join(
        f'<div class="kpi"><div class="k">{cmp.esc(t)}</div><div class="v"><span class="{c if it[k] else "flat"}">'
        f'{len(it[k])}</span> 条</div><div class="d">{cmp.esc(sub)}</div></div>' for t, k, sub, c in tiles) + "</div>"
    frows = []
    for k in keys:
        c = d["per_field"][k]
        name = cmp.pillars.FIELD_CN.get(k, k)
        frows.append(f"<tr><td>{cmp.esc(name)}</td>{_n(c[(False, True)])}{_n(c[(True, False)])}{_n(c[(False, False)])}"
                     f"{_n(c[(True, True)])}<td class=\"n\">{cmp.esc(c[(True, True)] + c[(True, False)])} → "
                     f"{cmp.esc(c[(True, True)] + c[(False, True)])}</td></tr>")
    ftable = ('<h3>按字段：每一项的翻转</h3><table><thead><tr><th>字段</th><th class="n">错 → 对</th><th class="n">对 → 错</th>'
              f'<th class="n">仍错</th><th class="n">保持对</th><th class="n">答对条数 {cmp.esc(vb)} → {cmp.esc(va)}</th></tr></thead>'
              f'<tbody>{"".join(frows)}</tbody></table>')
    drows = "".join(f'<tr><td>{cmp.pill(dn, "b")}</td>{_n(n)}{_n(ok)}{_n(n - ok)}</tr>' for dn, (n, ok) in d["dims"].items())
    on, ook = d["other"]
    if on:
        drows += f'<tr><td class="small">其他未通过（格式错误等）</td>{_n(on)}{_n(ook)}{_n(on - ook)}</tr>'
    dtable = ""
    if drows:
        wn, wok = d["wrong"]
        dtable = (f'<h3>② 里 {cmp.esc(vb)} 判错的 {wn} 条，{cmp.esc(va)} 整条答对 {wok} 条（按五维）</h3>'
                  f'<table><thead><tr><th>维度</th><th class="n">{cmp.esc(vb)} 判错</th><th class="n">{cmp.esc(va)} 整条答对</th>'
                  f'<th class="n">{cmp.esc(va)} 仍不对</th></tr></thead><tbody>{drows}</tbody></table>')

    def no_output(c):
        if not c:
            return ""
        if c.get("agent_error") and not c.get("out"):
            return "无输出"
        return "格式错误" if c.get("format_error") else ""

    def lst(fids, want, title, cls, empty, hint=""):
        rows = []
        for fid in fids:
            info = cmp.as_dict(fb.get(fid))
            x, y = ib[fid], ia[fid]
            gone = no_output(cases_b.get(fid) if want == (False, True) else cases_a.get(fid))
            if gone and want == (False, True):
                ch = f"{vb} {gone}（没有可读的分诊结果），{va} 各项都对"
            elif gone:
                ch = f"{va} {gone}（没有可读的分诊结果）"
            else:
                ch = _changes_of(fid, x, y, keys, cases_b, cases_a, fb, want)
            red = _red_txt(x if want == (False, True) else y, vb if want == (False, True) else va)
            if want == (False, True) and red:
                red = "消除红线（" + red + "）"
            label = " · ".join(str(v) for v in (info.get("golden_id"), cmp.clip(info.get("title"), 26)) if v)
            sub = f' <span class="small muted">{cmp.esc(label)}</span>' if label else ""
            dims = "、".join(info.get("dimensions") or []) or "—"
            what = "；".join(t for t in (ch, red) if t) or "—"
            rows.append(f'<tr><td><span class="mono">{cmp.esc(fid)}</span>{sub}</td><td class="small">{cmp.esc(dims)}</td>'
                        f'<td>{cmp.esc(what)}</td></tr>')
        body = "".join(rows) or f'<tr><td colspan="3" class="muted">{cmp.esc(empty)}</td></tr>'
        hint_html = f' <span class="small muted">{cmp.esc(hint)}</span>' if hint else ""
        return (f'<h3>{cmp.pill(title, cls)} {len(fids)} 条{hint_html}</h3><table class="flips"><thead><tr><th style="width:36%">需求</th>'
                f'<th style="width:12%">五维</th><th>变了什么（{cmp.esc(vb)}→{cmp.esc(va)}；括号里是期望）</th></tr></thead>'
                f'<tbody>{body}</tbody></table>')

    extra = []
    if it["unknown"]:
        extra.append(f"无评分 {len(it['unknown'])} 条")
    if it["one_side"]:
        extra.append(f"只在一次评估里出现 {len(it['one_side'])} 条")
    extra_txt = f'<p class="small muted">不计入上面四类：{cmp.esc("，".join(extra))}。</p>' if extra else ""
    return ('<div class="card story" id="story-flips">'
            f'<h2>④ 效果验证：哪些需求 错 → 对、对 → 错（{cmp.esc(vb)} → {cmp.esc(va)}）</h2>'
            '<p class="small muted lead2">“对” = 各项标签都对且没有红线（和门禁同一口径：红线条目整条判错，没有输出按错计）。'
            '“对 → 错”只算原来整条答对、后来不对的；下方逐条对比里的“退步”还包括原来就不全对、答对项数又变少的。'
            '对照 ③ 里的“预期影响的指标”看：变化是哪些需求带来的，有没有新的错误。</p>'
            f'{head}{extra_txt}{ftable}{dtable}'
            f'{lst(it["broke"], (True, False), "对 → 错", "r", "没有：原来答对的都还对", "先看有没有新的错误")}'
            f'{lst(it["fixed"], (False, True), "错 → 对", "g", "没有")}'
            '<p class="small muted">大模型每次运行有波动：“对 → 错”的条目先看原始输出（下方逐条对比，悬停可见理由），'
            '同一版本多跑几次再下结论，不要靠重跑把错误“跑掉”。</p></div>')


# ------------------------------------------------------------------------------------------- page


def story_html(b, a, pb: dict, pa: dict, specs, target: float, fb: dict) -> str:
    golden, src = load_golden()
    analysis = fl.analyze(b.data, golden)
    return (evaluators_card(b, a, target) + problems_card(b, pb, analysis, src) + changes_card(b, a)
            + flips_card(b, a, pb, pa, specs, analysis, fb))
