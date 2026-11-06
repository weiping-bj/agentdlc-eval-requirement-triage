"""Every "N.M" step number the guide mentions, and every #s-… anchor it links, must exist."""
import html
import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
PAGES = sorted(DOCS.glob("*.html"))
STEP_RX = re.compile(r'<span class="num">([^<]+)</span><h3')
REF_RX = re.compile(r"(?<![\w.\-/])((?:[1-9]|10|A[3-5]|B[34]|X|D))\.(\d{1,2})(?![\d.%\w])")
NOT_A_STEP_BEFORE = re.compile(r"(Sonnet|Haiku|Opus|Fable|Nova|Python|版本|v)\s*$")
NOT_A_STEP_AFTER = re.compile(r"^\s*(KB|MB|GB|秒|分钟|%)")


def _text(page):
    return html.unescape(re.sub(r"<[^>]+>", " ", page.read_text(encoding="utf-8")))


def test_every_step_number_mentioned_exists():
    steps = {n.strip() for p in PAGES for n in STEP_RX.findall(p.read_text(encoding="utf-8"))}
    assert {"2.4", "2.5", "6.5", "D.5", "X.6", "A3.1"} <= steps
    bad = []
    for p in PAGES:
        t = _text(p)
        for m in REF_RX.finditer(t):
            ref = f"{m.group(1)}.{m.group(2)}"
            if ref in steps or NOT_A_STEP_BEFORE.search(t[:m.start()]) or NOT_A_STEP_AFTER.match(t[m.end():]):
                continue
            bad.append(f"{p.name}: {ref} … {t[max(0, m.start() - 20):m.end() + 10]!r}")
    assert not bad, "\n".join(bad)


def test_every_step_anchor_link_resolves():
    ids = {p.name: set(re.findall(r'id="([^"]+)"', p.read_text(encoding="utf-8"))) for p in PAGES}
    bad = []
    for p in PAGES:
        for page, anchor in re.findall(r'href="([\w\-]*\.html)?#(s-[^"]+)"', p.read_text(encoding="utf-8")):
            target = page or p.name
            if anchor not in ids.get(target, set()):
                bad.append(f"{p.name} -> {target}#{anchor}")
    assert not bad, bad


def test_chapter_6_prerequisite_matches_labkit_online():
    root = DOCS.parent
    online = (root / "labkit" / "online.py").read_text(encoding="utf-8")
    a_src = online.split('if path == "a":', 1)[1].split("st = aws.load_state", 1)[0]
    assert "LOCAL_LOG_GROUP" in a_src and "LOCAL_SERVICE_NAME" in a_src and "CONVERTED" not in a_src
    assert "还没有部署 Runtime" in online
    a4 = (root / "11-A-convert-logs.sh").read_text(encoding="utf-8")
    assert "CONVERTED_SERVICE_NAME" in a4 and "LOCAL_SERVICE_NAME" not in a4      # A4 is not watched by path a
    head = (DOCS / "06-evaluators.html").read_text(encoding="utf-8").split('<section class="card">', 1)[0]
    assert "6.1</a>–<a" in head and "两条路线通用，只需要完成第 2 章" in head and "A4 导入的旧日志" in head
