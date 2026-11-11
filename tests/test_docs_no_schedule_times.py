"""Attendee-facing text never pins the workshop schedule to a clock time or a part of the day.

The on-site schedule keeps moving, so "11:00 分组讨论", "今天下午要完成", "16:40 前完成对比" or "17:00 Demo"
mislead attendees. Pages describe the order instead (分组讨论 -> 动手环节 -> Demo) and keep per-step durations
("约 25 分钟"). Data is not schedule wording and stays out of the scan: the rows of the two generated data pages
(mock-data.html, catalog.html; their prose is checked in the generators that write it), rendered compare
reports (docs/compare/), data files (csv/jsonl/json), and timestamps that are printed data (ISO 8601, or a date
and time followed by UTC / 北京时间). Anything else that only looks like a time goes in ALLOWED.
This is a tripwire for the usual ways of writing a time, not a parser of every Chinese time expression.
"""
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATED = {"docs/mock-data.html": "data/tools/mock_views.py", "docs/catalog.html": "data/tools/catalog_views.py"}
GLOBS = ("docs/*.html", "docs/assets/*.js", "*.md", "*.sh", "tools/*.sh", "ec2/*.md", "ec2/*.sh", "ec2/*.yaml",
         "evaluators/README.md", "telemetry/*.md", "telemetry/*.sh", "data/*.md", "data/golden/*.md",
         "data/prompts/*.md", "data/templates/*.md", "data/templates/*.yaml", "labkit/*.py", "data/tools/*.py",
         "agent/s12agent/prompts/*.changes.md")


def _scanned():
    files = {p for g in GLOBS for p in ROOT.glob(g) if p.is_file()}
    return sorted(p for p in files if p.relative_to(ROOT).as_posix() not in GENERATED)


# Printed data, not schedule: 2026-10-15T09:30:00+08:00, "11-06 10:24 北京时间", "2026-11-05 13:00 UTC".
DATED = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+\-]\d{2}:?\d{2})?"
                   r"|(?:\d{4}-)?\d{2}-\d{2} \d{2}:\d{2} (?:UTC|北京时间)")
_CN_HOUR = r"(?:二十[一二三]?|十[一二三四五六七八九]?)"          # 10-23 in Chinese numerals: rarely "N 项"
_CN_SMALL = r"[两三四五六七八九]"                              # 2-9: a time only with a time word after it
_AFTER_CN = r"(?:半|[一三]刻|[一二三四五]?十?[一二三四五六七八九]?分|整|钟|前|后|开始|集合|准时|左右| ?[A-Za-z])"
CLOCK = re.compile(
    r"(?<![\dA-Za-z_./+\-\[])(?<!\d:)(?:[01]?\d|2[0-3]):[0-5]\d(?![\d:])"                # 14:00, 4:40, 0:30
    r"|(?<![\dA-Za-z_.第几])(?<!第 )(?<!以下)(?<!以下 )(?<!这)(?<!这 )(?<!共)(?<!共 )"
    r"(?:1\d|2[0-3]|\d) ?点(?:半|[一三]刻|\d{1,2}(?: ?分)?)?(?![\d数])"                   # 14 点, 4点40, 5 点 Demo
    r"|(?<![一二两三四五六七八九十几第这共])(?:" + _CN_HOUR + r" ?点|" + _CN_SMALL + r" ?点" + _AFTER_CN + r")"
    r"|(?<![\dA-Za-z_.第])(?:[01]?\d|2[0-3]) ?时(?![间长候区延序效段刻])"                 # 14时, 14 时 30 分
    r"|(?<![A-Za-z0-9])\d{1,2} ?[ap]\.?m(?![A-Za-z])", re.I)                           # 2 PM, 2PM开始, 5 p.m
DAYPART = re.compile(r"上午|下午|中午|午饭|午餐|午休|午后|饭后|傍晚|晚上|早上|今天|今晚|今日|明天|明早|当天|当日"
                     r"|(?<![A-Za-z])(?:morning|afternoon|noon|evening|lunch|today|tonight|tomorrow)(?![A-Za-z])",
                     re.I)

# (file, exact text): a legitimate use. A hit is spared only when it lies inside one of these texts.
ALLOWED = {
    # 9.5: m:ss offsets inside the 3-minute Demo script, not times of day
    ("docs/09-compare.html", "<td>0:00–0:30</td>"), ("docs/09-compare.html", "<td>0:30–1:00</td>"),
    ("docs/09-compare.html", "<td>1:00–1:45</td>"), ("docs/09-compare.html", "<td>1:45–2:30</td>"),
    ("docs/09-compare.html", "<td>2:30–3:00</td>"),
}


