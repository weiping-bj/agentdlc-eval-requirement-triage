#!/usr/bin/env python3
"""Readable views of the platform capability catalog, generated from data/capability_catalog.json.

    python3 data/tools/catalog_views.py          # write docs/catalog.html + data/capability_catalog.csv
    python3 data/tools/catalog_views.py --check  # exit 1 if either is stale

Both files are generated: do not edit them by hand. evaluators/sync_shared.py writes and checks them
too, so 04-update-capability-catalog.md (step 3), golden_tool.py freeze and the test suite keep them
in step with the JSON. Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import html
import io
import json
import os
import sys
from typing import Dict, List

TOOLS = os.path.dirname(os.path.abspath(__file__))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
import common as C  # noqa: E402

HTML_PATH = os.path.join(C.REPO, "docs", "catalog.html")
CSV_PATH = os.path.join(C.DATA, "capability_catalog.csv")

# (JSON field, CSV header)
CSV_COLUMNS = [("id", "编号"), ("name", "名称"), ("module", "模块"), ("status", "状态"),
               ("description", "说明"), ("limits", "限制"), ("workaround", "替代方案"), ("how_to", "用法"),
               ("effort_hint", "难度提示"), ("keywords", "关键词")]
HTML_COLUMNS = CSV_COLUMNS[:8]
HTML_WIDTHS = {"id": "8%", "name": "11%", "module": "9%", "status": "8%", "description": "22%", "limits": "16%",
               "workaround": "13%", "how_to": "13%"}
NOWRAP = {"id", "module", "status"}
KEYWORD_SEP = "、"
STATUS_ORDER = ["已支持", "部分支持", "规划中", "明确不做"]
STATUS_STYLE = {"已支持": ("g", "st-supported", "✓"), "部分支持": ("o", "st-partial", "◐"),
                "规划中": ("b", "st-planned", "…"), "明确不做": ("r", "st-oos", "✕")}


def load(path: str = C.CATALOG_PATH) -> dict:
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return KEYWORD_SEP.join(_text(x) for x in v)
    return str(v)


def csv_row(cap: dict) -> List[str]:
    """One CSV row; cells pass through the same formula guard as every other CSV this repo writes."""
    return [_text(C._escape_cell(_text(cap.get(k)))) for k, _ in CSV_COLUMNS]


def render_csv(catalog: dict) -> bytes:
    """UTF-8 with BOM and Excel's own dialect (CRLF), like common.write_csv."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([h for _, h in CSV_COLUMNS])
    for cap in catalog.get("capabilities", []):
        w.writerow(csv_row(cap))
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def groups(catalog: dict) -> List[tuple]:
    """[(status, [caps])] in STATUS_ORDER, then any other status in order of first appearance."""
    by: Dict[str, list] = {}
    for cap in catalog.get("capabilities", []):
        by.setdefault(_text(cap.get("status")) or "未标状态", []).append(cap)
    order = [s for s in STATUS_ORDER if s in by] + [s for s in by if s not in STATUS_ORDER]
    return [(s, by[s]) for s in order]


def _style(status: str):
    """(pill class, anchor, mark); an unknown status gets an anchor derived from its text, so it stays stable."""
    return STATUS_STYLE.get(status, ("p", "st-x" + status.encode("utf-8").hex(), "?"))


