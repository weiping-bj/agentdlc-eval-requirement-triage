"""golden-set-feedback.csv 操作 column: aliases and the loud unknown-op summary."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "data" / "tools"))

import golden_lib as G  # noqa: E402


@pytest.mark.parametrize("text,op", [
    ("保留", "keep"), ("", "keep"), (None, "keep"), ("修改", "modify"), ("修改标签", "modify"),
    (" 修改标签 ", "modify"), ("修改标签。", "modify"), ("　修改　", "modify"), ("改标签", "modify"),
    ("删除", "delete"), ("删除样例", "delete"), ("新增", "add"), ("新增样例", "add"), ("新增 样例", "add"),
    ("新增用例", "add"), ("Modify", "modify"), ("ADD", "add"),
])
def test_known_ops_and_aliases(text, op):
    assert G.normalize_op(text) == op


@pytest.mark.parametrize("text", ["改一下", "待定", "x", "删掉吧"])
def test_unknown_ops(text):
    assert G.normalize_op(text) is None


def _golden():
    return [json.loads(x) for x in (ROOT / "data" / "golden" / "golden_set.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]


def test_alias_rows_are_applied_not_skipped():
    rows = _golden()
    g = rows[0]
    flip = "否" if g["expected"]["important"] == "是" else "是"
    fb = [{"golden_id": g["golden_id"], "feedback_id": g["feedback_id"], "操作": "修改标签", "是否重要": flip},
          {"golden_id": "", "feedback_id": "", "操作": "新增样例", "是否重要": "是", "是否紧急": "否", "处理建议": "迭代",
           "标题": "测试新增", "需求描述": "新增样例别名测试", "备注": "评审备注不进入输入"}]
    new_rows, log = G.apply_feedback(rows, fb, "vtest", None)
    assert not any("未知操作" in x for x in log), log
    assert next(r for r in new_rows if r["golden_id"] == g["golden_id"])["expected"]["important"] == flip
    added = [r for r in new_rows if r["input"].get("title") == "测试新增"]
    assert len(added) == 1 and "备注" not in json.dumps(added[0]["input"], ensure_ascii=False)
    assert G.unknown_ops(fb) == []


def test_apply_feedback_cli_prints_loud_summary_and_refuses(tmp_path):
    rows = _golden()
    golden = tmp_path / "golden_set.jsonl"
    golden.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    csv = tmp_path / "fb.csv"
    csv.write_text("golden_id,feedback_id,操作,是否重要\n"
                   f"{rows[0]['golden_id']},{rows[0]['feedback_id']},改一下,否\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "data" / "tools" / "golden_tool.py"), "--golden", str(golden),
                        "apply-feedback", "--feedback", str(csv), "--dry-run"],
                       capture_output=True, text=True, cwd=ROOT)
    out = r.stdout + r.stderr
    assert "无法识别" in out and "改一下" in out and "没有生效" in out
    assert r.returncode == 1  # unknown ops are an error, also in dry-run
    r = subprocess.run([sys.executable, str(ROOT / "data" / "tools" / "golden_tool.py"), "--golden", str(golden),
                        "apply-feedback", "--feedback", str(csv)], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 1 and "未写入" in r.stdout
    assert golden.read_text(encoding="utf-8").count("\n") == len(rows)
