"""The "可填写的值（速查）" table on docs/discussion.html must list what the code accepts."""
import os
import re

import common as C
import golden_lib as G


def _block():
    with open(os.path.join(C.REPO, "docs", "discussion.html"), encoding="utf-8") as f:
        page = f.read()
    return page.split('<div id="s-d-5-values">', 1)[1].split("</div>", 1)[0]


def _row(block, col):
    return next(r for r in block.split("<tr>") if r.startswith(f"<td>{col}"))


def gh_slug(heading: str) -> str:
    """GitHub's heading anchor: lower-case, drop punctuation/symbols, spaces -> '-'."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def test_values_match_the_code_constants():
    b = _block()
    for op in G.OP_CHOICES.split(" / "):
        assert f"<code>{op}</code>" in _row(b, "C 操作")
    for col in ("D 是否重要", "E 是否紧急", "I 可自动拒绝"):
        assert all(f"<code>{v}</code>" in _row(b, col) for v in C.YES_NO), col
    assert all(f"<code>{v}</code>" in _row(b, "F 处理建议") for v in C.DECISIONS)
    assert all(f"<code>{v}</code>" in _row(b, "H 优先级") for v in C.LEVELS)
    assert all(f"<code>{v}</code>" in _row(b, "J 五维") for v in C.DIMENSIONS)
    assert all(f"<code>{v}</code>" in _row(b, "K 样例类型") for v in C.CASE_TYPES)
    # separators: tags split on C.TAG_SEPS (/ works), points on C.LIST_SEPS (； works, / does not)
    assert C.split_list("迭代/待评估", C.TAG_SEPS) == ["迭代", "待评估"] and "<code>/</code>" in _row(b, "G 可接受建议")
    assert C.split_list("a；b", C.LIST_SEPS) == ["a", "b"] and C.split_list("a/b", C.LIST_SEPS) == ["a/b"]
    assert "<code>；</code>" in _row(b, "L 拒绝要点") and "不要用 /" in _row(b, "L 拒绝要点")
    assert "⚠️" in _row(b, "G 可接受建议") and G.ACC_WARN                          # matches the loud warning


def test_every_labeling_guide_link_hits_a_real_heading():
    with open(os.path.join(C.REPO, "data", "golden", "LABELING_GUIDE.md"), encoding="utf-8") as f:
        slugs = {gh_slug(line.lstrip("#").replace("`", "")) for line in f if line.startswith("#")}
    links = re.findall(r'data-repo="data/golden/LABELING_GUIDE\.md#([^"]+)"', _block())
    assert len(links) >= 9
    assert set(links) <= slugs, set(links) - slugs


def test_pointers_to_the_table():
    with open(os.path.join(C.REPO, "docs", "discussion.html"), encoding="utf-8") as f:
        page = f.read()
    d6 = page.split('id="s-d-6"', 1)[1].split('id="s-d-7"', 1)[0]
    assert 'href="#s-d-5-values"' in d6
    with open(os.path.join(C.REPO, "docs", "02-data.html"), encoding="utf-8") as f:
        s25 = f.read().split('id="s-2-5"', 1)[1]
    assert 'href="discussion.html#s-d-5-values"' in s25.split("<table>", 1)[0]       # above the column table
