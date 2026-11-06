"""docs/mock-data.html must match data/feedback_last_month.jsonl (+ categories from seed/pool.jsonl)."""
import collections
import html
import json
import os
import sys

import common as C
import catalog_views as CV
import generate_mock_data as GM
import mock_views as MV


def _page():
    with open(MV.HTML_PATH, "rb") as f:
        return f.read().decode("utf-8")


def _sections(page):
    dist = page.split('<div class="step" id="dist">', 1)[1].split('<div class="step" id="items">', 1)[0]
    items = page.split('<div class="step" id="items">', 1)[1]
    return dist, items


def _row(label, n, total):
    return f"<tr><td>{html.escape(label)}</td><td>{n}</td><td>{MV.pct(n, total)}</td></tr>"


def test_committed_page_matches_the_data_files():
    for path, content in MV.planned().items():
        with open(path, "rb") as f:
            assert CV.same(f.read(), content), "docs/mock-data.html 过期：运行 python3 evaluators/sync_shared.py"


def test_summary_tables_match_the_data_independently_computed():
    feedback = C.read_jsonl(C.FEEDBACK_PATH)
    pool = {r["id"]: r["_meta"]["category"] for r in C.read_jsonl(C.POOL_PATH)}   # plain join by id
    total = len(feedback)
    dist, _ = _sections(_page())
    cat = dist.split('<div id="dist-category">', 1)[1].split("</table>", 1)[0]
    cats = collections.Counter(pool[r["id"]] for r in feedback)
    assert sum(cats.values()) == total
    for key, n in cats.items():
        assert _row(GM.CATEGORY_CN[key], n, total) in cat, key
    assert MV.UNKNOWN not in dist
    for field, _ in MV.DISTRIBUTIONS:
        for value, n in collections.Counter(r.get(field) or "" for r in feedback).items():
            assert _row(value or "（空）", n, total) in dist, (field, value)
    assert MV.pct(15, 60) == "25%" and MV.pct(8, 60) == "13.3%"


def test_every_item_is_listed_in_order_with_every_field():
    feedback = C.read_jsonl(C.FEEDBACK_PATH)
    _, items = _sections(_page())
    rows = items.split("<tr data-row ")[1:]
    assert len(rows) == len(feedback)
    for r, row in zip(feedback, rows):
        row = row.split("</tr>", 1)[0]
        assert row.startswith(f'id="{r["id"]}"')
        for key, _ in MV.COLUMNS:
            assert html.escape(str(r.get(key) or ""), quote=True) in row, (r["id"], key)


def test_no_per_item_category_next_to_the_items():
    _, items = _sections(_page())
    for key, label in GM.CATEGORY_CN.items():
        assert key not in items and label not in items
    assert "_meta" not in _page() and "golden" not in items


def test_category_join_survives_renumbered_ids_and_flags_unknown_items():
    pool = C.read_jsonl(C.POOL_PATH)
    items, _ = GM.build({"month": "2026-11"}, pool, [])           # ids become FB-2611-xxx
    feedback = [{k: v for k, v in r.items() if k != "_meta"} for r in items]
    assert not set(r["id"] for r in feedback) & set(r["id"] for r in pool)
    cats = MV.categories(feedback, MV.category_index(C.POOL_PATH, None))
    assert cats == [r["_meta"]["category"] for r in items]
    stray = dict(feedback[0], id="FB-X-1", title="新写的", content="种子库里没有")
    assert MV.categories([stray], MV.category_index(C.POOL_PATH, None)) == [MV.UNKNOWN]


def test_extra_items_file_supplies_categories(tmp_path):
    extra = tmp_path / "extra.jsonl"
    row = {"id": "FB-NEW-001", "title": "额外", "content": "额外条目", "_meta": {"category": "vague"}}
    extra.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    fb = [{"id": "FB-2610-099", "title": "额外", "content": "额外条目"}]
    assert MV.categories(fb, MV.category_index(C.POOL_PATH, str(extra))) == ["vague"]


