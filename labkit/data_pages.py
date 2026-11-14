"""Handbook 2.1 (optional) / extension "regenerate mock data": publish the CURRENT data files as self-contained pages in the private lab bucket.

  ./tools/publish-data-pages.sh                 # capability catalog + mock feedback + golden set: render, upload, print links
  ./tools/publish-data-pages.sh --no-upload     # only write reports/data-pages/*.html (no AWS call at all)
  ./tools/publish-data-pages.sh --only golden   # one page only (catalog / mock / golden; repeat or comma-separate)
  python -m labkit.data_pages --catalog X.json --feedback Y.jsonl --golden Z.jsonl --out-dir DIR --no-upload

Why: the handbook has no page for the current data (the old GitHub Pages catalog.html / mock-data.html show the
repo's original sample, and there is no golden set page). Once a group changes the data on its EC2 (golden items
added in 2.4, regenerated mock data, prompt 04 catalog) nothing else shows it, and the EC2 has no inbound ports, so its files cannot be
opened in a browser either. This module renders the files as they are NOW, with the same renderers that build
the handbook pages (data/tools/catalog_views.py, data/tools/mock_views.py; the golden set page is rendered
here in the same page shape), turns each page into a self-contained file (style.css inlined, no external
script, stylesheet, image, font or link, a CSP that forbids any network load) and uploads it with
labkit.publish to reports/<file name> in the lab's private bucket (public access blocked; the page opens from
the S3 console, Object -> Open, with the attendee's own console login). Same file names every run, so a
re-run overwrites the same objects and the console links stay the same.

The render path is standard library only (runs with the system python3); boto3 is imported only to upload.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from labkit import config as labcfg
from labkit.compare import load_css, topbar

_TOOLS = str(Path(labcfg.ROOT) / "data" / "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
import catalog_views as CV  # noqa: E402
import common as C  # noqa: E402
import golden_lib as G  # noqa: E402
import mock_views as MV  # noqa: E402

BEIJING = timezone(timedelta(hours=8))  # attendees' wall clock; the EC2 itself runs in UTC
OUT_DIR = Path(labcfg.ROOT) / "reports" / "data-pages"
# kind -> (file name = S3 key under reports/, label)
PAGES = {"catalog": ("data-catalog.html", "能力目录"),
         "mock": ("data-mock-feedback.html", "mock 需求"),
         "golden": ("data-golden-set.html", "黄金集")}
# script-src is filled per page with the sha256 of each of OUR inline scripts (see _csp), so even injected
# inline script would not run; style-src needs 'unsafe-inline' for the templates' style="" attributes.
CSP = ("default-src 'none'; style-src 'unsafe-inline'; script-src {scripts}; img-src data:; "
       "base-uri 'none'; form-action 'none'")
_CSP_META = '<meta http-equiv="Content-Security-Policy" content="__ADLC_CSP__">'
_SCRIPT_RX = re.compile(r"<script>(.*?)</script>", re.S)
STANDALONE_CSS = """
.layout { max-width: 1680px; }
.pill.n { color: var(--muted); background: #f3f5f8; border-color: #d5dbe3; }
.small { font-size: 12px; }
ul.pts { margin: 0; padding-left: 16px; }
ul.pts li { margin: 0 0 2px; }
#snapshot .warn { margin: 0; }
td.caps code { white-space: nowrap; }
#dist th { white-space: nowrap; }
"""
E = lambda v: html.escape("" if v is None else str(v), quote=True)  # noqa: E731


# ---------------------------------------------------------------- source files
def shown(p) -> str:
    """Repo-relative path when inside the repo, else absolute (for messages and the page banner)."""
    p = Path(p).resolve()
    try:
        return str(p.relative_to(Path(labcfg.ROOT).resolve()))
    except ValueError:
        return str(p)


def source_info(path) -> dict:
    p = Path(path)
    raw = p.read_bytes()
    return {"path": shown(p), "sha": hashlib.sha256(raw).hexdigest(),
            "mtime": datetime.fromtimestamp(p.stat().st_mtime, BEIJING).strftime("%Y-%m-%d %H:%M")}


def golden_freeze_note(golden_path) -> str:
    """'' when MANIFEST.json next to the golden file has the same sha256, else a warning (HTML)."""
    m = Path(golden_path).parent / "MANIFEST.json"
    sha = source_info(golden_path)["sha"]
    try:
        man = json.loads(m.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ("<br>⚠️ 没找到冻结记录（<code>MANIFEST.json</code>）：确认无误后运行 "
                "<code>python3 data/tools/golden_tool.py freeze</code>（手册 2.4）。")
    if man.get("sha256") == sha:
        return f"<br>✅ 已冻结（{E(man.get('frozen_at'))}），评估器用的就是这一版（重新注册过评估器的前提下）。"
    return ("<br>⚠️ 这一版<b>还没有冻结</b>（和 <code>MANIFEST.json</code> 记录的不一致）：确认无误后运行 "
            "<code>python3 data/tools/golden_tool.py freeze</code>（手册 2.4），再运行 <code>./40-create-evaluators.sh</code>，评估器才会用上。")


# ---------------------------------------------------------------- self-contained check
_HREF_RX = re.compile(r"\bhref\s*=\s*(['\"]?)([^'\" >]*)", re.I)  # for _anchor (our own template markup only)
_FORBIDDEN_TAGS = {"link", "img", "iframe", "frame", "frameset", "embed", "object", "applet", "base", "form", "audio",
                   "video", "source", "track", "picture", "svg", "math", "use", "image", "portal"}
_LOADING_ATTRS = {"src", "srcset", "action", "formaction", "poster", "background", "data", "ping", "xlink:href",
                  "manifest", "codebase", "archive", "lowsrc", "dynsrc"}
_CSS_URL_RX = re.compile(r"url\(\s*(?!['\"]?\s*data:)", re.I)


class _Scan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.bad: List[str] = []
        self._style = False

    def _tag(self, tag, attrs):
        if tag in _FORBIDDEN_TAGS:
            self.bad.append(f"<{tag}>")
        for k, v in attrs:
            v = v or ""
            if k in _LOADING_ATTRS:
                self.bad.append(f"<{tag} {k}=>")
            elif k == "href" and not v.startswith("#"):
                self.bad.append(f'<{tag} href="{v}">')
            elif k == "style" and _CSS_URL_RX.search(v):
                self.bad.append(f"<{tag} style=url()>")
            elif k == "http-equiv" and v.strip().lower() == "refresh":
                self.bad.append("<meta refresh>")
            elif k.startswith("on"):  # our pages bind no inline handlers; one here means injected markup
                self.bad.append(f"<{tag} {k}=>")

    def handle_starttag(self, tag, attrs):
        self._tag(tag, attrs)
        self._style = tag == "style"

    def handle_startendtag(self, tag, attrs):
        self._tag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "style":
            self._style = False

    def handle_data(self, data):
        if self._style:
            if "@import" in data.lower():
                self.bad.append("@import")
            if _CSS_URL_RX.search(data):
                self.bad.append("css url()")


def external_refs(page: str) -> List[str]:
    """Markup in `page` that would load or link anything outside the file (empty list = self-contained).

    Parsed the way a browser reads it (tags, attribute names, <style> content), never by searching the text:
    data is HTML-escaped, so a feedback item that merely mentions "url(" or "src=" must not stop the page."""
    scan = _Scan()
    scan.feed(page)
    scan.close()
    return scan.bad


# ---------------------------------------------------------------- handbook page -> standalone page
_A_RX = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.S)
_REPO_ATTR_RX = re.compile(r'\bdata-(?:repo|download)="([^"]*)"')


def _anchor(m) -> str:
    """Keep in-page links; a repo / download / handbook link becomes plain text (it would show GitHub's original
    sample, not this EC2's file, and site.js that resolves it is not loaded here)."""
    attrs, inner = m.group(1), m.group(2)
    href = _HREF_RX.search(attrs)
    if href and href.group(2).startswith("#"):
        return m.group(0)
    if inner.strip():
        return f"<span>{inner}</span>"
    target = _REPO_ATTR_RX.search(attrs)  # already-escaped attribute text is valid escaped element text
    return f"<code>{target.group(1)}</code>" if target else ""


ORIG_NOTE = "实验包里自带的是<b>原始样例</b>；这一页按 EC2 上的当前文件生成，包含你们的修改。"
GOLDEN_ORIG_NOTE = ("手册里没有黄金集网页版；实验包里自带的 <code>golden_set.jsonl</code> / <code>.csv</code> 是"
                    "<b>原始样例</b>，这一页按 EC2 上的当前文件生成，包含你们的修改。")
NOT_SAMPLE_NOTE = ("<br>⚠️ 来源不是仓库自带的示例数据文件：页面可能含真实客户信息，只在本账号内查看，"
                   "不要下载后外传，也不要把对象改成公开。")


def _csp(page: str) -> str:
    hashes = sorted({"'sha256-" + base64.b64encode(hashlib.sha256(js.encode("utf-8")).digest()).decode() + "'"
                     for js in _SCRIPT_RX.findall(page)})
    return CSP.format(scripts=" ".join(hashes) or "'none'")


def _not_sample(s: str) -> str:
    """Drop the templates' 'all synthetic' claims: they are false for a customer's file (--feedback etc.)."""
    s = re.sub(r"（(\d+) 条，合成）", r"（\1 条）", s)
    s = re.sub(r"\s*·\s*全部为合成数据(（[^）]*“（示例）”）)?", "", s)
    return s.replace("所有数据均为合成数据", "数据来自 EC2 上的指定文件")


def standalone(page: str, title: str, label: str, src: dict, count: str, extra_note: str = "",
               now: Optional[datetime] = None, orig_note: str = ORIG_NOTE, sample: bool = True) -> str:
    """A page in the handbook's shape (data/tools/*_views.py) -> one self-contained file with a 'current version'
    banner. sample=False (not the repo's own data file) drops the 'synthetic data' claims and warns instead.
    Raises ValueError if anything external survives, so a non-self-contained page is never published."""
    now = (now or datetime.now(timezone.utc)).astimezone(BEIJING)
    stamp = now.strftime("%Y-%m-%d %H:%M")
    s = re.sub(r"<!--.*?-->\n?", "", page, flags=re.S)
    s = re.sub(r"<link\b[^>]*>\n?", "", s)
    s = re.sub(r"<script\b[^>]*\bsrc=[^>]*>\s*</script>\n?", "", s)
    s = re.sub(r"<title>.*?</title>", f"<title>{E(title)}</title>", s, count=1, flags=re.S)
    s = s.replace("</head>", f"<style>\n{load_css()}\n{STANDALONE_CSS}</style>\n</head>", 1)
    s = s.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n' + _CSP_META, 1)
    s = re.sub(r'<p\b[^>]*\bdata-handbook-only\b[^>]*>.*?</p>\n?', "", s, flags=re.S)
    s = s.replace(' <span class="applies common">A/B 通用</span>', "")   # old GitHub Pages nav pill, not for these pages
    s = _A_RX.sub(_anchor, s)
    s = s.replace('<nav id="toc"></nav>\n', "").replace('<nav id="toc"></nav>', "")
    bar = topbar(f"{label}（当前版本）· AgentDLC 动手实验", "按 EC2 上的数据文件生成 · 只在你的私有 S3 桶里，不公开",
                 [("生成于", f"{stamp} 北京时间"), ("sha256", src["sha"][:12])])
    s = s.replace('<div id="topbar"></div>', bar, 1)
    banner = (f'<section class="card" id="snapshot"><div class="warn"><b>这是 EC2 上数据文件的当前版本</b>'
              f"（{E(label)}，{E(count)}，生成于 {E(stamp)} 北京时间）。{orig_note}"
              f"<br>来源：<code>{E(src['path'])}</code> · sha256 <code>{E(src['sha'][:12])}</code>"
              f" · 文件修改于 {E(src['mtime'])}{'' if sample else NOT_SAMPLE_NOTE}{extra_note}<br>数据再改后（补黄金样本并冻结、重新生成模拟数据、提示词 04），在 EC2 上重新运行 "
              f"<code>./tools/publish-data-pages.sh</code>：同名对象被覆盖，在控制台重新点「打开」就是新版本。</div></section>\n")
    s = s.replace("<main>\n", "<main>\n" + banner, 1)
    s = s.replace("</footer>", f" · 生成于 {E(stamp)} 北京时间</footer>", 1)
    if not sample:
        s = _not_sample(s)
    s = s.replace("__ADLC_CSP__", _csp(s), 1)
    bad = external_refs(s)
    if bad or "<style>" not in s or "script-src" not in s or "id=\"snapshot\"" not in s:
        raise ValueError(f"{label}：页面不是自包含的，未发布：{sorted(set(bad)) or '缺少内联样式 / CSP / 版本说明'}")
    return s


def failure_page(label: str, src_path, err: str, now: Optional[datetime] = None) -> str:
    """Written (and uploaded) in place of a page that could not be built, so the old object under the same key
    never keeps claiming to be the current version."""
    stamp = (now or datetime.now(timezone.utc)).astimezone(BEIJING).strftime("%Y-%m-%d %H:%M")
    s = (f'<!doctype html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n{_CSP_META}\n'
         f'<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>{E(label)}（生成失败）· AgentDLC 动手实验</title>\n'
         f"<style>\n{load_css()}\n{STANDALONE_CSS}</style>\n</head>\n<body>\n"
         + topbar(f"{label}（生成失败）· AgentDLC 动手实验", "按 EC2 上的数据文件生成 · 只在你的私有 S3 桶里，不公开",
                  [("生成于", f"{stamp} 北京时间")])
         + f'\n<div class="layout"><main>\n<section class="card" id="snapshot"><div class="danger"><b>{E(label)}这次没能生成</b>'
         f"（{E(stamp)} 北京时间），上一版已被这张说明替换，免得把旧数据当成当前版本。<br>来源：<code>{E(shown(src_path))}</code>"
         f"<br>原因：<code>{E(err)}</code><br>在 EC2 上修好数据文件（黄金集可先运行 <code>python3 data/tools/golden_tool.py validate</code>）"
         f"后重新运行 <code>./tools/publish-data-pages.sh</code>。</div></section>\n</main></div>\n</body>\n</html>\n")
    s = s.replace("__ADLC_CSP__", _csp(s), 1)
    bad = external_refs(s)
    if bad:
        raise ValueError(f"{label}：失败说明页不是自包含的：{sorted(set(bad))}")
    return s


# ---------------------------------------------------------------- golden set page (handbook shape)
DEC_PILL = {"迭代": "g", "拒绝": "r", "待评估": "o"}
CASE_PILL = {"简单": "g", "模糊": "o", "对抗": "p", "应拒绝": "r"}
GOLDEN_COLUMNS = [("编号", "6%"), ("需求原文（Agent 的输入）", "21%"), ("期望标签", "8%"), ("可接受建议", "6%"),
                  ("可自动拒绝", "6%"), ("拒绝要点", "13%"), ("话术要点", "13%"), ("标注理由", "13%"),
                  ("五维 · 样例类型", "7%"), ("相关能力", "7%")]


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "是" if v else "否"
    return str(v)


def _pill(v, cls: str) -> str:
    return f'<span class="pill {cls}">{E(v)}</span>'


def _points(v) -> str:
    pts = [x for x in (v if isinstance(v, list) else [v]) if _text(x).strip()]
    if not pts:
        return '<span class="where">—</span>'
    return '<ul class="pts">' + "".join(f"<li>{E(x)}</li>" for x in pts) + "</ul>"


def _list(v) -> list:
    if isinstance(v, list):
        return v
    return [] if v in (None, "") else [v]


def _dist(counts: Dict[str, int], order, head: str, total: int, pill=None) -> str:
    rank = {v: i for i, v in enumerate(order)}
    rows = sorted(counts.items(), key=lambda kv: (rank.get(kv[0], len(rank)), -kv[1], _text(kv[0])))
    body = "".join(f"<tr><td>{_pill(v, pill.get(v, 'n')) if pill else E(v)}</td><td>{n}</td><td>{MV.pct(n, total)}</td></tr>"
                   for v, n in rows)
    return (f"<div><h4>{E(head)}</h4><table><tr><th>{E(head)}</th><th style=\"width:18%\">条数</th>"
            f"<th style=\"width:18%\">占比</th></tr>{body}</table></div>")


def _auto_rejectable(r: dict) -> bool:
    """Same reading as the evaluators (evaluators/shared/golden_store.py) and golden_lib.stats: missing = True,
    null / false / "" = not auto-rejectable."""
    return bool(r.get("can_auto_reject", True))


def golden_counts(rows: List[dict]) -> dict:
    """Distributions from exactly the values the rows display (_text / _list), so a table never disagrees with
    a row; golden_lib.validate reports the non-standard values separately (golden_validation_note)."""
    from collections import Counter
    c = {k: Counter() for k in ("important", "urgent", "decision", "level", "case_type", "dimensions")}
    for r in rows:
        exp = r.get("expected") if isinstance(r.get("expected"), dict) else {}
        for k in ("important", "urgent", "decision", "level"):
            c[k][_text(exp.get(k)) or "—"] += 1
        c["case_type"][_text(r.get("case_type")) or "—"] += 1
        for d in _list(r.get("dimensions")):
            c["dimensions"][_text(d)] += 1
    out = {k: dict(v) for k, v in c.items()}
    out["n"] = len(rows)
    out["not_auto_rejectable"] = sum(1 for r in rows if not _auto_rejectable(r))
    out["ambiguous_multi_answer"] = sum(1 for r in rows if len(_list(r.get("acceptable_decisions"))) > 1)
    return out


def golden_validation_note(rows: List[dict], golden_path, catalog_ids: Optional[set]) -> str:
    """'' when golden_lib.validate finds no error, else a warning line (HTML) with the first few errors."""
    scheme = "binary"
    try:
        man = json.loads((Path(golden_path).parent / "MANIFEST.json").read_text(encoding="utf-8"))
        scheme = man.get("label_scheme") if man.get("label_scheme") in ("binary", "level") else "binary"
    except (OSError, ValueError, AttributeError):
        pass
    try:
        errors, _ = G.validate(rows, scheme, catalog_ids)
    except Exception as e:  # noqa: BLE001 - a malformed row must not stop the page; say so instead
        return f"<br>⚠️ 校验没能完成（{E(type(e).__name__)}: {E(e)}）：运行 <code>python3 data/tools/golden_tool.py validate</code> 查看。"
    if not errors:
        return ""
    head = "；".join(E(x) for x in errors[:3]) + ("……" if len(errors) > 3 else "")
    return (f"<br>⚠️ 校验有 <b>{len(errors)} 个错误</b>（页面按原值显示，评估器可能另有解读）：{head}。"
            "运行 <code>python3 data/tools/golden_tool.py validate</code> 查看全部，改好后再冻结（手册 2.4）。")


def render_golden_html(rows: List[dict], cap_names: Optional[Dict[str, str]] = None) -> str:
    cap_names = cap_names or {}
    st = golden_counts(rows)
    total = st["n"]
    yn = {"是": "b", "否": "n"}
    dists = "".join([
        _dist(st["important"], C.YES_NO, "是否重要", total, yn),
        _dist(st["urgent"], C.YES_NO, "是否紧急", total, yn),
        _dist(st["decision"], C.DECISIONS, "处理建议", total, DEC_PILL),
        _dist(st["level"], C.LEVELS, "优先级", total),
        _dist(st["case_type"], C.CASE_TYPES, "样例类型", total, CASE_PILL),
        _dist(st["dimensions"], C.DIMENSIONS, "五维覆盖（一条可有多个维度）", total),
    ])
    versions = "、".join(sorted({_text(r.get("version")) or "v1" for r in rows})) or "—"
    head = "".join(f'<th style="width:{w}">{h}</th>' for h, w in GOLDEN_COLUMNS)
    out = []
    for r in rows:
        inp = r.get("input") if isinstance(r.get("input"), dict) else {}
        exp = r.get("expected") if isinstance(r.get("expected"), dict) else {}
        meta = " · ".join(E(x) for x in (inp.get("customer"), inp.get("customer_tier"), inp.get("industry"),
                                          inp.get("module"), inp.get("feedback_type")) if _text(x))
        if _text(inp.get("contract_commitment")) == "是":
            meta += " · 合同承诺"
        labels = (f"重要 {_pill(_text(exp.get('important')) or '—', yn.get(_text(exp.get('important')), 'n'))}<br>"
                  f"紧急 {_pill(_text(exp.get('urgent')) or '—', yn.get(_text(exp.get('urgent')), 'n'))}<br>"
                  f"建议 {_pill(_text(exp.get('decision')) or '—', DEC_PILL.get(_text(exp.get('decision')), 'n'))}<br>"
                  f"优先级 {E(_text(exp.get('level')) or '—')}")
        acc = " ".join(_pill(d, DEC_PILL.get(_text(d), "n")) for d in _list(r.get("acceptable_decisions"))) or "—"
        auto = (_pill("可以", "g") if _auto_rejectable(r)
                else _pill("不可自动拒绝", "r") + '<div class="where">判拒绝记严重错误</div>')
        dims = " ".join(_pill(d, "b") for d in _list(r.get("dimensions")))
        case = _pill(_text(r.get("case_type")), CASE_PILL.get(_text(r.get("case_type")), "n")) if _text(r.get("case_type")) else ""
        caps = "<br>".join(f'<code title="{E(cap_names.get(_text(c), ""))}">{E(c)}</code>'
                           + (f'<div class="where">{E(cap_names[_text(c)])}</div>' if cap_names.get(_text(c)) else "")
                           for c in _list(r.get("matched_capabilities"))) or '<span class="where">—</span>'
        cells = [
            f'<td style="white-space:nowrap"><b>{E(r.get("golden_id"))}</b><div class="where">{E(r.get("feedback_id"))}'
            f'<br>{E(r.get("version"))}</div></td>',
            f'<td><b>{E(inp.get("title"))}</b><div class="small">{E(inp.get("content"))}</div>'
            f'<div class="where">{meta}</div></td>',
            f'<td style="white-space:nowrap">{labels}</td>', f"<td>{acc}</td>", f"<td>{auto}</td>",
            f'<td class="small">{_points(r.get("reject_points"))}</td>',
            f'<td class="small">{_points(r.get("reply_points"))}</td>',
            f'<td class="small">{E(r.get("rationale")) or "—"}</td>', f"<td>{dims}<br>{case}</td>", f'<td class="caps">{caps}</td>']
        out.append(f'      <tr data-row id="{E(r.get("golden_id"))}">{"".join(cells)}</tr>')
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>黄金集 · AgentDLC 动手实验手册</title>
<link rel="stylesheet" href="assets/style.css">
</head>
<body data-page="golden-set">
<div id="topbar"></div>
<div class="layout">
<nav id="toc"></nav>
<main>

<section class="card chapter-head">
  <h1><span class="num">金</span>黄金集（{total} 条）</h1>
  <p class="lead">黄金集是评估用的“标准答案”：每条需求的期望标签、可接受的其它建议、是否可自动拒绝、拒绝要点、话术要点、标注理由、五维和样例类型。</p>
  <p>要补样本，按手册 2.4（判据 → 黄金集）；要逐条改，写进 <a data-repo="data/templates/golden-set-feedback.csv">golden-set-feedback.csv</a>（扩展「按讨论意见重新生成模拟数据」），改完冻结；标注标准见 <a data-repo="data/golden/LABELING_GUIDE.md">LABELING_GUIDE.md</a>。</p>
  <p class="where">版本 {E(versions)} · 多答案可接受 {st["ambiguous_multi_answer"]} 条 · 不可自动拒绝 {st["not_auto_rejectable"]} 条 · Excel 版：<a data-download="data/golden/golden_set.csv"></a>（运行 freeze 时导出）</p>
</section>

<section class="card">
  <div class="step" id="dist">
    <div class="step-head"><span class="num">分布</span><h3>标签分布（共 {total} 条）</h3></div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:4px 18px">{dists}</div>
  </div>

  <div class="step" id="items">
    <div class="step-head"><span class="num">明细</span><h3>{total} 条样例</h3></div>
    <p><input type="search" id="golden-q" placeholder="搜索：编号、标题、客户、标签、要点、理由……（空格分隔多个词，例如：不可自动拒绝 应拒绝 CAP-006）" style="width:60%;padding:6px 10px;font-size:14px"> <span id="golden-n" class="where"></span></p>
    <div style="overflow-x:auto"><table style="min-width:1280px">
      <tr>{head}</tr>
{chr(10).join(out)}
    </table></div>
  </div>
</section>

</main>
</div>
<footer>AgentDLC 动手实验 · 需求价值打分 Agent 实验手册 · 所有数据均为合成数据</footer>
<script src="assets/site.js"></script>
<script>
(function () {{
  var q = document.getElementById("golden-q"), n = document.getElementById("golden-n");
  var rows = Array.prototype.slice.call(document.querySelectorAll("tr[data-row]"));
  function apply() {{
    var terms = q.value.toLowerCase().split(/\\s+/).filter(Boolean), shown = 0;
    rows.forEach(function (r) {{
      var t = r.textContent.toLowerCase();
      var ok = terms.every(function (w) {{ return t.indexOf(w) >= 0; }});
      r.style.display = ok ? "" : "none";
      if (ok) shown++;
    }});
    n.textContent = "显示 " + shown + " / " + rows.length + " 条";
  }}
  q.addEventListener("input", apply);
  apply();
}})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- the three pages
def _is_repo_file(path, default) -> bool:
    """True for the repo's own (synthetic sample) data file; a customer's file passed with --feedback etc. is not."""
    try:
        return Path(path).resolve() == Path(default).resolve()
    except OSError:
        return False


def build_catalog(catalog_path, now=None) -> Tuple[str, str]:
    catalog = CV.load(str(catalog_path))
    n = len(catalog.get("capabilities") or [])
    label = PAGES["catalog"][1]
    page = standalone(CV.render_html(catalog), f"{label}（当前版本，{n} 项）· AgentDLC 动手实验", label,
                      source_info(catalog_path), f"{n} 项", now=now, sample=_is_repo_file(catalog_path, C.CATALOG_PATH))
    return page, f"{n} 项"


def build_mock(feedback_path, pool_path=C.POOL_PATH, extra_path: Optional[str] = "", now=None) -> Tuple[str, str]:
    """extra_path "" = the extra items file named in mock-data-params.yaml (as mock_views does); None = none."""
    if extra_path == "":
        extra_path = MV.extra_items_path()
    feedback = C.read_jsonl(str(feedback_path))
    cats = MV.categories(feedback, MV.category_index(str(pool_path), extra_path))
    unknown = sum(1 for c in cats if c == MV.UNKNOWN)
    note = (f"<br>⚠️ 有 {unknown} 条不在种子库或补充条目里，类别分布里记为“未能归类”。" if unknown else "")
    label = PAGES["mock"][1]
    page = standalone(MV.render_html(feedback, cats), f"{label}（当前版本，{len(feedback)} 条）· AgentDLC 动手实验", label,
                      source_info(feedback_path), f"{len(feedback)} 条", note, now=now,
                      sample=_is_repo_file(feedback_path, C.FEEDBACK_PATH))
    return page, f"{len(feedback)} 条"


def build_golden(golden_path, catalog_path=C.CATALOG_PATH, now=None) -> Tuple[str, str]:
    rows = C.read_jsonl(str(golden_path))
    try:
        caps = [c for c in CV.load(str(catalog_path)).get("capabilities") or [] if isinstance(c, dict)]
        cap_names = {_text(c.get("id")): _text(c.get("name")) for c in caps}
    except (OSError, ValueError, AttributeError):
        cap_names = {}
    label = PAGES["golden"][1]
    notes = golden_freeze_note(golden_path) + golden_validation_note(rows, golden_path, set(cap_names) or None)
    page = standalone(render_golden_html(rows, cap_names), f"{label}（当前版本，{len(rows)} 条）· AgentDLC 动手实验", label,
                      source_info(golden_path), f"{len(rows)} 条", notes, now=now, orig_note=GOLDEN_ORIG_NOTE,
                      sample=_is_repo_file(golden_path, C.GOLDEN_PATH))
    return page, f"{len(rows)} 条"


def _kinds(only: List[str]) -> List[str]:
    if not only:
        return list(PAGES)
    out = []
    for item in only:
        for k in item.replace("，", ",").split(","):
            k = k.strip()
            if k and k not in PAGES:
                raise SystemExit(f"❌ --only 只能是 {' / '.join(PAGES)}：{k}")
            if k and k not in out:
                out.append(k)
    if not out:
        raise SystemExit(f"❌ --only 后面要写页面：{' / '.join(PAGES)}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="./tools/publish-data-pages.sh", allow_abbrev=False,  # the wrapper matches exact flags
                                 description="把 EC2 上当前的能力目录 / mock 需求 / 黄金集生成自包含网页，上传到你的私有 S3 桶")
    ap.add_argument("--no-upload", action="store_true", help="只生成本地文件，不上传（不调用任何 AWS 接口）")
    ap.add_argument("--only", action="append", default=[], help="只做这几页：catalog / mock / golden（可写多次或用逗号分隔）")
    ap.add_argument("--out-dir", default=str(OUT_DIR), help="本地输出目录（默认 reports/data-pages/）")
    ap.add_argument("--catalog", default=C.CATALOG_PATH, help="能力目录 JSON（默认 data/capability_catalog.json）")
    ap.add_argument("--feedback", default=C.FEEDBACK_PATH, help="需求反馈 JSONL（默认 data/feedback_last_month.jsonl）")
    ap.add_argument("--golden", default=C.GOLDEN_PATH, help="黄金集 JSONL（默认 data/golden/golden_set.jsonl）")
    ap.add_argument("--pool", default=C.POOL_PATH, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    kinds = _kinds(a.only)
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if os.environ.get("ADLC_DATA_DIR"):
        print(f"ℹ️  设置了 ADLC_DATA_DIR={os.environ['ADLC_DATA_DIR']}：Agent 读的是那里的数据；"
              "要展示那份数据，请用 --feedback / --catalog 指定文件。")
    print(f"▶ 按 EC2 上的当前数据生成网页 → {shown(out_dir)}/")
    builders = {"catalog": (a.catalog, lambda: build_catalog(a.catalog)),
                "mock": (a.feedback, lambda: build_mock(a.feedback, a.pool)),
                "golden": (a.golden, lambda: build_golden(a.golden, a.catalog))}
    rc, written = 0, []
    for k in kinds:
        name, label = PAGES[k]
        src, build = builders[k]
        path = out_dir / name
        try:
            page, count = build()
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            # replace the old page (and, below, the S3 object): it must not keep saying "current version"
            rc = 1
            print(f"  ❌ {label}：无法生成（{shown(src)}）：{e}")
            try:
                path.write_text(failure_page(label, src, f"{type(e).__name__}: {e}"), encoding="utf-8")
            except (OSError, ValueError) as e2:
                if path.exists():
                    path.unlink()  # never leave the previous run's page behind as if it were current
                print(f"     连“生成失败”说明页也没能生成（{e2}），已删除本地旧页面；S3 上的旧对象没有改动")
                continue
            written.append((k, label, path))
            print(f"     已写入一页“生成失败”说明替换旧页面 → {shown(path)}")
            continue
        path.write_text(page, encoding="utf-8")
        written.append((k, label, path))
        print(f"  ✅ {label}：{count}（{shown(src)}）→ {shown(path)}")
    if a.no_upload:
        if written:
            print("--no-upload：只写了本地文件，没有上传。EC2 上的文件浏览器打不开；去掉 --no-upload 重跑即可上传到你的私有桶。")
        return rc
    links = []
    for k, label, path in written:
        try:
            from labkit import publish as P  # boto3 only on the upload path
            links.append((label, P.publish(str(path))))
        except Exception as e:  # noqa: BLE001 - report every failure, keep the local file
            rc = 1
            pub = sys.modules.get("labkit.publish")
            if pub is not None and isinstance(e, pub.NotPrivateError):  # same bucket for every page: stop here
                print(f"  ❌ {e}")
                break
            print(f"  ❌ {label}：上传失败（本地文件保留在 {shown(path)}）：{type(e).__name__}: {e}")
    if links:
        w = max(len(label) for label, _ in links)
        print("\n在笔记本浏览器打开（需已登录这个实验账号的控制台），点右上角「打开」查看（桶是私有的，链接不能给组外的人用）：")
        for label, url in links:
            print(f"  {label.ljust(w)}  {url}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
