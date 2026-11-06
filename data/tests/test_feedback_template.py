"""golden_tool.py feedback-template: regenerates the pre-filled sheet, never overwrites one that has edits."""
import csv
import io
import os
import subprocess
import sys

import common as C

PY = sys.executable
SHIPPED = os.path.join(C.DATA, "templates", "golden-set-feedback.csv")


def _run(*args):
    r = subprocess.run([PY, "-I", os.path.join(C.TOOLS, "golden_tool.py"), "feedback-template", *args],
                       capture_output=True, text=True, cwd=C.REPO)
    return r.returncode, r.stdout + r.stderr


def _rows(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def _write(path, rows):
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(buf.getvalue())


def test_regenerated_sheet_equals_the_shipped_one(tmp_path):
    out = tmp_path / "fb.csv"
    rc, _ = _run("--out", str(out))
    with open(SHIPPED, "rb") as f:
        assert rc == 0 and out.read_bytes() == f.read()     # every row 操作=保留, from the current golden set
    assert {r[2] for r in _rows(out)[1:]} == {"保留"}


def test_unchanged_sheet_is_overwritten_silently(tmp_path):
    out = tmp_path / "fb.csv"
    out.write_bytes(open(SHIPPED, "rb").read())
    assert _run("--out", str(out))[0] == 0


def _edited(tmp_path, col, value, row=1):
    rows = _rows(SHIPPED)
    rows[row][rows[0].index(col)] = value
    out = tmp_path / "fb.csv"
    _write(out, rows)
    return out, out.read_bytes()


def test_refuses_when_an_operation_or_a_label_changed(tmp_path):
    for col, value in (("操作", "修改"), ("是否紧急", "是"), ("标注理由", "现场改口径")):
        out, before = _edited(tmp_path, col, value)
        rc, msg = _run("--out", str(out))
        assert rc == 1 and "已有修改内容，未覆盖" in msg and "--force" in msg, col
        assert out.read_bytes() == before, col


def test_refuses_when_only_a_note_was_written(tmp_path):
    out, before = _edited(tmp_path, "备注", "产品和研发有分歧")
    rc, msg = _run("--out", str(out))
    assert rc == 1 and "已有修改内容，未覆盖" in msg
    assert out.read_bytes() == before


def test_force_overwrites(tmp_path):
    out, _ = _edited(tmp_path, "操作", "删除")
    assert _run("--out", str(out), "--force")[0] == 0
    assert out.read_bytes() == open(SHIPPED, "rb").read()


def test_guide_note_matches_the_command():
    with open(os.path.join(C.REPO, "docs", "02-data.html"), encoding="utf-8") as f:
        s25 = f.read().split('id="s-2-5"', 1)[1].split('id="s-2-6"', 1)[0]
    after = s25.split("golden_tool.py feedback-template</code></pre>", 1)[1]
    assert after.lstrip().startswith('<div class="note" id="s-2-5-template">')
    note = after.split("</div>", 1)[0]
    assert "已有修改内容，未覆盖" in note and "--force" in note and "不要运行" in note and 'href="#s-2-0"' in note
