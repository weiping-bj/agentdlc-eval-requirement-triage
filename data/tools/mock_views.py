#!/usr/bin/env python3
"""Readable view of the 60 mock requirement items, generated from data/feedback_last_month.jsonl.

    python3 data/tools/mock_views.py          # write docs/mock-data.html
    python3 data/tools/mock_views.py --check  # exit 1 if it is stale

The page shows the aggregate distributions (需求类别 / 反馈类型 / 客户等级 / 行业 / 来源渠道) and the
items themselves. The category of each item comes from data/tools/seed/pool.jsonl (and the extra items
file named in mock-data-params.yaml), joined by id, falling back to title + content when
generate_mock_data renumbered the ids. Only the aggregate category distribution is shown, never a
per-item category, because that is the answer key. The data file itself is untouched.

Generated: do not edit docs/mock-data.html by hand. generate_mock_data.py refreshes it when it writes
the repo data file, and evaluators/sync_shared.py writes and checks it like the other generated views.
"""
from __future__ import annotations

import argparse
import html
import os
import sys
from collections import Counter
from typing import Dict, List, Optional, Tuple

TOOLS = os.path.dirname(os.path.abspath(__file__))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
import common as C  # noqa: E402
import catalog_views as CV  # noqa: E402
from generate_mock_data import CATEGORY_CN, load_params  # noqa: E402

HTML_PATH = os.path.join(C.REPO, "docs", "mock-data.html")
CSV_REPO_PATH = "data/feedback_last_month.csv"
PARAMS_PATH = os.path.join(C.DATA, "templates", "mock-data-params.yaml")
UNKNOWN = "未能归类（不在种子库或补充条目里）"

# (field, column header) — same order as C.INPUT_FIELDS
COLUMNS = [("id", "编号"), ("title", "标题"), ("content", "需求描述"), ("customer", "客户"), ("industry", "行业"),
           ("customer_tier", "客户等级"), ("channel", "来源渠道"), ("module", "相关模块"), ("submitted_at", "提交时间"),
           ("feedback_type", "反馈类型"), ("contract_commitment", "合同承诺")]
WIDTHS = {"id": "7%", "title": "11%", "content": "30%", "customer": "10%", "industry": "6%", "customer_tier": "6%",
          "channel": "7%", "module": "7%", "submitted_at": "7%", "feedback_type": "5%", "contract_commitment": "4%"}
NOWRAP = {"id", "industry", "customer_tier", "submitted_at", "feedback_type", "contract_commitment"}
DISTRIBUTIONS = [("feedback_type", "反馈类型"), ("customer_tier", "客户等级"), ("industry", "行业"), ("channel", "来源渠道")]


def _key(r: dict) -> Tuple[str, str]:
    return (str(r.get("title") or ""), str(r.get("content") or ""))


def extra_items_path(params_path: str = PARAMS_PATH) -> Optional[str]:
    p = load_params(params_path).get("extra_items_file") if os.path.exists(params_path) else None
    if not p:
        return None
    return p if os.path.isabs(p) else os.path.join(C.REPO, p)


def category_index(pool_path: str = C.POOL_PATH, extra_path: Optional[str] = None):
    """({id: category}, {(title, content): category}) from the seed pool plus the extra items file."""
    by_id, by_text = {}, {}
    rows = C.read_jsonl(pool_path)
    if extra_path and os.path.exists(extra_path):
        rows += C.read_jsonl(extra_path)
    for r in rows:
        cat = (r.get("_meta") or {}).get("category")
        if cat:
            by_id.setdefault(r.get("id"), cat)
            by_text.setdefault(_key(r), cat)
    return by_id, by_text


def categories(feedback: List[dict], index) -> List[str]:
    by_id, by_text = index
    out = []
    for r in feedback:
        cat = by_text.get(_key(r)) or by_id.get(r.get("id"))
        out.append(cat or UNKNOWN)
    return out


def category_label(cat: str) -> str:
    return CATEGORY_CN.get(cat, cat)


def pct(n: int, total: int) -> str:
    if not total:
        return "0%"
    s = f"{n * 100 / total:.1f}"
    return (s[:-2] if s.endswith(".0") else s) + "%"


def distribution(values: List[str], order: Optional[List[str]] = None) -> List[Tuple[str, int]]:
    """[(value, count)] by count desc; ties keep `order` (or first appearance)."""
    cnt = Counter(values)
    rank = {v: i for i, v in enumerate(order or [])}
    first = {}
    for i, v in enumerate(values):
        first.setdefault(v, i)
    return sorted(cnt.items(), key=lambda kv: (-kv[1], rank.get(kv[0], len(rank)), first[kv[0]]))


def _text(v) -> str:
    return "" if v is None else str(v)