def _norm(s):
    return unicodedata.normalize("NFKC", s)       # full-width digits and colons -> ASCII


def schedule_hits(text, allowed=()):
    """[(word, start, end)] of schedule wording in one normalized line, minus hits inside an allowed text."""
    text = DATED.sub(lambda m: " " * len(m.group(0)), _norm(text))
    spans = [(m.start(), m.end()) for a in allowed for m in re.finditer(re.escape(_norm(a)), text)]
    hits = [(m.group(0), m.start(), m.end()) for rx in (CLOCK, DAYPART) for m in rx.finditer(text)]
    return [h for h in hits if not any(s <= h[1] and h[2] <= e for s, e in spans)]


def schedule_words(text):
    return [h[0] for h in schedule_hits(text)]


def test_pattern_catches_the_old_wording_and_spares_data():
    for old in ("11:00 分组讨论材料", "今天下午要完成的 4 件事", "14:00–17:00", "16:40 前完成对比", "<b>17:00 Demo</b>",
                "上午 · 2 分钟", "下午 14:00 才创建", "At 11:00 the EC2", "in the afternoon", "14 点开始", "从昨晚22点",
                "准备17:00的 Demo", "9：30 集合", "5 点 Demo", "5点Demo", "3 点茶歇", "1 点半开始动手", "16点40",
                "4点40前完成对比", "4:40 前完成对比", "Demo 在 5:00", "两点半", "五点 Demo", "十一点分组讨论", "十四点",
                "14时开始", "14 时 30 分", "１４：００", "１４:００", "2PM开始", "约2pm", "2 PM", "5 p.m.",
                "11-07 14:00 开始动手", "2026-11-07 14:00 开始", "11-07 14:00-17:00", "时间：17:00", "当天跑通", "今日任务",
                "午后开始", "today we build", "lunch break", "evening"):
        assert schedule_words(old), old
    for data in ("2026-10-15T09:30:00+08:00", "（4.9 KB，上传于 11-06 10:24 北京时间）", "2026-11-05 13:00 UTC",
                 "实测一次 A 版 V0：30 条", "约 25 分钟", "第 10 章清理", "Python 3.12", "约 20 分钟准备 Demo",
                 "讨论结束后", "动手环节", "单条 ≤ 30 秒", "第 3 点", "第 10 点", "10 点数据", "以下 6 点注意事项",
                 "注意这 8 点", "共 10 点", "以下五点：", "注意两点。", "慢一点", "多一点时间", "24 小时", "实时判断",
                 "s[1:30]", 'replace("Z", "+00:00")', "log 10:20:15", "launches", "约 3 小时"):
        assert not schedule_words(data), data
    row = '<tr><td>0:00–0:30</td><td>问题：17:00 前讲完</td></tr>'
    assert [h[0] for h in schedule_hits(row, ["<td>0:00–0:30</td>"])] == ["17:00"]   # allowlist is per hit


def test_attendee_pages_name_no_clock_time_or_part_of_day():
    bad = []
    for p in _scanned():
        rel = p.relative_to(ROOT).as_posix()
        allowed = [t for f, t in ALLOWED if f == rel]
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for w, s, _ in schedule_hits(line, allowed):
                bad.append(f"{rel}:{i}: {w!r} in …{_norm(line)[max(0, s - 40):s + 40].strip()!r}…")
    assert not bad, "schedule wording (use 分组讨论 / 动手环节 / Demo 前 and durations instead):\n" + "\n".join(bad)


def test_every_allowlist_entry_is_still_used():
    stale = [(f, t) for f, t in sorted(ALLOWED) if _norm(t) not in _norm((ROOT / f).read_text(encoding="utf-8"))]
    assert not stale, f"remove stale ALLOWED entries: {stale}"


def test_scan_covers_the_nav_the_session_pages_and_their_sources():
    rels = {p.relative_to(ROOT).as_posix() for p in _scanned()}
    assert {"docs/assets/site.js", "docs/index.html", "docs/discussion.html", "docs/02-data.html",
            "docs/09-compare.html", "data/tools/mock_views.py", "data/tools/catalog_views.py",
            "data/templates/discussion-feedback.md", "labkit/pull_templates.py", "tools/pull-templates.sh",
            "ec2/README.md", "evaluators/README.md", "telemetry/LOG_FORMAT.md", "00-config.sh"} <= rels
    assert not rels & set(GENERATED)          # data rows are not schedule wording; generators carry their prose
    assert not any(r.startswith("docs/compare/") for r in rels)