def test_untrusted_text_is_escaped_and_drift_is_detected(tmp_path):
    feedback = C.read_jsonl(C.FEEDBACK_PATH)
    feedback[0] = dict(feedback[0], title="<script>alert(1)</script>", customer='"><img src=x onerror=1>')
    fb = tmp_path / "fb.jsonl"
    C.write_jsonl(str(fb), feedback)
    out = MV.planned(str(fb), C.POOL_PATH, None, str(tmp_path / "m.html"))
    page = next(iter(out.values())).decode("utf-8")
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page and "<img src=x" not in page
    assert MV.UNKNOWN not in page                               # edited text no longer joins by text, but by id
    assert CV.sync(out, check=True) == [str(tmp_path / "m.html")]  # missing file = stale
    assert MV.main(["--feedback", str(fb), "--html", str(tmp_path / "m.html"), "--check"]) == 1
    assert MV.main(["--feedback", str(fb), "--html", str(tmp_path / "m.html")]) == 0
    assert MV.main(["--feedback", str(fb), "--html", str(tmp_path / "m.html"), "--check"]) == 0


def test_sync_shared_covers_the_page():
    sys.path.insert(0, os.path.join(C.REPO, "evaluators"))
    import sync_shared
    assert MV.HTML_PATH in sync_shared.planned(C.GOLDEN_PATH, C.CATALOG_PATH)


def test_guide_and_template_link_the_page():
    with open(os.path.join(C.REPO, "docs", "discussion.html"), encoding="utf-8") as f:
        disc = f.read()
    d6 = disc.split('id="s-d-6"', 1)[1].split('id="s-d-7"', 1)[0]
    assert '<a href="mock-data.html">' in d6 and 'data-download="data/feedback_last_month.csv"' in d6
    with open(os.path.join(C.DATA, "templates", "discussion-feedback.md"), encoding="utf-8") as f:
        tpl = f.read()
    sec5 = tpl.split("## 5.", 1)[1]
    title = sec5.split("\n", 1)[0]
    assert "mock-data-params.yaml" not in title and "/mock-data.html)" in sec5
    cat_line = next(line for line in sec5.splitlines() if "类别占比" in line)
    assert "/mock-data.html#dist-category)" in cat_line and 'id="dist-category"' in _page()
    with open(os.path.join(C.REPO, "docs", "assets", "site.js"), encoding="utf-8") as f:
        assert 'file: "mock-data.html"' in f.read()


def test_guide_2_4_leads_with_the_prompt_and_ends_with_the_next_step():
    with open(os.path.join(C.REPO, "docs", "02-data.html"), encoding="utf-8") as f:
        s24 = f.read().split('id="s-2-4"', 1)[1].split('id="s-2-5"', 1)[0]
    main, manual = s24.split('id="s-2-4-manual"', 1)
    assert 'claude "$(cat data/prompts/02-regenerate-mock-data.md)"' in main and "基本符合" in main
    assert "git diff data/templates/mock-data-params.yaml" in main
    assert "generate_mock_data.py</code></pre>" in manual                       # hand-editing is the secondary box
    assert main.index("02-regenerate-mock-data.md") < main.index("mock-data-params.yaml")
    tail = manual.rsplit("下一步", 1)[1]
    assert 'href="#s-2-5"' in tail and "./20-B-deploy-runtime.sh" in tail
    # the distribution shown is exactly what the generator prints for the default parameters
    cats = collections.Counter(r["_meta"]["category"] for r in C.read_jsonl(C.POOL_PATH))
    for k, n in cats.items():
        assert f"  - {GM.CATEGORY_CN[k]}：{n}" in main.replace("</pre>", "\n").splitlines(), k
    with open(os.path.join(C.DATA, "prompts", "02-regenerate-mock-data.md"), encoding="utf-8") as f:
        prompt = f.read()
    assert "generate_mock_data.py" in prompt and "原样贴出来" in prompt and "可以跳过 2.4" in prompt
