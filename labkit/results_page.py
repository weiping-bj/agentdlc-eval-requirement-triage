"""Chapters 4/5: one interactive results page for one or more evaluated runs (V0, V1, V1 参考版, later V2 ...).

  python -m labkit.results_page                          # default: the latest evaluation of each version
  python -m labkit.results_page <run_id|eval json> [...]  # these evaluations, in the order given
  python -m labkit.results_page --out page.html

Inputs are results/eval-<run_id>.json (labkit.evaluate, registered in .lab-state/evals.json) plus, when it is still on
disk, the run file results/run-<run_id>.jsonl (labkit.batch) for the model's raw output. The page shows, per version,
the verdict (red lines, gates, pass rates), a trend across the versions, and a filterable item list; clicking an item
opens a drawer with the requirement, the golden answer, each version's agent output, the tool calls of its trace
(recorded by labkit.evaluate from the spans it already fetched; older evaluation files have none) and every
evaluator's score and explanation.

Self-contained like the compare page (CSS inline, inline SVG, one inline script, no external asset). Everything taken
from the data (agent output, evaluator explanations, tool payloads, ids) is HTML-escaped on the server; the script
only reads data-* attributes the generator wrote from constants and indexes, and only toggles visibility.
Prints the output path on stdout (status lines go to stderr). Standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from labkit import compare as cmp
from labkit import compare_story as story
from labkit import items as labitems
from labkit.compare import as_dict, clip, esc, parse_obj, pill, pillars, text

MAX_VERSIONS = 8
RAW_MAX = 8000          # characters of the model's raw output shown per version
EXPL_MAX = 4000         # characters of one evaluator explanation
FOOTER = "数据为合成数据；分数来自 AgentCore Evaluations（按需评估）；Agent 输出、工具返回和评估器解释按原文显示（已转义）"
ACCENT = cmp.AFTER_COLOR
FIELD_SHORT = story.FIELD_SHORT
OUT_KEYS = (("important", "是否重要"), ("urgent", "是否紧急"), ("decision", "处理建议"), ("level", "优先级"))
TEXT_KEYS = (("reasoning", "理由"), ("reject_reason", "拒绝理由（内部）"), ("customer_reply", "客户话术"))
KNOWN_OUT = {k for k, _ in OUT_KEYS} | {k for k, _ in TEXT_KEYS} | {"feedback_id", "_warnings", "_parse_error"}
LABEL_CLASS = {"pass": "g", "fail": "r", "criticalfail": "r", "formaterror": "r"}

CSS = """
.rp-runs { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; }
.rp-runs .runbox td:first-child { width: 132px; }
.rp-filters { display: flex; gap: 8px 14px; flex-wrap: wrap; align-items: center; margin: 4px 0 10px; }
.rp-filters .grp { display: flex; gap: 4px; align-items: center; flex-wrap: wrap; }
.rp-filters .lab { font-size: 12px; color: var(--muted); margin-right: 2px; }
.rp-filters select { font: inherit; font-size: 12px; padding: 2px 6px; border: 1px solid var(--line); border-radius: 6px; }
table.rp-list td { vertical-align: middle; }
tr.rp-row { cursor: pointer; }
tr.rp-row:hover td, tr.rp-row:focus td { background: #f7f9fc; }
tr.rp-row:focus { outline: 2px solid var(--blue); outline-offset: -2px; }
th.cur, td.cur { background: #eef4fe; }
th.base, td.base { background: #f6f7f9; }
.rp-open { border: 1px solid var(--line); background: #fff; border-radius: 6px; padding: 1px 10px; font: inherit; font-size: 12px;
  cursor: pointer; color: var(--blue); }
#rp-backdrop { position: fixed; inset: 0; background: rgba(15, 23, 42, .35); z-index: 40; }
#rp-drawer { position: fixed; top: 0; right: 0; bottom: 0; width: min(920px, 96vw); background: #fff; z-index: 50;
  box-shadow: -8px 0 24px rgba(0, 0, 0, .18); display: flex; flex-direction: column; }
#rp-drawer[hidden], #rp-backdrop[hidden], .rp-item[hidden], #rp-hint[hidden] { display: none; }
.rp-bar { display: flex; align-items: center; gap: 10px; padding: 10px 20px; border-bottom: 1px solid var(--line); }
.rp-bar .sp { flex: 1; }
.rp-bar button { border: 1px solid var(--line); background: #fff; border-radius: 6px; padding: 3px 12px; font: inherit; cursor: pointer; }
.rp-body { overflow-y: auto; padding: 6px 24px 36px; }
.rp-item h3 { margin: 16px 0 6px; font-size: 15px; }
.rp-item h4 { margin: 12px 0 4px; font-size: 13.5px; }
.rp-item table.kv td:first-child { white-space: nowrap; color: var(--muted); width: 120px; }
table.rp-tools td { vertical-align: top; }
.rp-ver { border: 1px solid var(--line); border-radius: 8px; padding: 6px 14px; margin: 10px 0; }
.rp-ver > summary { cursor: pointer; font-weight: 600; padding: 4px 0; }
pre.rp-pre { white-space: pre-wrap; word-break: break-word; background: #f7f9fb; color: #23303d; border: 1px solid var(--line);
  border-radius: 6px; padding: 6px 10px; margin: 0; max-height: 300px; overflow: auto; font-size: 12px; }
.rp-quote { white-space: pre-wrap; word-break: break-word; background: #fafbfc; border-left: 3px solid var(--line); padding: 6px 12px; }
td.reply { background: #fbfaff; }
.spark svg { display: block; }
.rp-item ul { margin: 2px 0; padding-left: 20px; }
"""

# Only reads data-* attributes the generator wrote (constants / indexes) and toggles visibility; never builds markup.
JS = r"""(function () {
  var rows = Array.prototype.slice.call(document.querySelectorAll('#items tbody tr.rp-row'));
  var drawer = document.getElementById('rp-drawer'), backdrop = document.getElementById('rp-backdrop');
  var cur = {state: 'all', field: 'all', dim: 'all'};
  function num(id, dflt) {
    var e = document.getElementById(id), n = e ? parseInt(e.value, 10) : dflt;
    return isNaN(n) ? dflt : n;
  }
  function has(tags, t) { return tags.indexOf(' ' + t + ' ') >= 0; }
  function apply() {
    var t = num('rp-target', 0), b = num('rp-base', -1), shown = 0;
    rows.forEach(function (tr) {
      var tags = ' ' + (tr.getAttribute('data-tags') || '') + ' ', ok = true, st = cur.state;
      if (st === 'bad') { ok = has(tags, 'v' + t + ':bad'); }
      else if (st === 'red') { ok = has(tags, 'v' + t + ':red'); }
      else if (st === 'fixed') { ok = b >= 0 && b !== t && has(tags, 'v' + b + ':bad') && has(tags, 'v' + t + ':ok'); }
      else if (st === 'broke') { ok = b >= 0 && b !== t && has(tags, 'v' + b + ':ok') && has(tags, 'v' + t + ':bad'); }
      if (ok && cur.field !== 'all') { ok = has(tags, 'v' + t + ':f-' + cur.field); }
      if (ok && cur.dim !== 'all') { ok = has(tags, cur.dim); }
      tr.style.display = ok ? '' : 'none';
      if (ok) { shown++; }
    });
    document.querySelectorAll('[data-col]').forEach(function (c) {
      var k = parseInt(c.getAttribute('data-col'), 10);
      c.classList.toggle('cur', k === t);
      c.classList.toggle('base', k === b && b !== t);
    });
    document.querySelectorAll('[data-rp]').forEach(function (el) {
      el.classList.toggle('active', cur[el.getAttribute('data-rp')] === el.getAttribute('data-v'));
    });
    var n = document.getElementById('rp-count');
    if (n) { n.textContent = '显示 ' + shown + ' / ' + rows.length + ' 条'; }
    var hint = document.getElementById('rp-hint');
    if (hint) { hint.hidden = !((cur.state === 'fixed' || cur.state === 'broke') && (b < 0 || b === t)); }
  }
  function setHash(h) {
    try { if (history.replaceState) { history.replaceState(null, '', h); } } catch (err) { /* sandboxed: ignore */ }
  }
  function openItem(k) {
    if (!/^\d+$/.test(String(k))) { return; }
    var sec = document.getElementById('item-' + k);
    if (!sec || !drawer) { return; }
    document.querySelectorAll('.rp-item').forEach(function (s) { s.hidden = s !== sec; });
    var t = num('rp-target', 0), b = num('rp-base', -1);
    sec.querySelectorAll('details.rp-ver').forEach(function (d) {
      var v = parseInt(d.getAttribute('data-ver'), 10);
      d.open = v === t || v === b;
    });
    drawer.hidden = false;
    if (backdrop) { backdrop.hidden = false; }
    var body = drawer.querySelector('.rp-body');
    if (body) { body.scrollTop = 0; }
    document.body.style.overflow = 'hidden';
    setHash('#item-' + k);
    var c = document.getElementById('rp-close');
    if (c) { c.focus(); }
  }
  function closeItem() {
    if (!drawer || drawer.hidden) { return; }
    drawer.hidden = true;
    if (backdrop) { backdrop.hidden = true; }
    document.body.style.overflow = '';
    setHash('#list');
  }
  document.addEventListener('click', function (e) {
    var el = e.target.closest ? e.target.closest('[data-rp], tr.rp-row, #rp-close, #rp-backdrop') : null;
    if (!el) { return; }
    if (el.id === 'rp-close' || el.id === 'rp-backdrop') { closeItem(); return; }
    if (el.hasAttribute('data-rp')) {
      e.preventDefault();
      cur[el.getAttribute('data-rp')] = el.getAttribute('data-v');
      apply();
      return;
    }
    openItem(el.getAttribute('data-item'));
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeItem(); return; }
    var el = document.activeElement;
    if ((e.key === 'Enter' || e.key === ' ') && el && el.classList && el.classList.contains('rp-row')) {
      e.preventDefault();
      openItem(el.getAttribute('data-item'));
    }
  });
  ['rp-target', 'rp-base'].forEach(function (id) {
    var s = document.getElementById(id);
    if (s) { s.addEventListener('change', apply); }
  });
  apply();
  var m = /^#item-(\d+)$/.exec(location.hash || '');
  if (m) { openItem(m[1]); }
})();"""


# ---------------------------------------------------------------------------------------------- data


def version_key(v: str) -> tuple:
    """v0 < v1 < v1-ref < v2 ...; anything else after them, in input order (stable sort)."""
    m = re.fullmatch(r"v(\d{1,3})(-ref)?", str(v or "").strip().lower())
    return (0, int(m.group(1)), 1 if m.group(2) else 0) if m else (1, 0, 0)


def requirement_index() -> dict:
    """{feedback_id: {"item": agent input, "golden": golden row or None}} from the golden set and the feedback pool."""
    golden, _src = story.load_golden()
    idx = {fid: {"item": labitems.item_of(row), "golden": row} for fid, row in golden.items()}
    for c in labitems.FEEDBACK_CANDIDATES:
        for row in cmp.read_jsonl_safe(cmp.root() / c):
            fid = labitems.fid_of(row)
            if fid and fid not in idx:
                idx[fid] = {"item": labitems.item_of(row), "golden": None}
    return idx


def run_records(ev: cmp.EvalRun) -> tuple:
    """({session_id or feedback_id: run record}, shown path or None): the run file this evaluation scored, if local."""
    cands = []
    f = text(ev.run.get("file")).strip()
    if f:
        p = Path(f)
        cands.append(p if p.is_absolute() else cmp.root() / p)
    if cmp.safe_name(ev.run_id) == ev.run_id:  # next to the eval file; never a path built from odd characters
        cands.append(ev.path.parent / f"run-{ev.run_id}.jsonl")
    for p in cands:
        if p.is_file():
            out = {}
            for r in cmp.read_jsonl_safe(p):
                for k in (r.get("session_id"), r.get("feedback_id")):
                    if k:
                        out.setdefault(str(k), r)
            return out, cmp.shown_path(p)
    return {}, None


def fid_of(s: dict, i: int) -> str:
    """Same key as compare.pillar_items."""
    return str(s.get("feedback_id") or s.get("session_id") or f"#{i + 1}")


class Version:
    """One evaluated run on the page."""

    def __init__(self, ev: cmp.EvalRun, idx: int, label: str, specs: tuple, fb: dict, target: float):
        self.ev, self.idx, self.label = ev, idx, label
        self.p = cmp.pillars_of(ev, fb, target, specs)
        self.items = self.p.pop("_items")
        self.sessions, self.cases = {}, {}
        for i, s in enumerate(ev.sessions):
            k = fid_of(s, i)
            self.sessions[k] = s
            self.cases[k] = cmp.case_of(s)
        self.runs, self.run_file = run_records(ev)
        self.fields = [k for _n, k, _c in specs]

    def state(self, fid: str) -> dict:
        """Item state: ok (every label right after the veto, no red line) / bad / na (not scored) / none (absent)."""
        it, s = self.items.get(fid), self.sessions.get(fid)
        if it is None or s is None:
            return {"state": "none", "red": [], "wrong": [], "rej_fail": False}
        red = pillars.red_line_kinds(it)
        flds = it.get("fields") or {}
        vetoed = [False if (red or it.get("no_output")) else flds.get(f) for f in self.fields]
        st = "na" if any(v is None for v in vetoed) else ("ok" if all(vetoed) and not red else "bad")
        wrong = list(self.fields) if it.get("no_output") else [f for f in self.fields if flds.get(f) is False]
        rej = it.get("reject") or {}
        rej_fail = rej.get("status") == "judged" and (not rej.get("passed") or bool(rej.get("red_flags")))
        return {"state": st, "red": red, "wrong": wrong, "rej_fail": rej_fail, "eval_error": s.get("error"),
                "agent_error": s.get("agent_error")}


def labels_for(evs: list) -> list:
    """Display names; a version shown twice gets （1）（2）."""
    names = [ev.vlabel for ev in evs]
    out = []
    for i, n in enumerate(names):
        if names.count(n) > 1:
            n = f"{n}（{names[:i + 1].count(n)}）"
        out.append(n)
    return out


# ---------------------------------------------------------------------------------------------- overview + trend


def _verdict_pill(p: dict) -> str:
    return pill(pillars.VERDICT_CN.get(p["verdict"], p["verdict"]),
                {"pass": "g", "fail": "r"}.get(p["verdict"], "o"))


def overview_card(vs: list) -> str:
    boxes = []
    for v in vs:
        p, ev = v.p, v.ev
        rl = p["red_line"]
        letter, plabel = cmp.path_of(ev.run.get("mode"))
        obs = p["observations"]
        rows = [("结论", _verdict_pill(p)),
                ("红线", f'{cmp._rl_cell(rl["count"], rl["count"] == 0)} <span class="small muted">误拒 '
                       f'{rl["by_kind"].get("misreject", 0)} · 话术红线 {rl["by_kind"].get("reject_red_flag", 0)}</span>')]
        for g in p["gates"]:
            rows.append((g["name"], f'{esc(cmp._gate_txt(g))} {cmp._gate_badge(g)}'))
        rows += [("整条通过率（观测）", esc(cmp.pct(obs["all_correct_rate"]))),
                 ("条数", esc(f"{len(ev.sessions)} 条 · Agent 出错 {ev.agent_errors()} · 评估出错 "
                             f"{sum(1 for s in ev.sessions if s.get('error'))}")),
                 ("run_id", f'<span class="mono">{esc(ev.run_id)}</span>'),
                 ("运行位置 · 模型", f'{esc(plabel)} · <span class="mono">{esc(ev.run.get("model") or "—")}</span>'),
                 ("评估时间", esc(cmp.fmt_time(ev.data.get("evaluated_at"))))]
        trs = "".join(f"<tr><td>{esc(k)}</td><td>{val}</td></tr>" for k, val in rows)
        boxes.append(f'<div class="runbox"><h3>{esc(v.label)} {pill(letter, "o" if letter == "Runtime" else "p")}</h3>'
                     f'<table>{trs}</table></div>')
    return ('<div class="card" id="overview"><h2>每个版本的结论：红线 0 条，且门禁各 ≥ 门槛，才算通过</h2>'
            '<p class="small muted">口径和终端里 ./43-run-eval.sh 的结论相同（evaluators/shared/pillars.py）：红线条目整条判错，'
            'Agent 出错的条目按错计，评估出错的条目不计入分母。</p>'
            f'<div class="rp-runs">{"".join(boxes)}</div></div>')


def spark(vals: list, labels: list, kind: str, target: float | None) -> str:
    """One series across the versions on its own small axis (0-100% for rates, 0-max for counts), dots with tooltips."""
    W, H, px, py = 168, 44, 10, 8
    n = len(vals)
    xs = [px + (W - 2 * px) * (i / (n - 1) if n > 1 else 0.5) for i in range(n)]
    top = 1.0 if kind == "pct" else max([v for v in vals if v is not None] + [1])

    def y(v):
        return py + (H - 2 * py) * (1 - max(0.0, min(1.0, v / top)))

    parts = [f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="各版本趋势" '
             'xmlns="http://www.w3.org/2000/svg">',
             f'<line x1="{px}" x2="{W - px}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="#c9d1dc" stroke-width="1"/>']
    if kind == "pct" and target is not None:
        parts.append(f'<line x1="{px}" x2="{W - px}" y1="{y(target):.1f}" y2="{y(target):.1f}" stroke="{cmp.TARGET_COLOR}" '
                     'stroke-width="1" stroke-dasharray="4 3"/>')
    seg = []
    for x, v in zip(xs, vals):
        if v is None:
            if len(seg) > 1:
                parts.append(f'<polyline points="{" ".join(seg)}" fill="none" stroke="{ACCENT}" stroke-width="2"/>')
            seg = []
            continue
        seg.append(f"{x:.1f},{y(v):.1f}")
    if len(seg) > 1:
        parts.append(f'<polyline points="{" ".join(seg)}" fill="none" stroke="{ACCENT}" stroke-width="2"/>')
    for x, v, lab in zip(xs, vals, labels):
        if v is None:
            continue
        shown = cmp.pct(v) if kind == "pct" else f"{v:g} 条"
        parts.append(f'<g><title>{esc(f"{lab}：{shown}")}</title><circle cx="{x:.1f}" cy="{y(v):.1f}" r="8" fill="transparent"/>'
                     f'<circle cx="{x:.1f}" cy="{y(v):.1f}" r="4" fill="{ACCENT}" stroke="#fff" stroke-width="2"/></g>')
    parts.append("</svg>")
    return "".join(parts)


def trend_card(vs: list, target: float) -> str:
    labels = [v.label for v in vs]
    if len(vs) < 2:
        return ('<div class="card" id="trend"><h2>版本趋势</h2><p class="muted">现在只有一个版本。第 5 章评估过 V1 之后再运行 '
                '<span class="mono">./45-results-page.sh</span>，这里会按版本画出红线、门禁和整条通过率的变化。</p></div>')
    metrics = [("红线（条，要求 0）", [v.p["red_line"]["count"] for v in vs], "count", None)]
    for g in vs[-1].p["gates"]:
        metrics.append((g["name"], [cmp._gate(v.p, g["key"])["actual"] for v in vs], "pct", g.get("threshold", target)))
    metrics.append(("整条通过率（观测）", [v.p["observations"]["all_correct_rate"] for v in vs], "pct", None))
    head = "".join(f'<th class="num" data-col="{v.idx}">{esc(v.label)}</th>' for v in vs)
    rows = []
    for name, vals, kind, thr in metrics:
        cells = []
        for v, x in zip(vs, vals):
            if kind == "count":
                cells.append(f'<td class="num" data-col="{v.idx}">{cmp._rl_cell(x, x == 0)}</td>')
            else:
                ok = None if x is None or thr is None else x >= thr
                badge = "" if ok is None else (pill("✓", "g") if ok else pill("✗", "r"))
                cells.append(f'<td class="num" data-col="{v.idx}">{esc(cmp.pct(x))} {badge}</td>')
        first = next((x for x in vals if x is not None), None)
        last = next((x for x in reversed(vals) if x is not None), None)
        delta = cmp.delta_html(first, last, kind, higher_is_better=(kind != "count"))
        thr_txt = "0 条" if kind == "count" else ("—" if thr is None else f"≥ {cmp.pct_short(thr)}")
        rows.append(f'<tr><td>{esc(name)}</td><td class="num small">{esc(thr_txt)}</td>{"".join(cells)}'
                    f'<td class="num">{delta}</td><td class="spark">{spark(vals, labels, kind, thr)}</td></tr>')
    return ('<div class="card" id="trend"><h2>版本趋势（' + esc(" → ".join(labels)) + '）</h2>'
            '<p class="small muted">每一行是一个指标在各版本上的值；右侧小图是同一行的趋势（红色虚线 = 门槛，悬停圆点看数值）。'
            '“首 → 末”是第一个和最后一个版本之间的变化。</p>'
            f'<table><thead><tr><th>指标</th><th class="num">门槛</th>{head}<th class="num">首 → 末</th><th>趋势</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


# ---------------------------------------------------------------------------------------------- item list


def _state_cell(v: Version, st: dict) -> str:
    s = st["state"]
    if s == "none":
        body = pill("—", "n", f"{v.label}：这次评估里没有这条需求")
    elif s == "na":
        why = st.get("eval_error") or "缺少单项评估结果"
        body = pill("? 无评分", "n", f"{v.label}：{clip(why, 300)}")
    elif s == "ok":
        body = pill("✓ 全对", "g", f"{v.label}：各项都对，没有红线")
    elif st["red"]:
        detail = "\n".join(f"{pillars.KIND_CN[k['kind']]}：{k['detail']}" for k in st["red"])
        body = pill("⛔ 红线", "r", f"{v.label} 红线（一票否决，整条判错）\n{clip(detail, 400)}")
    else:
        names = "·".join(FIELD_SHORT.get(f, f) for f in st["wrong"]) or "错"
        body = pill(f"✗ {names}", "r", f"{v.label}：单项评估器判错的字段：{names}")
    if st.get("rej_fail") and not st["red"]:
        body += pill("话术 ✗", "o", f"{v.label}：拒绝话术评审未通过")
    return f'<td data-col="{v.idx}">{body}</td>'


def _tags(vs: list, fid: str, dims: list, dim_idx: dict) -> str:
    tags = []
    for v in vs:
        st = v.state(fid)
        p = f"v{v.idx}:"
        tags.append(p + {"ok": "ok", "bad": "bad", "na": "na", "none": "none"}[st["state"]])
        if st["red"]:
            tags.append(p + "red")
        tags += [p + "f-" + f for f in st["wrong"]]
        if st["rej_fail"]:
            tags.append(p + "f-rej")
    tags += [f"dim-{dim_idx[d]}" for d in dims if d in dim_idx]
    return " ".join(tags)


def _expected_txt(g: dict | None) -> str:
    if not g:
        return ""
    exp = labitems.expected_of(g)
    acc = g.get("acceptable_decisions")
    dec = "/".join(map(str, acc)) if isinstance(acc, list) and acc else exp.get("decision")
    return f"重要 {text(exp.get('important')) or '—'} · 紧急 {text(exp.get('urgent')) or '—'} · {text(dec) or '—'}"


def list_card(vs: list, order: list, req: dict, fb: dict) -> str:
    dims = [d for d in pillars.DIMS]
    seen = {str(d) for fid in order for d in (as_dict(fb.get(fid)).get("dimensions") or [])}
    dims = [d for d in dims if d in seen] + sorted(seen - set(dims))
    dim_idx = {d: i for i, d in enumerate(dims)}
    rows = []
    for k, fid in enumerate(order):
        info = as_dict(fb.get(fid))
        item = as_dict(as_dict(req.get(fid)).get("item"))
        title = info.get("title") or item.get("title")
        gid = info.get("golden_id")
        who = (f'<span class="mono">{esc(fid)}</span>' + (f' <span class="small muted">{esc(gid)}</span>' if gid else "")
               + (f'<div class="small">{esc(clip(title, 46))}</div>' if title else ""))
        ds = [str(d) for d in info.get("dimensions") or []]
        dcell = "".join(pill(d, "n") for d in ds) or '<span class="muted small">—</span>'
        exp = _expected_txt(as_dict(req.get(fid)).get("golden"))
        cells = "".join(_state_cell(v, v.state(fid)) for v in vs)
        rows.append(f'<tr class="rp-row" data-item="{k}" data-tags="{esc(_tags(vs, fid, ds, dim_idx))}" tabindex="0">'
                    f'<td>{who}</td><td>{dcell}</td><td class="small">{esc(exp) or "—"}</td>{cells}'
                    f'<td><button type="button" class="rp-open">详情</button></td></tr>')
    opts = "".join(f'<option value="{v.idx}"{" selected" if v is vs[-1] else ""}>{esc(v.label)}</option>' for v in vs)
    base_opts = '<option value="-1">（不对照）</option>' + ("".join(
        f'<option value="{v.idx}"{" selected" if v is vs[0] else ""}>{esc(v.label)}</option>' for v in vs) if len(vs) > 1 else "")

    def btn(group, val, label):
        return (f'<button type="button" class="fbtn{" active" if val == "all" else ""}" data-rp="{group}" data-v="{esc(val)}">'
                f'{esc(label)}</button>')

    states = [("all", "全部"), ("bad", "判错（含红线）"), ("red", "含红线")]
    if len(vs) > 1:
        states += [("fixed", "错 → 对"), ("broke", "对 → 错")]
    fields = [(f, f"{pillars.FIELD_CN.get(f, f)}错") for f in vs[0].fields] + [("rej", "话术未通过")]
    filters = (
        '<div class="rp-filters">'
        f'<span class="grp"><span class="lab">看版本</span><select id="rp-target" aria-label="看哪个版本">{opts}</select></span>'
        f'<span class="grp"><span class="lab">对照</span><select id="rp-base" aria-label="和哪个版本对照">{base_opts}</select></span>'
        '<span class="small muted" id="rp-count"></span></div>'
        '<div class="rp-filters"><span class="grp"><span class="lab">状态</span>'
        + "".join(btn("state", v, lab) for v, lab in states) + '</span></div>'
        '<div class="rp-filters"><span class="grp"><span class="lab">字段</span>' + btn("field", "all", "全部")
        + "".join(btn("field", v, lab) for v, lab in fields) + '</span>'
        '<span class="grp"><span class="lab">五维</span>' + btn("dim", "all", "全部")
        + "".join(btn("dim", f"dim-{i}", d) for d, i in dim_idx.items()) + '</span></div>'
        '<div class="warn small" id="rp-hint" hidden>“错 → 对 / 对 → 错”要在“对照”里选另一个版本。</div>')
    head = "".join(f'<th data-col="{v.idx}">{esc(v.label)}</th>' for v in vs)
    return ('<div class="card" id="list"><h2>逐条查看（点一行打开详情）</h2>'
            '<p class="small muted">每个版本一列：<span class="pill g">✓ 全对</span>= 各项都对且没有红线；'
            '<span class="pill r">✗ 重要·建议</span>= 单项评估器判错的字段；<span class="pill r">⛔ 红线</span>= 一票否决，整条判错；'
            '<span class="pill n">? 无评分</span>= 评估出错或缺少结果。“看版本”决定状态和字段筛选看哪一列；'
            '“错 → 对 / 对 → 错”比较“对照”和“看版本”两列。字段筛选按单项评估器的原始判定（红线条目也照原样）。</p>'
            + filters +
            f'<table id="items" class="rp-list"><thead><tr><th style="width:24%">需求</th><th>五维</th><th>期望</th>{head}'
            '<th></th></tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>')


# ---------------------------------------------------------------------------------------------- drawer


def _pre(v, n: int) -> str:
    s = text(v)
    return f'<pre class="rp-pre">{esc(clip(s, n) if len(s) > n else s)}</pre>'


def requirement_html(fid: str, entry: dict, info: dict) -> str:
    item = as_dict(entry.get("item"))
    content = text(item.get("content") or item.get("description")).strip()
    meta = " · ".join(f"{lab} {text(item.get(k))}" for k, lab in (
        ("customer", "客户"), ("customer_tier", "客户等级"), ("industry", "行业"), ("feedback_type", "反馈类型"),
        ("channel", "来源"), ("module", "模块"), ("submitted_at", "提交"), ("contract_commitment", "合同承诺"))
        if item.get(k) not in (None, ""))
    title = text(item.get("title") or info.get("title"))
    body = (f'<p><b>{esc(title)}</b></p>' if title else "") + (
        f'<div class="rp-quote">{esc(content)}</div>' if content else '<p class="muted">本地数据里没有这条需求的原文。</p>')
    return '<h3>需求原文</h3>' + body + (f'<p class="small muted">{esc(meta)}</p>' if meta else "")


def golden_html(g: dict | None, graded: bool) -> str:
    if not g:
        return '<h3>黄金集答案</h3><p class="muted">这条不在黄金集里，没有标准答案（标签评估器会返回 Skipped）。</p>'
    exp = labitems.expected_of(g)
    acc = g.get("acceptable_decisions")
    dec = text(exp.get("decision"))
    if isinstance(acc, list) and len(acc) > 1:
        dec += f"（可接受：{'/'.join(map(str, acc))}）"
    rows = [("期望标签", f"是否重要 {text(exp.get('important')) or '—'} · 是否紧急 {text(exp.get('urgent')) or '—'} · "
                      f"处理建议 {dec or '—'}" + (f" · 优先级 {text(exp.get('level'))}" if graded and exp.get("level") else ""))]
    car = g.get("can_auto_reject")
    if car is not None:
        rows.append(("可否自动拒绝", "是" if car is True else ("否（数据 / 安全 / 合规 / 合同 / 缺陷类，判拒绝就是误拒）"
                                                          if car is False else text(car))))
    dims = "、".join(map(str, g.get("dimensions") or [])) or "—"
    rows.append(("五维 · 类型", f"{dims} · {text(g.get('case_type')) or '—'}"))
    if g.get("matched_capabilities"):
        rows.append(("相关能力", "、".join(map(str, g["matched_capabilities"]))))
    trs = "".join(f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in rows)
    if g.get("rationale"):
        trs += f'<tr><td>标注理由</td><td class="prewrap">{esc(g["rationale"])}</td></tr>'
    for key, lab in (("reject_points", "拒绝要点"), ("reply_points", "话术要点")):
        pts = [text(x) for x in g.get(key) or [] if text(x).strip()]
        if pts:
            trs += f"<tr><td>{esc(lab)}</td><td><ul>" + "".join(f"<li>{esc(x)}</li>" for x in pts) + "</ul></td></tr>"
    gid = text(g.get("golden_id"))
    return f'<h3>黄金集答案{(" · " + esc(gid)) if gid else ""}</h3><table class="kv">{trs}</table>'


def _label_pill(e: dict) -> str:
    if cmp.is_error(e):
        return pill(text(e.get("errorCode")) or "出错", "n")
    lab = text(e.get("label")).strip()
    if not lab:
        return pill("—", "n")
    return pill(lab, "n" if cmp.is_na(lab) else LABEL_CLASS.get(lab.lower().replace(" ", ""), "b"))


def output_html(v: Version, fid: str, g: dict | None) -> str:
    s, c = v.sessions[fid], v.cases[fid]
    out = c["out"]
    parts = []
    if s.get("agent_error"):
        parts.append(f'<div class="danger">Agent 出错（这一条没有输出，门禁里按错计）：{esc(clip(s.get("agent_error"), 600))}</div>')
    if c["format_error"]:
        parts.append('<div class="warn">FormatError：有回答但解析不出约定的 JSON，三项都按错计。</div>')
    if out.get("_parse_error"):
        parts.append(f'<div class="warn">输出解析失败：{esc(clip(out.get("_parse_error"), 300))}</div>')
    exp = labitems.expected_of(g) if g else {}
    rows = []
    for key, cn in OUT_KEYS:
        if key == "level" and "level" not in v.fields and text(out.get("level")).strip().lower() in ("", "none", "null"):
            continue
        val = text(cmp.first_present(out, cmp.ALIASES.get(key, (key,)))) if key in cmp.ALIASES else text(out.get(key))
        ok = c["labels"][key][1] if key in c["labels"] else None
        if key == "level" and "level" in v.fields:
            ok = (v.items.get(fid) or {}).get("fields", {}).get("level")
        mark = {True: pill("✓", "g", "单项评估器判对"), False: pill("✗", "r", "单项评估器判错"), None: ""}[ok]
        want = text(exp.get(key)) if (key != "level" or "level" in v.fields) else ""
        if key == "decision" and g and isinstance(g.get("acceptable_decisions"), list) and len(g["acceptable_decisions"]) > 1:
            want = "/".join(map(str, g["acceptable_decisions"]))
        rows.append(f'<tr><td>{esc(cn)}</td><td>{esc(val or "—")} {mark}'
                    + (f' <span class="small muted">期望 {esc(want)}</span>' if want else "") + "</td></tr>")
    for key, cn in TEXT_KEYS:
        val = text(out.get(key)).strip()
        cls = "prewrap reply" if key == "customer_reply" else "prewrap"
        rows.append(f'<tr><td>{esc(cn)}</td><td class="{cls}">{esc(val) if val else "—"}</td></tr>')
    extra = {k: val for k, val in out.items() if k not in KNOWN_OUT}
    if extra:
        rows.append(f'<tr><td>其它字段</td><td>{_pre(json.dumps(extra, ensure_ascii=False), 1500)}</td></tr>')
    if out.get("_warnings"):
        rows.append(f'<tr><td>取值警告</td><td class="small">{esc(clip(out.get("_warnings"), 400))}</td></tr>')
    parts.append(f'<table class="kv">{"".join(rows)}</table>' if out else '<p class="muted">没有结构化输出。</p>')
    rec = v.runs.get(text(s.get("session_id"))) or v.runs.get(fid)
    raw = rec.get("raw") if rec else None
    if raw not in (None, ""):
        parts.append(f'<details><summary class="small">模型原始输出（run 文件里的 raw）</summary>{_pre(raw, RAW_MAX)}</details>')
    elif v.run_file is None:
        parts.append('<p class="small muted">本地没有这次运行的 run 文件，原始输出看不到（结构化输出来自评估结果）。</p>')
    return "<h4>Agent 输出</h4>" + "".join(parts)


def tools_html(v: Version, fid: str) -> str:
    s = v.sessions[fid]
    ids = (f'session <span class="mono">{esc(s.get("session_id") or "—")}</span> · trace '
           f'<span class="mono">{esc(s.get("trace_id") or "—")}</span> · span {esc(s.get("span_docs") or "—")} 个'
           + (f" · 耗时 {cmp.num(s.get('latency_ms')) / 1000:.1f} 秒" if cmp.num(s.get("latency_ms")) is not None else ""))
    calls = s.get("tool_calls")
    if not isinstance(calls, list):
        body = ('<p class="small muted">这份评估结果没有记录工具调用（加入这项记录之前评估的，或这一条没有取到 trace）。'
                f'重新运行 <span class="mono">./43-run-eval.sh --run {esc(v.ev.run_id)}</span> 会记下来（只重新评估，不重跑 Agent）；'
                '也可以在 CloudWatch → GenAI Observability → Bedrock AgentCore → 你的 Agent → Sessions 里按下面的 session 查看 trace。</p>')
    elif not calls:
        body = '<p class="small muted">这一条没有调用工具（trace 里没有 execute_tool span）。</p>'
    else:
        trs = []
        for i, t in enumerate(calls, 1):
            t = as_dict(t)
            st = text(t.get("status"))
            badge = pill(st or "—", "g" if st.lower() in ("success", "ok") else ("r" if st else "n"))
            dur = cmp.num(t.get("duration_ms"))
            trs.append(f'<tr><td class="num">{i}</td><td class="mono">{esc(t.get("name") or "—")}</td>'
                       f'<td>{_pre(t.get("input"), 1500)}</td><td>{_pre(t.get("output"), 3000)}</td>'
                       f'<td class="small">{badge}{"" if dur is None else f"<br>{dur:g} ms"}</td></tr>')
        body = ('<table class="rp-tools"><thead><tr><th>#</th><th>工具</th><th style="width:24%">输入</th><th>返回</th>'
                '<th>状态</th></tr></thead><tbody>' + "".join(trs) + "</tbody></table>")
    return f'<h4>工具调用</h4>{body}<p class="small muted">{ids}</p>'


def evaluators_html(v: Version, fid: str) -> str:
    s = v.sessions[fid]
    sc = as_dict(s.get("scores"))
    if s.get("error"):
        head = f'<div class="warn">这一条评估出错，不计入门禁分母：{esc(clip(s.get("error"), 400))}</div>'
    else:
        head = ""
    if not sc:
        return f"<h4>评估器</h4>{head}<p class=\"muted\">没有评估器结果。</p>"
    names = [n for n in cmp.EVAL_ORDER if n in sc] + sorted(set(sc) - set(cmp.EVAL_ORDER))
    trs = []
    for n in names:
        e = cmp.entry_of(sc[n])
        roles = story.ROLES.get(n, story.DEFAULT_ROLE)[0]
        val = cmp.num(e.get("value"))
        expl = text(e.get("explanation")).strip()
        err = " ".join(x for x in (text(e.get("errorCode")), text(e.get("errorMessage"))) if x)
        cell = (esc(clip(expl, EXPL_MAX)) if expl else "") + (
            f'<div class="small" style="color:var(--red)">{esc(clip(err, 400))}</div>' if err else "")
        trs.append(f'<tr><td><span class="mono">{esc(n)}</span><div>{story._pills(roles)}</div></td>'
                   f'<td>{_label_pill(e)}</td><td class="num">{"—" if val is None else f"{val:.2f}"}</td>'
                   f'<td class="prewrap small">{cell or "—"}</td></tr>')
    return (f"<h4>评估器</h4>{head}<table><thead><tr><th>评估器 · 支柱</th><th>结果</th><th class=\"num\">分数</th>"
            f"<th>解释（评估器原文）</th></tr></thead><tbody>{''.join(trs)}</tbody></table>")


def version_block(v: Version, fid: str, g: dict | None, is_open: bool) -> str:
    st = v.state(fid)
    if st["state"] == "none":
        return (f'<details class="rp-ver" data-ver="{v.idx}"><summary>{esc(v.label)} · '
                f'{pill("这次评估里没有这条", "n")}</summary></details>')
    cell = _state_cell(v, st)[len(f'<td data-col="{v.idx}">'):-len("</td>")]
    red = ""
    if st["red"]:
        red = '<div class="danger"><b>红线（一票否决，整条判错）</b>' + "".join(
            f'<div>{esc(pillars.KIND_CN[k["kind"]])}：{esc(k["detail"])}</div>' for k in st["red"]) + "</div>"
    return (f'<details class="rp-ver" data-ver="{v.idx}"{" open" if is_open else ""}>'
            f'<summary>{esc(v.label)} · {cell} <span class="small muted mono">{esc(v.ev.run_id)}</span></summary>'
            f'{red}{output_html(v, fid, g)}{tools_html(v, fid)}{evaluators_html(v, fid)}</details>')


def drawer_html(vs: list, order: list, req: dict, fb: dict, graded: bool) -> str:
    secs = []
    for k, fid in enumerate(order):
        entry = as_dict(req.get(fid))
        info = as_dict(fb.get(fid))
        g = entry.get("golden")
        gid = info.get("golden_id") or (g or {}).get("golden_id")
        blocks = "".join(version_block(v, fid, g, v is vs[-1]) for v in vs)
        secs.append(f'<section class="rp-item" id="item-{k}" hidden>'
                    f'<h2 style="margin-top:12px"><span class="mono">{esc(fid)}</span>'
                    + (f' <span class="small muted">{esc(gid)}</span>' if gid else "") + "</h2>"
                    f'{requirement_html(fid, entry, info)}{golden_html(g, graded)}'
                    f'<h3>各版本（展开的是“看版本”和“对照”选中的两个）</h3>{blocks}</section>')
    return ('<div id="rp-backdrop" hidden></div><aside id="rp-drawer" hidden aria-label="需求详情">'
            '<div class="rp-bar"><b>需求详情</b><span class="sp"></span><span class="small muted">Esc 关闭</span>'
            '<button type="button" id="rp-close">关闭 ✕</button></div>'
            f'<div class="rp-body">{"".join(secs)}</div></aside>')


# ---------------------------------------------------------------------------------------------- page


def build_page(evs: list, target: float) -> str:
    if not evs:
        raise SystemExit("❌ 没有可显示的评估结果")
    labels = labels_for(evs)
    fb = cmp.feedback_index()
    req = requirement_index()
    specs = cmp.label_specs(*evs)
    vs = [Version(ev, i, labels[i], specs, fb, target) for i, ev in enumerate(evs)]
    order = list(dict.fromkeys(k for v in vs for k in v.sessions))
    notes = []
    if len({text(v.ev.run.get("model")) for v in vs}) > 1:
        notes.append("这几个版本用的模型不同，变化里同时包含模型的影响。")
    sets = {frozenset(v.sessions) for v in vs}
    if len(sets) > 1:
        notes.append("这几次评估的需求集合不完全一致：缺的条目在对应的列里显示“—”。")
    if len({text(v.ev.run.get("mode")) for v in vs}) > 1:
        notes.append("这几次运行的位置不同（Runtime / 自托管 / 导入），对比仅供参考。")
    if not any(isinstance(s.get("tool_calls"), list) for v in vs for s in v.ev.sessions):
        notes.append("这些评估结果都没有记录工具调用（加入这项记录之前评估的）：详情里只显示 session / trace 编号，"
                     "重新运行 ./43-run-eval.sh --run <run_id> 后再生成本页即可看到。")
    notes_html = "".join(f'<div class="note">{esc(n)}</div>' for n in notes)
    t = cmp.pct_short(target)
    head = (f'<div class="card"><h2>{esc(" → ".join(labels))}：{len(order)} 条需求 · 红线 0 条且门禁各 ≥{esc(t)} 才通过</h2>'
            + "".join(cmp._verdict_box(v.label, v.p) for v in reversed(vs)) + notes_html
            + '<p class="story-nav"><b>这一页怎么读：</b><a href="#overview">每个版本的结论</a> → <a href="#trend">版本趋势</a> → '
              '<a href="#list">逐条查看</a>（点一行看需求原文、黄金集答案、Agent 输出、工具调用和每个评估器的解释）</p></div>')
    region = next((v.ev.data.get("region") for v in vs if v.ev.data.get("region")), "—")
    bar = cmp.topbar(f"AgentDLC 动手实验 · 评估结果 {' → '.join(labels)}",
                     "需求反馈分诊 Agent · 按需评估结果：结论、趋势、逐条详情",
                     [("版本", " · ".join(labels)), ("最新 run", vs[-1].ev.run_id), ("区域", region)])
    body = head + overview_card(vs) + trend_card(vs, target) + list_card(vs, order, req, fb) + \
        drawer_html(vs, order, req, fb, cmp.LEVEL_SPEC in specs)
    return cmp.page(f"评估结果 {' → '.join(labels)} · AgentDLC 动手实验", bar, body, FOOTER,
                    cmp.PILLAR_CSS + story.STORY_CSS + CSS, JS)


def pick_default() -> list:
    """The latest evaluation of each version, same mode as the latest Runtime / self-hosted evaluation, by version."""
    evals = [e for e in cmp.load_state_list("evals") if e.get("file")]
    if not evals:
        raise SystemExit("❌ .lab-state/evals.json 里还没有评估记录：先运行 ./43-run-eval.sh（第 4.5 步），"
                         "或直接指定 eval JSON：./45-results-page.sh results/eval-<run_id>.json")
    main_mode = next((e.get("mode") for e in reversed(evals) if e.get("mode") in story.MOCK_MODES), evals[-1].get("mode"))
    latest = {}
    for e in evals:
        if e.get("mode") == main_mode:
            latest[str(e.get("version") or "?").lower()] = e
    picked = sorted(latest.values(), key=lambda e: version_key(e.get("version")))
    paths = []
    for e in picked:
        p = cmp.root() / str(e["file"])
        if p.exists():
            paths.append(p)
        else:
            print(f"⚠️  找不到 {e['file']}（{e.get('run_id')}），跳过", file=sys.stderr)
    if not paths:
        raise SystemExit("❌ evals.json 里登记的评估结果文件都不在了：重新运行 ./43-run-eval.sh")
    if len(paths) > MAX_VERSIONS:
        print(f"⚠️  版本超过 {MAX_VERSIONS} 个，只显示最后 {MAX_VERSIONS} 个", file=sys.stderr)
        paths = paths[-MAX_VERSIONS:]
    return paths


def out_name(evs: list) -> str:
    ids = [cmp.safe_name(ev.run_id) for ev in evs]
    return f"results-page-{ids[0]}.html" if len(ids) == 1 else f"results-page-{ids[0]}-to-{ids[-1]}.html"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m labkit.results_page",
                                 description="生成评估结果页（自包含静态 HTML）：每个版本的结论、趋势、逐条详情")
    ap.add_argument("runs", nargs="*", metavar="run_id",
                    help="评估的 run_id 或 results/eval-…json，可以给多个（按给出的顺序）；默认：每个版本最近一次评估")
    ap.add_argument("--out", help="输出 HTML（默认：results/results-page-<第一个 run_id>[-to-<最后一个 run_id>].html）")
    a = ap.parse_args(argv)
    if len(a.runs) > MAX_VERSIONS:
        ap.error(f"最多 {MAX_VERSIONS} 次评估")
    paths = [cmp.resolve_input(r, "evals") for r in a.runs] if a.runs else pick_default()
    for p in paths:
        if not Path(p).is_file():
            raise SystemExit(f"❌ 找不到评估结果文件：{p}")
    evs = [cmp.EvalRun.load(p) for p in paths]
    target = cmp.target_accuracy()
    labels = labels_for(evs)
    print(f"▶ 结果页：{' → '.join(f'{lab} {ev.run_id}' for lab, ev in zip(labels, evs))}", file=sys.stderr)
    out = Path(a.out) if a.out else cmp.root() / "results" / out_name(evs)
    path = cmp.write_page(out, build_page(evs, target))
    print("✅ 结果页已生成（本地文件，可直接用浏览器打开）：", file=sys.stderr)
    print(cmp.shown_path(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
