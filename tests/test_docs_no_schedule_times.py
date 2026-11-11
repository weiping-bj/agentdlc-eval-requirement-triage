"""Attendee-facing text never pins the workshop schedule to a clock time or a part of the day.

The on-site schedule keeps moving, so "11:00 分组讨论", "今天下午要完成", "16:40 前完成对比" or "17:00 Demo"
mislead attendees. Pages describe the order instead (分组讨论 -> 动手环节 -> Demo) and keep per-step durations
("约 25 分钟"). Data is not schedule wording and stays out of the scan: the rows of the two generated data pages
(mock-data.html, catalog.html; their prose is checked in the generators that write it), rendered compare
reports (docs/compare/), and any clock time that follows a date (ISO timestamps, "11-06 10:24" upload times).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATED = {"docs/mock-data.html": "data/tools/mock_views.py", "docs/catalog.html": "data/tools/catalog_views.py"}


def _scanned():
    files = [p for p in sorted((ROOT / "docs").glob("*.html")) if p.relative_to(ROOT).as_posix() not in GENERATED]
    files += [ROOT / f for f in GENERATED.values()]
    files += [ROOT / "docs/assets/site.js", ROOT / "README.md", ROOT / "data/README.md", ROOT / "data/HANDOFF.md",
              ROOT / "data/golden/LABELING_GUIDE.md", ROOT / "data/templates/discussion-feedback.md",
              ROOT / "data/templates/mock-data-params.yaml", ROOT / "tools/pull-templates.sh",
              ROOT / "labkit/pull_templates.py"]
    files += sorted((ROOT / "data/prompts").glob("*.md"))
    return files


# A clock time attached to a date is a data timestamp: 2026-10-15T09:30:00+08:00, 2026-11-05 13:00, 11-06 10:24.
DATED = re.compile(r"(?:\d{4}-)?\d{2}-\d{2}[ T]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+\-]\d{2}:?\d{2})?")
# Workshop clock times. Hours 0-5 are left out on purpose: they are m:ss offsets inside the 3-minute Demo script
# (09-compare.html "0:00–0:30"), never a time of day the workshop runs at.
CLOCK = re.compile(r"(?<![\dA-Za-z_.:/\-])(?:0?[6-9]|1\d|2[0-3])[:：][0-5]\d(?![\d:])"
                   r"|(?<![\dA-Za-z_.第])(?<!第 )(?:[6-9]|1\d|2[0-3]) ?点(?:半|\d{1,2} ?分)?(?![\d数])")
DAYPART = re.compile(r"上午|下午|中午|午饭|午餐|午休|傍晚|晚上|早上|今天|今晚|明天|明早"
                     r"|\b(?:morning|afternoon|lunch|tonight|tomorrow)\b|\b\d{1,2} ?[ap]\.?m\b", re.I)

# (file, exact text) for a legitimate use; keep each entry specific and say why.
ALLOWED = set()


def schedule_words(text):
    text = DATED.sub(" ", text)
    return [m.group(0) for rx in (CLOCK, DAYPART) for m in rx.finditer(text)]


def test_pattern_catches_the_old_wording_and_spares_data():
    for old in ("11:00 分组讨论材料", "今天下午要完成的 4 件事", "14:00–17:00", "16:40 前完成对比", "<b>17:00 Demo</b>",
                "上午 · 2 分钟", "下午 14:00 才创建", "At 11:00 the EC2", "in the afternoon", "14 点开始", "从昨晚22点", "准备17:00的 Demo", "9：30 集合"):
        assert schedule_words(old), old
    for data in ("2026-10-15T09:30:00+08:00", "（4.9 KB，上传于 11-06 10:24 北京时间）", "2026-11-05 13:00 UTC",
                 "<td>0:00–0:30</td>", "<td>2:30–3:00</td>", "实测一次 A 版 V0：30 条", "约 25 分钟", "第 10 章清理",
                 "Python 3.12", "约 20 分钟准备 Demo", "讨论结束后", "动手环节", "单条 ≤ 30 秒", "第 3 点", "第 10 点", "10 点数据"):
        assert not schedule_words(data), data


def test_attendee_pages_name_no_clock_time_or_part_of_day():
    bad = []
    for p in _scanned():
        rel = p.relative_to(ROOT).as_posix()
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for w in schedule_words(line):
                if not any(f == rel and t in line for f, t in ALLOWED):
                    bad.append(f"{rel}:{i}: {w!r} in {line.strip()[:120]!r}")
    assert not bad, "schedule wording (use 分组讨论 / 动手环节 / Demo 前 and durations instead):\n" + "\n".join(bad)


def test_scan_covers_the_nav_and_the_session_pages():
    rels = {p.relative_to(ROOT).as_posix() for p in _scanned()}
    assert {"docs/assets/site.js", "docs/index.html", "docs/discussion.html", "docs/02-data.html",
            "docs/09-compare.html", "data/tools/mock_views.py", "data/templates/discussion-feedback.md"} <= rels
    assert all((ROOT / r).is_file() for r in rels)
    assert not rels & set(GENERATED)          # data rows are not schedule wording; generators carry their prose
