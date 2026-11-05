"""Chapter 9: static HTML page comparing two on-demand evaluations (V0 baseline vs V1 optimized).

  python -m labkit.compare                                  # before = earliest v0 eval, after = latest other version
  python -m labkit.compare --before results/eval-B-v0-....json --after results/eval-B-v1-....json
  python -m labkit.compare --before <eval json|run_id> --after <eval json|run_id> --out page.html

Inputs are the results/eval-<run_id>.json files written by labkit.evaluate (registered in
.lab-state/evals.json). The page is self-contained (CSS inlined from docs/assets/style.css, inline SVG
chart, tiny inline filter script, no external assets) so it opens from a local file or from S3.
Prints the output path on stdout (status lines go to stderr). Standard library only.

The page-building helpers at the top of this module are shared with labkit.report.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from labkit import config as labcfg
from labkit import items as labitems

# ----------------------------------------------------------------------------------------------
# Shared page helpers (also used by labkit.report)
# ----------------------------------------------------------------------------------------------

SEED_POOL = "data/tools/seed/pool.jsonl"  # last-resort source of titles when data files are absent
NA_LABELS = {"skipped", "不适用", "not applicable", "notapplicable", "not_applicable", "n/a", "na"}
PATHS = {"local": ("A", "A 版 · 自有 Agent（本地运行）"), "runtime": ("B", "B 版 · AgentCore Runtime")}

FALLBACK_CSS = (
    "body{margin:0;background:#f4f6f9;color:#1f2933;font:14px/1.7 -apple-system,'PingFang SC','Microsoft YaHei',sans-serif}"
    ":root{--navy:#1f3350;--blue:#2f6fdc;--muted:#6b7785;--line:#e3e7ee;--green:#1f9d55;--red:#d64545;--orange:#d9822b;"
    "--purple:#7b5bd6;--green-soft:#e6f6ec;--red-soft:#fdeaea;--orange-soft:#fdf1e3;--blue-soft:#e8f0fd;--purple-soft:#f0ebfd}"
    "#topbar{background:var(--navy);color:#fff;padding:10px 24px;display:flex;gap:16px;align-items:center}"
    "#topbar .title{font-size:17px;font-weight:700}#topbar .subtitle,#topbar .meta{font-size:12px;color:#c9d4e3}"
    "#topbar .spacer{flex:1}.card{background:#fff;border-radius:10px;padding:22px 28px;margin-bottom:18px;border:1px solid #edf0f4}"
    "table{border-collapse:collapse;width:100%;font-size:13px}th{text-align:left;color:var(--muted);border-bottom:1px solid var(--line);"
    "padding:7px 10px;background:#fafbfc}td{border-bottom:1px solid #f0f2f5;padding:7px 10px;vertical-align:top}"
    ".pill{display:inline-block;font-size:11.5px;border-radius:4px;padding:0 7px;border:1px solid;margin:1px 2px;white-space:nowrap}"
    ".pill.g{color:var(--green);background:var(--green-soft)}.pill.r{color:var(--red);background:var(--red-soft)}"
    ".pill.o{color:var(--orange);background:var(--orange-soft)}.pill.b{color:var(--blue);background:var(--blue-soft)}"
    ".pill.p{color:var(--purple);background:var(--purple-soft)}.tip,.warn,.note,.danger{border-radius:8px;padding:10px 14px;margin:10px 0}"
    ".tip{background:var(--green-soft)}.warn{background:var(--orange-soft)}.note{background:var(--blue-soft)}.danger{background:var(--red-soft)}"
    "footer{color:var(--muted);font-size:12px;text-align:center;padding:20px}"
)

EXTRA_CSS = """
main.wrap { max-width: 1280px; margin: 0 auto; padding: 20px 28px 40px; }
.muted { color: var(--muted); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }
.small { font-size: 12px; }
.num { font-variant-numeric: tabular-nums; white-space: nowrap; }
.pill.n { color: var(--muted); background: #f3f5f8; border-color: #d5dbe3; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(172px, 1fr)); gap: 12px; margin: 4px 0 0; }
.kpi { background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 12px 16px; text-align: left; font: inherit; color: inherit; }
.kpi .k { font-size: 12.5px; color: var(--muted); display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.kpi .v { font-size: 26px; font-weight: 700; line-height: 1.35; margin-top: 2px; }
.kpi .v .from { color: var(--muted); font-size: 18px; font-weight: 600; }
.kpi .v .arrow { color: var(--muted); font-size: 16px; font-weight: 400; margin: 0 6px; }
.kpi .d { font-size: 12px; color: var(--muted); }
button.kpi { cursor: pointer; }
button.kpi:hover { border-color: var(--blue); }
.active.kpi, .cell.active { outline: 2px solid var(--blue); outline-offset: -1px; }
.up { color: var(--green); font-weight: 600; }
.down { color: var(--red); font-weight: 600; }
.flat { color: var(--muted); }
.runs { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 12px; }
.runbox { border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px; }
.runbox h3 { margin-bottom: 6px; }
.runbox table { margin: 0; }
.runbox td { border: 0; padding: 2px 8px 2px 0; }
.runbox td:first-child { color: var(--muted); width: 84px; white-space: nowrap; }
.legend { display: flex; gap: 18px; flex-wrap: wrap; font-size: 12px; color: #46525e; margin: 4px 0 6px; }
.legend .sw { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 6px; vertical-align: -1px; }
.legend .dash { display: inline-block; width: 18px; border-top: 2px dashed #d64545; margin-right: 6px; vertical-align: 3px; }
.chart { max-width: 720px; }
.chart svg { display: block; width: 100%; height: auto; }
.filters { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin: 6px 0 4px; }
.fbtn { border: 1px solid var(--line); background: #fff; border-radius: 6px; padding: 3px 12px; font: inherit; font-size: 12px; cursor: pointer; color: #3b4652; }
.fbtn:hover { border-color: var(--blue); }
.fbtn.active { background: var(--navy); border-color: var(--navy); color: #fff; }
table.cases td { vertical-align: middle; }
.arr { color: var(--muted); margin: 0 2px; }
.exp { font-size: 11.5px; color: var(--muted); }
.eid { color: var(--muted); font-size: 11px; word-break: break-all; }
.prewrap { white-space: pre-wrap; word-break: break-word; }
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap: 18px; }
"""

# Generic tag filter: any element with data-target + data-f filters `<target> tbody tr[data-tags]`.
FILTER_JS = """(function () {
  function apply(target, f, label) {
    var n = 0;
    document.querySelectorAll(target + ' tbody tr').forEach(function (tr) {
      var tags = ' ' + (tr.getAttribute('data-tags') || '') + ' ';
      var ok = f === 'all' || tags.indexOf(' ' + f + ' ') >= 0;
      tr.style.display = ok ? '' : 'none';
      if (ok) { n++; }
    });
    document.querySelectorAll('[data-target="' + target + '"][data-f]').forEach(function (el) {
      el.classList.toggle('active', el.getAttribute('data-f') === f);
    });
    var st = document.querySelector('[data-status-for="' + target + '"]');
    if (st) { st.textContent = (f === 'all' ? '全部' : label) + '：' + n + ' 条'; }
  }
  document.addEventListener('click', function (e) {
    var el = e.target.closest ? e.target.closest('[data-f][data-target]') : null;
    if (!el) { return; }
    e.preventDefault();
    var target = el.getAttribute('data-target');
    apply(target, el.getAttribute('data-f'), el.getAttribute('data-label') || el.textContent.trim());
    if (el.hasAttribute('data-scroll')) {
      var t = document.querySelector(target);
      if (t) { t.scrollIntoView({behavior: 'smooth', block: 'start'}); }
    }
  });
})();"""


def root() -> Path:
    """Repo root (read at call time so tests can point it at a temporary directory)."""
    return Path(labcfg.ROOT)


def esc(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def num(v):
    """A finite float from int/float/numeric string, else None (bools are not numbers here)."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
    elif isinstance(v, str):
        try:
            f = float(v.strip())
        except ValueError:
            return None
    else:
        return None
    return f if math.isfinite(f) else None


def as_dict(v) -> dict:
    return v if isinstance(v, dict) else {}


def parse_obj(v) -> dict:
    """Agent output may be a dict, a JSON string, or garbage: always return a dict."""
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            o = json.loads(v)
        except ValueError:
            return {}
        return o if isinstance(o, dict) else {}
    return {}


def text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def clip(v, n: int) -> str:
    s = text(v).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def is_na(label) -> bool:
    return isinstance(label, str) and label.strip().lower() in NA_LABELS


def pct(v, digits: int = 1) -> str:
    return "—" if v is None else f"{v * 100:.{digits}f}%"


def pct_short(v) -> str:
    return "—" if v is None else f"{v * 100:g}%"


def score_fmt(v) -> str:
    return "—" if v is None else f"{v:.2f}"


def fmt_time(v) -> str:
    if not v:
        return "—"
    s = str(v).strip()
    dt = None
    if "-" in s:
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            dt = None
    if dt is None:
        try:
            dt = datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return s
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def path_of(mode) -> tuple:
    return PATHS.get(str(mode or "").lower(), ("?", str(mode) if mode else "—"))


def safe_name(s) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(s or "x")).strip("_") or "x"


def target_accuracy(default: float = 0.70) -> float:
    try:
        v = num(labcfg.load(require_suffix=False)["TARGET_ACCURACY"])
        if v is not None and 0 < v <= 1:
            return v
        if v is not None and 1 < v <= 100:  # someone wrote 70 instead of 0.70
            return v / 100
    except Exception:  # noqa: BLE001 - the page must render even without a usable config
        pass
    return default


def load_css() -> str:
    try:
        css = (root() / "docs" / "assets" / "style.css").read_text(encoding="utf-8")
    except OSError:
        css = FALLBACK_CSS
    return css.replace("</", "<\\/")  # never close the <style> element early


def read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"❌ 无法读取 {path}：{e}")


def read_jsonl_safe(path: Path) -> list:
    out = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def load_state_list(name: str) -> list:
    p = root() / ".lab-state" / f"{name}.json"
    if not p.exists():
        return []
    data = read_json(p)
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def resolve_input(arg: str, state: str) -> Path:
    """A path (cwd-relative, repo-relative or absolute) or a run_id registered in .lab-state/<state>.json."""
    p = Path(arg).expanduser()
    if p.is_absolute() or p.exists():
        return p.resolve()
    if (root() / p).exists():
        return (root() / p).resolve()
    for e in reversed(load_state_list(state)):
        if str(e.get("run_id")) == arg and e.get("file"):
            return (root() / str(e["file"])).resolve()
    raise SystemExit(f"❌ 找不到文件或 run_id：{arg}")


def feedback_index(extra_paths=()) -> dict:
    """Best-effort {feedback_id: {title, customer, ..., expected}} from golden / feedback data files.

    Used for display only (titles, customer details, expected labels). Earlier files win.
    """
    cands = [*extra_paths, *labitems.GOLDEN_CANDIDATES, *labitems.FEEDBACK_CANDIDATES, SEED_POOL]
    idx: dict = {}
    seen = set()
    for c in cands:
        p = Path(c) if Path(c).is_absolute() else root() / c
        if p in seen or not p.exists():
            continue
        seen.add(p)
        for row in read_jsonl_safe(p):
            try:
                it = labitems.item_of(row)
                fid = labitems.fid_of(row)
            except Exception:  # noqa: BLE001
                continue
            if not fid:
                continue
            e = idx.setdefault(fid, {})
            for k in ("title", "customer", "customer_tier", "module", "industry", "feedback_type",
                      "contract_commitment", "channel", "submitted_at"):
                if e.get(k) in (None, "") and it.get(k) not in (None, ""):
                    e[k] = it.get(k)
            src = next((row[k] for k in ("expected", "labels", "expected_labels", "_meta") if isinstance(row.get(k), dict)),
                       row)
            if "expected" not in e and all(k in src for k in ("important", "urgent", "decision")):
                acc = src.get("acceptable_decisions") or row.get("acceptable_decisions")
                e["expected"] = {"important": src.get("important"), "urgent": src.get("urgent"),
                                 "decision": "/".join(map(str, acc)) if isinstance(acc, list) and acc else src.get("decision")}
    return idx


def pill(label, cls: str, title=None) -> str:
    t = f' title="{esc(title)}"' if title else ""
    return f'<span class="pill {cls}"{t}>{esc(label)}</span>'


def topbar(title: str, subtitle: str, meta: list) -> str:
    m = "<br>".join(f"{esc(k)} <b>{esc(v)}</b>" for k, v in meta)
    return (f'<div id="topbar"><div><div class="title">{esc(title)}</div><div class="subtitle">{esc(subtitle)}</div></div>'
            f'<div class="spacer"></div><div class="meta">{m}</div></div>')


def page(title: str, bar: str, body: str, footer: str, extra_css: str = "") -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return ("<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>{esc(title)}</title>\n<style>\n{load_css()}\n{EXTRA_CSS}{extra_css}</style>\n</head>\n"
            f"<body>\n{bar}\n<main class=\"wrap\">\n{body}\n</main>\n"
            f"<footer>{esc(footer)} · 生成时间 {esc(generated)}</footer>\n"
            f"<script>\n{FILTER_JS}\n</script>\n</body>\n</html>\n")


def delta_html(b, a, kind: str = "pct", higher_is_better: bool = True) -> str:
    if b is None or a is None:
        return '<span class="flat">—</span>'
    d = a - b
    if abs(d) < 1e-9:
        return '<span class="flat">＝ 持平</span>'
    arrow = "▲" if d > 0 else "▼"
    cls = "up" if (d > 0) == higher_is_better else "down"
    if kind == "pct":
        txt = f"{arrow} {abs(d) * 100:.1f} 个百分点"
    elif kind == "count":
        txt = f"{arrow} {abs(d):g}"
    else:
        txt = f"{arrow} {abs(d):.2f}"
    return f'<span class="{cls}">{esc(txt)}</span>'


def write_page(out: Path, content: str) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    return out.resolve()


def shown_path(p: Path) -> str:
    """Repo-relative when inside the repo (the 3x wrapper scripts grep `results/...html`), else absolute."""
    p = Path(p).resolve()
    try:
        return str(p.relative_to(root().resolve()))
    except ValueError:
        return str(p)


# ----------------------------------------------------------------------------------------------
# Compare page
# ----------------------------------------------------------------------------------------------

LABELS = (("label_important", "important", "是否重要"),
          ("label_urgent", "urgent", "是否紧急"),
          ("label_decision", "decision", "处理建议"))
ALIASES = {"important": ("important", "是否重要"), "urgent": ("urgent", "是否紧急"), "decision": ("decision", "处理建议")}
EVAL_ORDER = ["label_accuracy", "label_important", "label_urgent", "label_decision",
              "reject_quality", "reject_quality_llm", "Builtin.Correctness", "Builtin.Helpfulness"]
EVAL_DESC = {
    "label_accuracy": ("自定义 · 代码", "三项标签命中比例（0~1）；Pass = 三项全对，CriticalFail = 误拒了不可自动拒绝的需求"),
    "label_important": ("自定义 · 代码", "“是否重要”单项对错（0/1），平均分即准确率"),
    "label_urgent": ("自定义 · 代码", "“是否紧急”单项对错（0/1），平均分即准确率"),
    "label_decision": ("自定义 · 代码", "“处理建议”单项对错（0/1），平均分即准确率"),
    "reject_quality": ("自定义 · Lambda 内 LLM 评审", "拒绝理由与客户话术质量（0~1，≥0.7 为 Pass），只评“拒绝”条目"),
    "reject_quality_llm": ("自定义 · LLM-as-judge", "同一套中文评分标准的原生 LLM 评审（5 档），只评“拒绝”条目"),
    "Builtin.Correctness": ("内置", "回答正确性（对照期望输出）"),
    "Builtin.Helpfulness": ("内置", "回答有用性"),
}
PCT_EVALS = {"label_important", "label_urgent", "label_decision"}
BEFORE_COLOR = "#a3b0c2"   # de-emphasis gray for the baseline
AFTER_COLOR = "#2f6fdc"    # accent blue for the optimized version
TARGET_COLOR = "#d64545"
FOOTER = "数据为合成数据；分数来自 AgentCore Evaluations（按需评估）"
STATUS_VIEW = {
    "fixed": ("↑ 修复", "g"), "regressed": ("↓ 退步", "r"), "wrong": ("= 仍错误", "o"),
    "kept": ("= 保持正确", "n"), "unknown": ("— 无评分", "n"), "new": ("仅优化后有", "b"), "dropped": ("仅优化前有", "b"),
}


class EvalRun:
    """One results/eval-<run_id>.json, tolerant to missing keys."""

    def __init__(self, data, path):
        self.path = Path(path)
        self.data = as_dict(data)
        self.run = as_dict(self.data.get("run"))
        self.summary = as_dict(self.data.get("summary"))
        sessions = self.data.get("sessions")
        self.sessions = [s for s in sessions if isinstance(s, dict)] if isinstance(sessions, list) else []
        stem = self.path.stem
        self.run_id = str(self.run.get("run_id") or (stem[5:] if stem.startswith("eval-") else stem))
        self.version = str(self.run.get("version") or "").strip()
        self.vlabel = self.version.upper() if self.version else "?"
        self.evaluators = {str(k): str(v) for k, v in as_dict(self.data.get("evaluators")).items()}

    @classmethod
    def load(cls, path: Path) -> "EvalRun":
        return cls(read_json(path), path)

    def agent_errors(self) -> int:
        if self.sessions:
            return sum(1 for s in self.sessions if s.get("agent_error"))
        return int(num(self.summary.get("agent_errors")) or 0)

    def names(self) -> set:
        n = set(self.evaluators)
        n.update(as_dict(self.summary.get("metrics")))
        for s in self.sessions:
            n.update(as_dict(s.get("scores")))
        return {str(x) for x in n}


def entry_of(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if num(raw) is not None:
        return {"value": raw}
    return {}


def is_error(e: dict) -> bool:
    return bool(e.get("errorCode")) or (bool(e.get("errorMessage")) and num(e.get("value")) is None)


def score_value(entry) -> float | None:
    """Numeric score of one evaluator result; None for errors, not-applicable/Skipped and missing values."""
    e = entry_of(entry)
    if not e or is_error(e) or is_na(e.get("label")):
        return None
    return num(e.get("value"))


def metric(ev: EvalRun, name: str) -> dict:
    """Mean over sessions (numeric values only; errors and Skipped/不适用 excluded). Falls back to summary."""
    vals, errors, skipped, present = [], 0, 0, False
    for s in ev.sessions:
        sc = as_dict(s.get("scores"))
        if name not in sc:
            continue
        present = True
        e = entry_of(sc[name])
        if is_error(e):
            errors += 1
        elif is_na(e.get("label")) or num(e.get("value")) is None:
            skipped += 1
        else:
            vals.append(num(e.get("value")))
    if not present:
        m = as_dict(as_dict(ev.summary.get("metrics")).get(name))
        mean = num(m.get("mean"))
        return {"mean": mean, "n": int(num(m.get("n")) or 0) if mean is not None else 0, "errors": 0, "skipped": 0}
    return {"mean": (sum(vals) / len(vals)) if vals else None, "n": len(vals), "errors": errors, "skipped": skipped}


def label_metric(ev: EvalRun, ev_name: str, cn: str) -> dict:
    m = metric(ev, ev_name)
    if m["mean"] is None:
        v = num(as_dict(ev.summary.get("three_labels")).get(cn))
        if v is not None:
            m = {"mean": v, "n": int(num(ev.summary.get("sessions")) or 0), "errors": 0, "skipped": 0}
    return m


def first_present(d: dict, keys) -> object:
    for k in keys:
        if d.get(k) not in (None, ""):
            return d.get(k)
    return None


def case_of(s: dict) -> dict:
    out = parse_obj(s.get("agent_output"))
    sc = as_dict(s.get("scores"))
    labels, known, correct = {}, 0, 0
    for ev_name, key, _cn in LABELS:
        e = entry_of(sc.get(ev_name))
        v = score_value(e)
        ok = None if v is None else (True if v >= 0.999 else (False if v <= 0.001 else None))
        labels[key] = (first_present(out, ALIASES[key]), ok, e)
        if ok is not None:
            known += 1
            correct += int(ok)
    la = entry_of(sc.get("label_accuracy"))
    lav = score_value(la)
    la_label = str(la.get("label") or "")
    agent_error = s.get("agent_error")
    if known == 3:
        n = correct
    elif lav is not None:
        n = int(round(lav * 3))
    elif agent_error or la_label == "FormatError":
        n = 0
    else:
        n = None
    return {"fid": str(s.get("feedback_id") or s.get("session_id") or ""), "out": out, "labels": labels, "n": n,
            "critical": la_label == "CriticalFail", "format_error": la_label == "FormatError",
            "agent_error": agent_error, "eval_error": s.get("error"),
            "reject": entry_of(sc.get("reject_quality")), "has_reject": "reject_quality" in sc}


def all_correct(ev: EvalRun) -> dict:
    """Share of sessions with all three labels right (label_accuracy == 1), plus CriticalFail count."""
    n = ok = 0
    crit = sum(1 for s in ev.sessions
               if str(entry_of(as_dict(s.get("scores")).get("label_accuracy")).get("label") or "") == "CriticalFail")
    for s in ev.sessions:
        v = score_value(entry_of(as_dict(s.get("scores")).get("label_accuracy")))
        if v is not None:
            n += 1
            ok += int(v >= 0.999)
    if n == 0:  # no label_accuracy evaluator: derive from the three single-label evaluators
        for s in ev.sessions:
            c = case_of(s)
            if not c["agent_error"] and all(c["labels"][k][1] is not None for k in ALIASES):
                n += 1
                ok += int(c["n"] == 3)
    return {"rate": ok / n if n else None, "n": n, "ok": ok, "critical": crit}


def status_of(cb, ca) -> str:
    if cb is None:
        return "new"
    if ca is None:
        return "dropped"
    nb, na = cb["n"], ca["n"]
    if nb is None or na is None:
        return "unknown"
    if na == 3:
        return "kept" if nb == 3 else "fixed"
    return "regressed" if na < nb else "wrong"


def _bar_path(x: float, y0: float, w: float, h: float, r: float = 4.0) -> str:
    """Column growing up from baseline y0: rounded data-end, square at the baseline."""
    r = max(0.0, min(r, h, w / 2))
    top = y0 - h
    return (f"M{x:.1f},{y0:.1f} L{x:.1f},{top + r:.1f} A{r:.1f},{r:.1f} 0 0 1 {x + r:.1f},{top:.1f} "
            f"L{x + w - r:.1f},{top:.1f} A{r:.1f},{r:.1f} 0 0 1 {x + w:.1f},{top + r:.1f} L{x + w:.1f},{y0:.1f} Z")


def bar_chart(groups: list, target: float, vb: str, va: str) -> str:
    """Grouped columns (before/after) per label on one 0-100% axis with a dashed target line."""
    W, H, ml, mr, mt, mb = 680, 300, 48, 92, 18, 58
    pw, ph = W - ml - mr, H - mt - mb
    y0 = mt + ph

    def y(v):
        return mt + ph * (1 - max(0.0, min(1.0, v)))

    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="三项标签准确率：优化前与优化后对比" '
             f'xmlns="http://www.w3.org/2000/svg" font-size="12">']
    for t in (0, 0.2, 0.4, 0.6, 0.8, 1.0):
        yy = y(t)
        stroke = "#c9d1dc" if t == 0 else "#e9edf2"
        parts.append(f'<line x1="{ml}" x2="{ml + pw}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="{stroke}" stroke-width="1"/>')
        parts.append(f'<text x="{ml - 8}" y="{yy + 4:.1f}" text-anchor="end" fill="#6b7785" font-size="11">{t * 100:.0f}%</text>')
    band = pw / max(1, len(groups))
    bw, gap = 24, 8
    for i, (cn, mbf, maf) in enumerate(groups):
        cx = ml + band * (i + 0.5)
        for x, m, color, tag, ink in ((cx - gap / 2 - bw, mbf, BEFORE_COLOR, vb, "#6b7785"),
                                      (cx + gap / 2, maf, AFTER_COLOR, va, "#1f2933")):
            v = m["mean"]
            tip = f"{tag} · {cn}：{pct(v)}（{m['n']} 条）"
            parts.append(f'<g><title>{esc(tip)}</title>'
                         f'<rect x="{x - 3:.1f}" y="{mt}" width="{bw + 6}" height="{ph}" fill="transparent"/>')
            if v is not None and v > 0:
                parts.append(f'<path d="{_bar_path(x, y0, bw, ph * min(1.0, v))}" fill="{color}"/>')
            label = "—" if v is None else f"{v * 100:.0f}%"
            ly = (y(v) if v is not None else y0) - 6
            parts.append(f'<text x="{x + bw / 2:.1f}" y="{ly:.1f}" text-anchor="middle" fill="{ink}" '
                         f'font-size="11" font-weight="600">{esc(label)}</text></g>')
        parts.append(f'<text x="{cx:.1f}" y="{y0 + 20:.1f}" text-anchor="middle" fill="#1f2933" font-size="13">{esc(cn)}</text>')
        b, a = mbf["mean"], maf["mean"]
        if b is not None and a is not None:
            d = (a - b) * 100
            fill = "#1f9d55" if d > 0 else ("#d64545" if d < 0 else "#6b7785")
            sign = "▲" if d > 0 else ("▼" if d < 0 else "＝")
            parts.append(f'<text x="{cx:.1f}" y="{y0 + 38:.1f}" text-anchor="middle" fill="{fill}" font-size="11">'
                         f'{esc(f"{sign} {abs(d):.1f} 个百分点")}</text>')
    ty = y(target)
    parts.append(f'<line x1="{ml}" x2="{ml + pw}" y1="{ty:.1f}" y2="{ty:.1f}" stroke="{TARGET_COLOR}" '
                 f'stroke-width="1.5" stroke-dasharray="6 4"/>')
    parts.append(f'<text x="{ml + pw + 8}" y="{ty + 4:.1f}" fill="#46525e" font-size="12">目标 {esc(pct_short(target))}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _kpi_label(cn: str, mb: dict, ma: dict, target: float) -> str:
    a = ma["mean"]
    t = pct_short(target)
    if a is None:
        badge = pill("无数据", "n")
    elif a >= target:
        badge = pill(f"✓ 达标（≥{t}）", "g")
    else:
        badge = pill(f"✗ 未达标（<{t}）", "r")
    return (f'<div class="kpi"><div class="k">{esc(cn)} 准确率 {badge}</div>'
            f'<div class="v"><span class="from">{esc(pct(mb["mean"]))}</span><span class="arrow">→</span>{esc(pct(a))}</div>'
            f'<div class="d">{delta_html(mb["mean"], a, "pct")} · 目标 ≥{esc(t)} · {mb["n"]} → {ma["n"]} 条</div></div>')


def _kpi_plain(title: str, vb_txt: str, va_txt: str, delta: str, sub: str) -> str:
    return (f'<div class="kpi"><div class="k">{esc(title)}</div>'
            f'<div class="v"><span class="from">{esc(vb_txt)}</span><span class="arrow">→</span>{esc(va_txt)}</div>'
            f'<div class="d">{delta} · {esc(sub)}</div></div>')


def _run_box(ev: EvalRun, role: str) -> str:
    r = ev.run
    letter, plabel = path_of(r.get("mode"))
    count = r.get("count") if r.get("count") not in (None, "") else (len(ev.sessions) or "—")
    rows = [
        ("run_id", f'<span class="mono">{esc(ev.run_id)}</span>'),
        ("路线", esc(plabel)),
        ("模型", f'<span class="mono">{esc(r.get("model") or "—")}</span>'),
        ("数据集", esc(f"{r.get('source') or '—'} · {count} 条")),
        ("运行时间", esc(fmt_time(r.get("created")))),
        ("评估时间", esc(fmt_time(ev.data.get("evaluated_at")))),
        ("区域", esc(ev.data.get("region") or "—")),
        ("Agent 错误", esc(f"{ev.agent_errors()} 条")),
    ]
    trs = "".join(f"<tr><td>{esc(k)}</td><td>{v}</td></tr>" for k, v in rows)
    return (f'<div class="runbox"><h3>{esc(role)} · {esc(ev.vlabel)} {pill("路线 " + letter, "p" if letter == "A" else "o")}</h3>'
            f"<table>{trs}</table></div>")


def _label_cell(cb, ca, key: str, cn: str, vb: str, va: str, expected) -> str:
    def one(c, tag):
        if c is None:
            return pill("无", "n", f"{tag}：这次评估里没有这条需求")
        if c["agent_error"] and not c["out"]:
            return pill("错误", "r", f"{tag} Agent 出错：{clip(c['agent_error'], 300)}")
        val, ok, e = c["labels"][key]
        icon = {True: "✓ ", False: "✗ ", None: ""}[ok]
        cls = {True: "g", False: "r", None: "n"}[ok]
        tips = [f"{tag} · {cn}：{text(val) or '—'}"]
        if ok is None:
            tips.append("（无单项评估结果）" if not is_error(e) else f"（评估出错：{clip(e.get('errorCode') or e.get('errorMessage'), 120)}）")
        out = c["out"]
        if key == "decision":
            if out.get("level"):
                tips.append(f"优先级：{text(out.get('level'))}")
            if text(val) == "拒绝":
                if out.get("reject_reason"):
                    tips.append("拒绝理由：" + clip(out.get("reject_reason"), 200))
                if out.get("customer_reply"):
                    tips.append("客户话术：" + clip(out.get("customer_reply"), 260))
            elif out.get("reasoning"):
                tips.append("理由：" + clip(out.get("reasoning"), 200))
        elif out.get("reasoning"):
            tips.append("理由：" + clip(out.get("reasoning"), 160))
        return pill(icon + (text(val) or "—"), cls, "\n".join(tips))

    exp = f'<div class="exp">期望 {esc(expected)}</div>' if expected else ""
    return f'<td>{one(cb, vb)}<span class="arr">→</span>{one(ca, va)}{exp}</td>'


def _reject_cell(cb, ca, vb: str, va: str) -> str:
    """reject_quality before → after: green Pass (>= 0.7), red Fail, gray 不适用 / 出错 / 无."""
    def one(c, tag):
        if c is None or not c["has_reject"]:
            return pill("—", "n", f"{tag}：无话术评估结果")
        e = c["reject"]
        if is_error(e):
            return pill("出错", "n", f"{tag} 评估出错：{clip(e.get('errorCode') or '', 60)} {clip(e.get('errorMessage'), 200)}")
        if is_na(e.get("label")) or num(e.get("value")) is None:
            return pill("不适用", "n", f"{tag}：处理建议不是“拒绝”，话术评估不适用")
        v = num(e.get("value"))
        lab = str(e.get("label") or "")
        ok = lab == "Pass" if lab in ("Pass", "Fail") else v >= 0.7
        tip = f"{tag} 话术质量 {v:.2f}（{lab or '—'}）"
        if e.get("explanation"):
            tip += "\n" + clip(e.get("explanation"), 260)
        return pill(f"{v:.2f}", "g" if ok else "r", tip)

    return f'<td class="num">{one(cb, vb)}<span class="arr">→</span>{one(ca, va)}</td>'


def _cases_section(b: EvalRun, a: EvalRun, fb: dict) -> tuple:
    cases_b = {}
    for i, s in enumerate(b.sessions):
        c = case_of(s)
        cases_b[c["fid"] or f"#{i + 1}"] = c
    cases_a = {}
    for i, s in enumerate(a.sessions):
        c = case_of(s)
        cases_a[c["fid"] or f"#{i + 1}"] = c
    order = list(cases_b) + [k for k in cases_a if k not in cases_b]
    counts = {k: 0 for k in STATUS_VIEW}
    rows = []
    n_crit = 0
    for fid in order:
        cb, ca = cases_b.get(fid), cases_a.get(fid)
        st = status_of(cb, ca)
        counts[st] += 1
        info = fb.get(fid, {})
        exp = as_dict(info.get("expected"))
        title = f'<div class="small muted">{esc(clip(info.get("title"), 40))}</div>' if info.get("title") else ""
        cells = "".join(_label_cell(cb, ca, key, cn, b.vlabel, a.vlabel, exp.get(key)) for _e, key, cn in LABELS)

        def nfmt(c):
            if c is None or c["n"] is None:
                return "—"
            return f"{c['n']}/3"

        crit = ""
        tags = [st]
        for c, tag in ((cb, b.vlabel), (ca, a.vlabel)):
            if c and c["critical"]:
                crit += "<br>" + pill(f"⚠ {tag} 误拒", "r", "CriticalFail：拒绝了不可自动拒绝的需求（数据丢失/安全/合规/合同承诺/线上缺陷）")
                tags.append("critical")
            if c and c["format_error"]:
                crit += "<br>" + pill(f"{tag} 格式错误", "r", "FormatError：有回答但解析不出分诊 JSON")
            if c and c["eval_error"]:
                crit += "<br>" + pill(f"{tag} 评估出错", "n", clip(c["eval_error"], 200))
        n_crit += int("critical" in tags)
        label, cls = STATUS_VIEW[st]
        if st == "wrong" and cb and ca and (ca["n"] or 0) > (cb["n"] or 0):
            label = "↗ 仍错误（部分改善）"
        rows.append(f'<tr data-tags="{esc(" ".join(tags))}"><td><span class="mono">{esc(fid)}</span>{title}</td>{cells}'
                    f'<td class="num">{esc(nfmt(cb))} → {esc(nfmt(ca))}{crit}</td>{_reject_cell(cb, ca, b.vlabel, a.vlabel)}'
                    f'<td>{pill(label, cls)}</td></tr>')
    total = len(order)
    btn = []
    for f, lab, n in (("all", "全部", total), ("fixed", "修复", counts["fixed"]), ("regressed", "退步", counts["regressed"]),
                      ("wrong", "仍错误", counts["wrong"]), ("critical", "含误拒", n_crit)):
        if f == "critical" and not n:
            continue
        active = " active" if f == "all" else ""
        btn.append(f'<button type="button" class="fbtn{active}" data-target="#cases" data-f="{f}" '
                   f'data-label="{esc(lab)}">{esc(f"{lab}（{n}）")}</button>')
    summary = (f"修复 {counts['fixed']} 条 · 退步 {counts['regressed']} 条 · 仍错误 {counts['wrong']} 条 · "
               f"保持正确 {counts['kept']} 条")
    if counts["unknown"]:
        summary += f" · 无评分 {counts['unknown']} 条"
    if counts["new"] or counts["dropped"]:
        summary += f" · 仅优化前 {counts['dropped']} 条 / 仅优化后 {counts['new']} 条"
    html_ = (
        '<div class="card"><h2>逐条对比（按需求编号对齐）</h2>'
        f'<p class="small muted">每格显示 {esc(b.vlabel)} → {esc(a.vlabel)} 的 Agent 输出：'
        '<span class="pill g">✓ 绿色</span>= 该项单项评估器判对，<span class="pill r">✗ 红色</span>= 判错，'
        '<span class="pill n">灰色</span>= 无评估结果 / 不适用。鼠标悬停查看理由；“拒绝”条目悬停可看客户话术。'
        '“修复”= 优化后三项全对而优化前没有；“退步”= 优化后答对的项数减少；“仍错误”= 优化后仍未三项全对；'
        '<span class="pill r">⚠ 误拒</span>= CriticalFail（拒绝了不可自动拒绝的需求）。</p>'
        f'<p><b>{esc(summary)}</b></p>'
        f'<div class="filters">{"".join(btn)}<span class="small muted" data-status-for="#cases">全部：{total} 条</span></div>'
        '<table id="cases" class="cases"><thead><tr><th>需求</th><th>是否重要</th><th>是否紧急</th><th>处理建议</th>'
        '<th>三项命中</th><th>拒绝话术</th><th>变化</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>"
    )
    return html_, counts


def build_compare(b: EvalRun, a: EvalRun, target: float) -> str:
    vb, va = b.vlabel, a.vlabel
    if vb == va:  # fallback pairing of two runs of the same version
        vb, va = f"{vb}（前）", f"{va}（后）"
        b.vlabel, a.vlabel = vb, va
    t = pct_short(target)
    fb = feedback_index()
    groups = [(cn, label_metric(b, ev_name, cn), label_metric(a, ev_name, cn)) for ev_name, _k, cn in LABELS]

    failing = [cn for cn, _mb, ma in groups if ma["mean"] is None or ma["mean"] < target]
    if not failing:
        verdict = f'<div class="tip">✅ {esc(va)} 三项标签准确率均达到目标（各 ≥{esc(t)}）。</div>'
    else:
        verdict = (f'<div class="warn">⚠️ {esc(va)} 仍有 {len(failing)} 项未达到目标（各 ≥{esc(t)}）：'
                   f'{esc("、".join(failing))}。</div>')
    notes = []
    if (b.run.get("model") or "") != (a.run.get("model") or ""):
        notes.append("两次运行使用的模型不同，对比结论同时包含模型变化的影响。")
    fids_b = {str(s.get("feedback_id")) for s in b.sessions}
    fids_a = {str(s.get("feedback_id")) for s in a.sessions}
    if fids_b != fids_a:
        notes.append(f"两次评估的需求集合不完全一致（仅优化前 {len(fids_b - fids_a)} 条，仅优化后 {len(fids_a - fids_b)} 条）。")
    notes_html = "".join(f'<div class="note">{esc(n)}</div>' for n in notes)

    ac_b, ac_a = all_correct(b), all_correct(a)
    la_b, la_a = metric(b, "label_accuracy"), metric(a, "label_accuracy")
    rq_b, rq_a = metric(b, "reject_quality"), metric(a, "reject_quality")
    kpis = "".join(_kpi_label(cn, mb, ma, target) for cn, mb, ma in groups)
    kpis += _kpi_plain("三项全对率", pct(ac_b["rate"]), pct(ac_a["rate"]), delta_html(ac_b["rate"], ac_a["rate"], "pct"),
                       f"{ac_b['ok']}/{ac_b['n']} → {ac_a['ok']}/{ac_a['n']} 条全对 · "
                       f"label_accuracy 均值 {score_fmt(la_b['mean'])} → {score_fmt(la_a['mean'])}")
    kpis += _kpi_plain("⚠ 误拒（CriticalFail）", f"{ac_b['critical']} 条", f"{ac_a['critical']} 条",
                       delta_html(ac_b["critical"], ac_a["critical"], "count", higher_is_better=False),
                       "拒绝了不可自动拒绝的需求，越少越好")
    kpis += _kpi_plain("拒绝话术质量", score_fmt(rq_b["mean"]), score_fmt(rq_a["mean"]),
                       delta_html(rq_b["mean"], rq_a["mean"], "score"),
                       f"reject_quality 均值，只计“拒绝”条目：{rq_b['n']} → {rq_a['n']} 条")

    chart = ('<div class="card"><h2>三项标签准确率：优化前 vs 优化后</h2>'
             f'<div class="legend"><span><span class="sw" style="background:{BEFORE_COLOR}"></span>{esc(vb)} · 优化前</span>'
             f'<span><span class="sw" style="background:{AFTER_COLOR}"></span>{esc(va)} · 优化后</span>'
             f'<span><span class="dash"></span>目标 ≥{esc(t)}</span></div>'
             f'<div class="chart">{bar_chart(groups, target, vb, va)}</div>'
             '<p class="small muted">准确率 = 单项评估器（label_important / label_urgent / label_decision）的平均分；'
             'Agent 出错、评估出错的条目不计入分母。悬停柱子可查看条数。</p></div>')

    names = [n for n in EVAL_ORDER if n in (b.names() | a.names())]
    names += sorted((b.names() | a.names()) - set(names))
    trs = []
    for name in names:
        mb, ma = metric(b, name), metric(a, name)
        kind = "pct" if name in PCT_EVALS else "score"
        fmt = pct if kind == "pct" else score_fmt
        typ, desc = EVAL_DESC.get(name, ("自定义", ""))
        eid = a.evaluators.get(name) or b.evaluators.get(name) or ""
        eid_html = f'<div class="eid mono">{esc(eid)}</div>' if eid and eid != name else ""
        errs = f"{mb['errors']} → {ma['errors']}" if (mb["errors"] or ma["errors"]) else "—"
        trs.append(f'<tr><td><b class="mono">{esc(name)}</b>{eid_html}</td><td class="small">{esc(typ)}</td>'
                   f'<td class="small">{esc(desc)}</td>'
                   f'<td class="num">{esc(fmt(mb["mean"]))} <span class="muted small">（{mb["n"]}）</span></td>'
                   f'<td class="num">{esc(fmt(ma["mean"]))} <span class="muted small">（{ma["n"]}）</span></td>'
                   f'<td class="num">{delta_html(mb["mean"], ma["mean"], kind)}</td><td class="num small">{esc(errs)}</td></tr>')
    table = ('<div class="card"><h2>全部评估器平均分</h2>'
             '<p class="small muted">括号内为参与平均的条数；“不适用 / Skipped”的条目（例如非拒绝条目的话术评估）和评估出错的条目不计入。</p>'
             f'<table><thead><tr><th>评估器</th><th>类型</th><th>说明</th><th>{esc(vb)} 优化前</th><th>{esc(va)} 优化后</th>'
             '<th>变化</th><th>评估出错</th></tr></thead><tbody>' + "".join(trs) + "</tbody></table></div>")

    cases_html, _counts = _cases_section(b, a, fb)

    region = a.data.get("region") or b.data.get("region") or "—"
    bar = topbar(f"场景 12 · 评估对比 {vb} vs {va}", "需求反馈分诊 Agent · AgentCore Evaluations 前后对比",
                 [("优化前", b.run_id), ("优化后", a.run_id), ("区域", region)])
    head = (f'<div class="card"><h2>{esc(vb)}（基线）→ {esc(va)}（优化后）：三项标签目标各 ≥{esc(t)}</h2>'
            f'{verdict}{notes_html}<div class="kpis">{kpis}</div></div>'
            f'<div class="card"><h2>两次运行</h2><div class="runs">{_run_box(b, "优化前")}{_run_box(a, "优化后")}</div></div>')
    return page(f"评估对比 {vb} vs {va} · 场景 12", bar, head + chart + table + cases_html, FOOTER)


def pick_default() -> tuple:
    evals = [e for e in load_state_list("evals") if e.get("file")]
    if len(evals) < 2:
        raise SystemExit("❌ .lab-state/evals.json 里不足两次评估记录：先对 V0 和 V1 各运行一次 ./31-run-eval.sh，"
                         "或用 --before / --after 指定两个 eval JSON")
    before = next((e for e in evals if str(e.get("version") or "").lower() == "v0"), None)
    after = None
    if before is not None:
        bv = str(before.get("version") or "").lower()
        after = next((e for e in reversed(evals) if str(e.get("version") or "").lower() != bv), None)
    if before is None or after is None:
        before, after = evals[-2], evals[-1]
    return root() / str(before["file"]), root() / str(after["file"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m labkit.compare", description="生成 V0 vs V1 评估对比页（自包含静态 HTML）")
    ap.add_argument("--before", help="优化前的 eval JSON 路径或 run_id（默认：evals.json 中最早的 v0）")
    ap.add_argument("--after", help="优化后的 eval JSON 路径或 run_id（默认：evals.json 中最新的非 v0 版本）")
    ap.add_argument("--out", help="输出 HTML（默认：results/compare-<before>-vs-<after>.html）")
    a = ap.parse_args(argv)
    if a.before and a.after:
        before_p, after_p = resolve_input(a.before, "evals"), resolve_input(a.after, "evals")
    else:
        d_before, d_after = pick_default()
        before_p = resolve_input(a.before, "evals") if a.before else d_before
        after_p = resolve_input(a.after, "evals") if a.after else d_after
    for p in (before_p, after_p):
        if not Path(p).exists():
            raise SystemExit(f"❌ 找不到评估结果文件：{p}")
    b, af = EvalRun.load(before_p), EvalRun.load(after_p)
    target = target_accuracy()
    print(f"▶ 对比 {b.vlabel} {b.run_id} → {af.vlabel} {af.run_id}（目标各 ≥{pct_short(target)}）", file=sys.stderr)
    out = Path(a.out) if a.out else root() / "results" / f"compare-{safe_name(b.run_id)}-vs-{safe_name(af.run_id)}.html"
    path = write_page(out, build_compare(b, af, target))
    print("✅ 对比页已生成（本地文件，可直接用浏览器打开）：", file=sys.stderr)
    print(shown_path(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