def render_html(catalog: dict) -> str:
    e = lambda v: html.escape(_text(v), quote=True)  # noqa: E731
    plat = catalog.get("platform") or {}
    caps = catalog.get("capabilities", [])
    gs = groups(catalog)
    chips = " ".join(f'<a href="#{_style(s)[1]}"><span class="pill {_style(s)[0]}">{e(s)} {len(items)} 项</span></a>'
                     for s, items in gs)
    principles = "".join(f"<li>{e(p)}</li>" for p in plat.get("principles") or [])
    out = [f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>平台能力目录 · 场景 12 实验手册</title>
<link rel="stylesheet" href="assets/style.css">
</head>
<body data-page="catalog">
<!-- 由 data/tools/catalog_views.py 根据 data/capability_catalog.json 生成，不要手改；改完 JSON 运行 python3 evaluators/sync_shared.py -->
<div id="topbar"></div>
<div class="layout">
<nav id="toc"></nav>
<main>

<section class="card chapter-head">
  <h1><span class="num">录</span>平台能力目录：{e(plat.get("name"))} <span class="applies common">A/B 通用</span></h1>
  <p class="lead">{e(plat.get("positioning"))}</p>
  <p>Agent 判断“已有能力 / 真实缺口 / 明确不做”只看这份目录。11:00 讨论时请<b>看一遍</b>：缺了哪些能力、哪里写错了，填进 <a data-repo="data/templates/discussion-feedback.md">讨论反馈模板</a> 第 3 节；不用改本页，也不用看 JSON。</p>
  <p>共 {len(caps)} 项：{chips}</p>
  <p class="where">版本 {e(catalog.get("version"))}{" · 全部为合成数据" if catalog.get("synthetic") else ""} · 本页由 <code>data/tools/catalog_views.py</code> 根据 <a data-repo="data/capability_catalog.json">capability_catalog.json</a> 自动生成 · Excel 版：<a data-download="data/capability_catalog.csv"></a></p>
</section>

<section class="card">
  <div class="step" id="st-principles">
    <div class="step-head"><span class="num">原则</span><h3>平台定位与原则</h3></div>
    <ol>{principles}</ol>
  </div>
"""]
    for status, items in gs:
        cls, anchor, mark = _style(status)
        rows = []
        for cap in items:
            cells = []
            for k, _ in HTML_COLUMNS:
                v = e(cap.get(k))
                if k == "id":
                    v = f"<code>{v}</code>"
                elif k == "status":
                    v = f'<span class="pill {cls}">{v}</span>'
                cells.append(f'<td style="white-space:nowrap">{v}</td>' if k in NOWRAP else f"<td>{v}</td>")
            rows.append(f'      <tr id="{e(cap.get("id"))}">{"".join(cells)}</tr>')
        head = "".join(f'<th style="width:{HTML_WIDTHS[k]}">{h}</th>' for k, h in HTML_COLUMNS)
        out.append(f"""
  <div class="step" id="{anchor}">
    <div class="step-head"><span class="num">{mark}</span><h3>{e(status)}（{len(items)} 项）</h3></div>
    <table>
      <tr>{head}</tr>
{chr(10).join(rows)}
    </table>
  </div>
""")
    out.append("""</section>

</main>
</div>
<footer>场景 12 · 需求价值打分 Agent 实验手册 · 所有数据均为合成数据</footer>
<script src="assets/site.js"></script>
</body>
</html>
""")
    return "".join(out)


def planned(catalog_path: str = C.CATALOG_PATH, html_path: str = HTML_PATH, csv_path: str = CSV_PATH) -> Dict[str, bytes]:
    catalog = load(catalog_path)
    return {html_path: render_html(catalog).encode("utf-8"), csv_path: render_csv(catalog)}


def same(a: bytes, b: bytes) -> bool:
    """Equal up to line endings: a Windows checkout (git core.autocrlf) turns LF into CRLF, which is not drift."""
    return a.replace(b"\r\n", b"\n") == b.replace(b"\r\n", b"\n")


def sync(files: Dict[str, bytes], check: bool) -> List[str]:
    """Write (or with check=True only compare) generated files; return the stale ones."""
    stale = []
    for path, content in files.items():
        cur = None
        if os.path.exists(path):
            with open(path, "rb") as f:
                cur = f.read()
        if cur is None or not same(cur, content):
            stale.append(path)
            if not check:
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(content)
    return stale


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="只检查，不写文件；过期时退出码 1")
    ap.add_argument("--catalog", default=C.CATALOG_PATH)
    ap.add_argument("--html", default=HTML_PATH)
    ap.add_argument("--csv", default=CSV_PATH)
    a = ap.parse_args(argv)
    stale = [os.path.relpath(p, C.REPO) for p in sync(planned(a.catalog, a.html, a.csv), a.check)]
    if a.check:
        if stale:
            print("能力目录视图过期（请运行 python3 data/tools/catalog_views.py）：\n  " + "\n  ".join(stale))
            return 1
        print("能力目录视图已同步。")
        return 0
    print("已更新：" + (", ".join(stale) if stale else "无（已是最新）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
