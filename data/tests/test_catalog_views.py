"""docs/catalog.html and data/capability_catalog.csv must match data/capability_catalog.json."""
import collections
import csv
import html
import io
import json
import os
import re
import subprocess
import sys

import common as C
import catalog_views as CV

PY = sys.executable


def _catalog():
    return CV.load(C.CATALOG_PATH)


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def test_committed_views_match_the_json():
    for path, content in CV.planned().items():
        assert CV.same(_read(path), content), f"{os.path.relpath(path, C.REPO)} 过期：运行 python3 evaluators/sync_shared.py"


def test_crlf_checkout_is_not_drift_but_a_content_change_is(tmp_path):
    f = tmp_path / "v.html"
    f.write_bytes(b"a\r\nb\r\n")                       # what git core.autocrlf leaves on Windows
    assert CV.sync({str(f): b"a\nb\n"}, check=True) == []
    assert CV.sync({str(f): b"a\nc\n"}, check=True) == [str(f)]
    assert f.read_bytes() == b"a\r\nb\r\n"               # --check never writes


def test_csv_rows_match_every_capability_field():
    raw = _read(CV.CSV_PATH)
    assert raw.startswith(b"\xef\xbb\xbf"), "Excel needs the UTF-8 BOM"
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline="")))
    assert rows[0] == [h for _, h in CV.CSV_COLUMNS]
    caps = _catalog()["capabilities"]
    assert len(rows) - 1 == len(caps)
    for cap, row in zip(caps, rows[1:]):
        for (key, _), cell in zip(CV.CSV_COLUMNS, row):
            want = cap.get(key)
            want = "、".join(want) if isinstance(want, list) else ("" if want is None else str(want))
            assert cell == C._escape_cell(want), (cap["id"], key)


def test_html_lists_every_capability_under_its_status():
    page = _read(CV.HTML_PATH).decode("utf-8")
    cat = _catalog()
    counts = collections.Counter(c["status"] for c in cat["capabilities"])
    assert f"共 {len(cat['capabilities'])} 项" in page
    for status, n in counts.items():
        assert f"{status} {n} 项</span>" in page          # summary chip
        assert f"<h3>{status}（{n} 项）</h3>" in page        # group heading
    # each row sits in the section of its own status, with every displayed field escaped
    sections = re.split(r'<div class="step" id="', page)
    for cap in cat["capabilities"]:
        sec = next(s for s in sections if f'<tr id="{cap["id"]}">' in s)
        assert f"<h3>{cap['status']}（" in sec, cap["id"]
        row = sec.split(f'<tr id="{cap["id"]}">', 1)[1].split("</tr>", 1)[0]
        for key, _ in CV.HTML_COLUMNS:
            assert html.escape(str(cap.get(key) or ""), quote=True) in row, (cap["id"], key)
    assert html.escape(cat["platform"]["name"]) in page
    for p in cat["platform"]["principles"]:
        assert html.escape(p) in page


def test_untrusted_text_is_escaped_and_formula_guarded(tmp_path):
    cat = {"version": "t", "platform": {"name": "<b>X</b>", "positioning": "p", "principles": ["a&b"]},
           "capabilities": [{"id": "CAP-900", "name": "<script>alert(1)</script>", "module": "m", "status": "奇怪状态",
                             "description": "=HYPERLINK(\"x\")", "limits": None, "workaround": "", "how_to": "",
                             "keywords": ["k1", "k2"]}]}
    page = CV.render_html(cat)
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<b>X</b>" not in page and "a&amp;b" in page
    anchor = CV._style("奇怪状态")[1]
    assert f'id="{anchor}"' in page and f'href="#{anchor}"' in page and "奇怪状态 1 项" in page  # own, stable group
    row = list(csv.reader(io.StringIO(CV.render_csv(cat).decode("utf-8-sig"), newline="")))[1]
    assert row[4].startswith("'=") and row[9] == "k1、k2" and row[5] == ""


def test_check_flags_drift_and_regeneration_fixes_it(tmp_path):
    src = tmp_path / "catalog.json"
    cat = _catalog()
    src.write_text(json.dumps(cat, ensure_ascii=False), encoding="utf-8")
    out = ["--catalog", str(src), "--html", str(tmp_path / "c.html"), "--csv", str(tmp_path / "c.csv")]
    assert CV.main(out + ["--check"]) == 1          # missing files are stale
    assert CV.main(out) == 0 and CV.main(out + ["--check"]) == 0
    cat["capabilities"][0]["status"] = "明确不做"
    src.write_text(json.dumps(cat, ensure_ascii=False), encoding="utf-8")
    assert CV.main(out + ["--check"]) == 1
    assert CV.main(out) == 0 and CV.main(out + ["--check"]) == 0
    assert "明确不做 8 项" in (tmp_path / "c.html").read_text(encoding="utf-8")


def test_sync_shared_regenerates_and_checks_the_views(tmp_path):
    sys.path.insert(0, os.path.join(C.REPO, "evaluators"))
    import sync_shared
    files = sync_shared.planned(C.GOLDEN_PATH, C.CATALOG_PATH)
    assert {CV.HTML_PATH, CV.CSV_PATH} <= set(files)
    # a --catalog other than the repo one (e.g. customer data) feeds the evaluators, never the published views
    other = _catalog()
    other["capabilities"] = other["capabilities"][:1]
    alt = tmp_path / "customer_catalog.json"
    alt.write_text(json.dumps(other, ensure_ascii=False), encoding="utf-8")
    alt_files = sync_shared.planned(C.GOLDEN_PATH, str(alt))
    assert alt_files[CV.HTML_PATH] == files[CV.HTML_PATH] and alt_files[CV.CSV_PATH] == files[CV.CSV_PATH]
    r = subprocess.run([PY, "-I", os.path.join(C.REPO, "data", "tools", "catalog_views.py"), "--check"],
                       capture_output=True, text=True, cwd=C.REPO)
    assert r.returncode == 0, r.stdout


def test_guide_links_the_catalog_views():
    disc = _read(os.path.join(C.REPO, "docs", "discussion.html")).decode("utf-8")
    assert '<a href="catalog.html">' in disc and 'data-download="data/capability_catalog.csv"' in disc
    tpl = _read(os.path.join(C.DATA, "templates", "discussion-feedback.md")).decode("utf-8")
    sec3 = tpl.split("## 3.", 1)[1].split("## 4.", 1)[0]
    assert "/catalog.html)" in sec3 and "/blob/main/data/capability_catalog.csv)" in sec3   # absolute: works in a downloaded copy
    assert "04-update-capability-catalog.md" not in sec3     # the prompt is for the coding agent, not the customer
    site = _read(os.path.join(C.REPO, "docs", "assets", "site.js")).decode("utf-8")
    assert 'file: "catalog.html"' in site
    # TOC entries are read back with textContent (un-escaped) and must be escaped again before innerHTML
    assert "esc(h.textContent)" in site and "esc(n.textContent)" in site