def render_html(feedback: List[dict], cats: List[str]) -> str:
    e = lambda v: html.escape(_text(v), quote=True)  # noqa: E731
    total = len(feedback)

    def table(rows: List[Tuple[str, int]], head: str, label=lambda v: v) -> str:
        body = "".join(f"<tr><td>{e(label(v) or '（空）')}</td><td>{n}</td><td>{pct(n, total)}</td></tr>" for v, n in rows)
        return f"<table><tr><th>{head}</th><th style=\"width:18%\">条数</th><th style=\"width:18%\">占比</th></tr>{body}</table>"

    cat_rows = distribution(cats, list(CATEGORY_CN))
    small = "".join(f"<div><h4>{h}</h4>{table(distribution([_text(r.get(k)) for r in feedback]), h)}</div>"
                    for k, h in DISTRIBUTIONS)
    head = "".join(f'<th style="width:{WIDTHS[k]}">{h}</th>' for k, h in COLUMNS)
    rows = []
    for r in feedback:
        cells = "".join(f'<td style="white-space:nowrap">{e(r.get(k))}</td>' if k in NOWRAP else f"<td>{e(r.get(k))}</td>"
                        for k, _ in COLUMNS)
        rows.append(f'      <tr data-row id="{e(r.get("id"))}">{cells}</tr>')
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>60 条 mock 需求 · 场景 12 实验手册</title>
<link rel="stylesheet" href="assets/style.css">
</head>
<body data-page="mock-data">
<!-- 由 data/tools/mock_views.py 根据 data/feedback_last_month.jsonl 生成，不要手改；重新生成数据后运行 python3 evaluators/sync_shared.py -->
<div id="topbar"></div>
<div class="layout">
<nav id="toc"></nav>
<main>

<section class="card chapter-head">
  <h1><span class="num">数</span>上个月的 mock 需求反馈（{total} 条，合成） <span class="applies common">A/B 通用</span></h1>
  <p class="lead">这是下午 Agent 要分诊的“上个月的需求反馈”。11:00 讨论时请<b>看一遍</b>：条数、类别占比、行业、渠道像不像你们的真实情况，缺了哪类常见需求。</p>
  <p>意见用大白话写进 <a data-repo="data/templates/discussion-feedback.md">讨论反馈模板</a> 第 5 节即可；下午 coding agent 会把它改成 mock 数据参数和新增条目，你们不用打开参数文件。</p>
  <p class="where">本页由 <code>data/tools/mock_views.py</code> 根据 <a data-repo="data/feedback_last_month.jsonl">feedback_last_month.jsonl</a> 自动生成 · Excel 版：<a data-download="{CSV_REPO_PATH}"></a> · 全部为合成数据（客户名均为“（示例）”）</p>
</section>

<section class="card">
  <div class="step" id="dist">
    <div class="step-head"><span class="num">分布</span><h3>现在的分布（共 {total} 条）</h3></div>
    <div id="dist-category">
      <h4>需求类别</h4>
      {table(cat_rows, "需求类别", category_label)}
      <p class="where">类别只给总体占比，不标在每一条上（那是下午要让 Agent 判断的答案）。</p>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:4px 18px">{small}</div>
  </div>

  <div class="step" id="items">
    <div class="step-head"><span class="num">明细</span><h3>{total} 条需求</h3></div>
    <p><input type="search" id="mock-q" placeholder="搜索：标题、描述、客户、行业、渠道、模块……（空格分隔多个词）" style="width:60%;padding:6px 10px;font-size:14px"> <span id="mock-n" class="where"></span></p>
    <table>
      <tr>{head}</tr>
{chr(10).join(rows)}
    </table>
  </div>
</section>

</main>
</div>
<footer>场景 12 · 需求价值打分 Agent 实验手册 · 所有数据均为合成数据</footer>
<script src="assets/site.js"></script>
<script>
(function () {{
  var q = document.getElementById("mock-q"), n = document.getElementById("mock-n");
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


def planned(feedback_path: str = C.FEEDBACK_PATH, pool_path: str = C.POOL_PATH, extra_path: Optional[str] = "",
            html_path: str = HTML_PATH) -> Dict[str, bytes]:
    """extra_path "" = the extra items file named in mock-data-params.yaml; None = no extra file."""
    if extra_path == "":
        extra_path = extra_items_path()
    feedback = C.read_jsonl(feedback_path)
    cats = categories(feedback, category_index(pool_path, extra_path))
    return {html_path: render_html(feedback, cats).encode("utf-8")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="只检查，不写文件；过期时退出码 1")
    ap.add_argument("--feedback", default=C.FEEDBACK_PATH)
    ap.add_argument("--pool", default=C.POOL_PATH)
    ap.add_argument("--html", default=HTML_PATH)
    a = ap.parse_args(argv)
    stale = [os.path.relpath(p, C.REPO) for p in CV.sync(planned(a.feedback, a.pool, "", a.html), a.check)]
    if a.check:
        if stale:
            print("mock 数据页过期（请运行 python3 data/tools/mock_views.py）：\n  " + "\n  ".join(stale))
            return 1
        print("mock 数据页已同步。")
        return 0
    print("已更新：" + (", ".join(stale) if stale else "无（已是最新）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
