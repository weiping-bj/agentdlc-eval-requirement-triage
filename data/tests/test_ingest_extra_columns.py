"""Customer columns our schema lacks: report.md lists them loudly; --append-to-content puts chosen ones into 需求描述."""
import csv
import os
import sys
import zipfile

import common as C
import ingest_customer_data as ING

sys.path.insert(0, os.path.join(C.REPO, "agent"))


def _csv(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _src(tmp_path):
    p = tmp_path / "cust.csv"
    _csv(p, ["需求编号", "需求标题", "需求描述", "客户名称", "影响用户数", "所属区域", "空列"],
         [["R1", "批量编辑", "希望批量编辑自定义字段", "甲（示例）", "500", "华东", ""],
          ["R2", "离线录入", "外勤希望离线录入拜访", "乙（示例）", "", "华南", ""],
          ["R3", "导出", "导出报表太慢，联系 13812345678", "丙（示例）", "1200", "", ""]])
    return p


def test_report_lists_every_unused_column_with_counts(tmp_path):
    out = tmp_path / "o"
    assert ING.main(["--in", str(_src(tmp_path)), "--out-dir", str(out)]) == 0
    rep = (out / "report.md").read_text(encoding="utf-8")
    assert "这些列没有用上" in rep
    sec = rep.split("这些列没有用上", 1)[1].split("\n## ", 1)[0]
    assert "| 影响用户数 | 2 |" in sec and "| 所属区域 | 2 |" in sec and "| 空列 | 0 |" in sec
    names = [line.split("|")[1].strip() for line in sec.splitlines() if line.startswith("| ") and "---" not in line][1:]
    assert names == ["影响用户数", "所属区域", "空列"]                   # exactly the unmapped ones, in column order
    assert "Agent 不会读" in sec and "--append-to-content" in sec
    items = C.read_jsonl(str(out / "feedback.normalized.jsonl"))
    assert items[0]["extra"] == {"影响用户数": "500", "所属区域": "华东"}  # still kept in the file


def test_report_says_so_when_every_column_is_used(tmp_path):
    p = tmp_path / "c.csv"
    _csv(p, ["需求标题", "需求描述"], [["a", "需求描述一"]])
    assert ING.main(["--in", str(p), "--out-dir", str(tmp_path / "o")]) == 0
    rep = (tmp_path / "o" / "report.md").read_text(encoding="utf-8")
    assert "这些列没有用上" not in rep and "所有列都用上了" in rep


def test_append_to_content_puts_chosen_columns_into_the_description(tmp_path):
    out = tmp_path / "o"
    assert ING.main(["--in", str(_src(tmp_path)), "--out-dir", str(out), "--append-to-content", "影响用户数，所属区域"]) == 0
    items = C.read_jsonl(str(out / "feedback.normalized.jsonl"))
    assert items[0]["content"] == "希望批量编辑自定义字段\n影响用户数: 500\n所属区域: 华东"
    assert items[1]["content"] == "外勤希望离线录入拜访\n所属区域: 华南"           # empty values are skipped
    assert "extra" not in items[0] and "13812345678" not in items[2]["content"]
    rep = (out / "report.md").read_text(encoding="utf-8")
    assert "已追加到需求描述：影响用户数、所属区域" in rep
    sec = rep.split("这些列没有用上", 1)[1].split("\n## ", 1)[0]
    assert "影响用户数" not in sec and "| 空列 | 0 |" in sec
    from s12agent import core
    assert "影响用户数: 500" in core.format_input(items[0])                # the agent now sees it


def test_append_to_content_rejects_unknown_or_mapped_columns(tmp_path, capsys):
    src = _src(tmp_path)
    assert ING.main(["--in", str(src), "--out-dir", str(tmp_path / "o"), "--append-to-content", "影响人数"]) == 2
    assert "影响人数" in capsys.readouterr().out
    assert ING.main(["--in", str(src), "--out-dir", str(tmp_path / "o"), "--append-to-content", "客户名称"]) == 2
    assert "已经对应到" in capsys.readouterr().out


def _two_sheet_xlsx(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   '<sheet name="说明" sheetId="1" r:id="rId1"/><sheet name="需求明细" sheetId="2" r:id="rId2"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/>'
                   '<Relationship Id="rId2" Target="worksheets/sheet2.xml" Type="x"/></Relationships>')
        row = lambda *cells: "<row>" + "".join(f'<c t="inlineStr"><is><t>{c}</t></is></c>' for c in cells) + "</row>"  # noqa: E731
        z.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   f"<sheetData>{row('说明')}{row('本表为导出说明')}</sheetData></worksheet>")
        z.writestr("xl/worksheets/sheet2.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   f"<sheetData>{row('需求标题', '需求描述')}{row('导出', '导出报表太慢')}</sheetData></worksheet>")


def test_sheet_by_name_and_a_wrong_name_is_an_error_not_the_first_sheet(tmp_path, capsys):
    p = tmp_path / "c.xlsx"
    _two_sheet_xlsx(p)
    assert C.read_xlsx(str(p), "需求明细") == [{"需求标题": "导出", "需求描述": "导出报表太慢"}]
    assert ING.main(["--in", str(p), "--sheet", "需求明细", "--out-dir", str(tmp_path / "o")]) == 0
    assert ING.main(["--in", str(p), "--sheet", "需求", "--out-dir", str(tmp_path / "o2")]) == 2
    msg = capsys.readouterr().out
    assert "需求" in msg and "需求明细" in msg and "说明" in msg                       # lists the real sheet names


def test_guide_2_3_examples_are_true():
    with open(os.path.join(C.REPO, "docs", "02-data.html"), encoding="utf-8") as f:
        s23 = f.read().split('id="s-2-3"', 1)[1].split('id="s-2-4"', 1)[0]
    examples = {"问题描述": "content", "详情": "content", "客户名称": "customer", "租户": "customer",
                "客户级别": "customer_tier", "创建时间": "submitted_at", "提交日期": "submitted_at"}
    for col, field in examples.items():
        assert f"“{col}”" in s23, col
        assert ING.auto_map([col]) == {col: field}, col
    assert len(C.INPUT_FIELDS) == 11 and C.REQUIRED_FIELDS == ["id", "title", "content"]
    assert all(f"<code>{f}</code>" in s23 for f in C.INPUT_FIELDS)
    assert "01-assess-customer-data.md" in s23.split('id="s-2-3-manual"', 1)[0]      # recommended path comes first
    assert '--append-to-content "影响用户数,所属区域"' in s23 and "Agent 不读这些列" in s23
    with open(os.path.join(C.DATA, "prompts", "01-assess-customer-data.md"), encoding="utf-8") as f:
        prompt = f.read()
    assert "mapping.json" in prompt and "--sheet" in prompt and "--append-to-content" in prompt and "这些列没有用上" in prompt
