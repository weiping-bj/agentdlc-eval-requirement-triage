"""可接受建议 that does not contain 处理建议 is replaced by just 处理建议: apply-feedback must say so loudly."""
import json
import subprocess
import sys

import common as C
import golden_lib as G

PY = sys.executable


def _golden():
    return C.read_jsonl(C.GOLDEN_PATH)


def test_modify_row_with_acceptable_missing_the_decision_warns():
    rows = _golden()
    g = rows[0]
    fb = [{"golden_id": g["golden_id"], "操作": "修改", "处理建议": "迭代", "可接受建议": "待评估", "标注理由": "t"}]
    new, log = G.apply_feedback(rows, fb, "v2")
    got = next(r for r in new if r["golden_id"] == g["golden_id"])
    assert got["acceptable_decisions"] == ["迭代"]          # behaviour kept: only the decision is accepted
    warn = [line for line in log if "可接受建议" in line and "警告" in line]
    assert len(warn) == 1, log
    assert "第 2 行" in warn[0] and g["golden_id"] in warn[0] and "待评估" in warn[0] and "迭代" in warn[0]


def test_add_row_with_acceptable_missing_the_decision_warns():
    fb = [{"golden_id": "", "操作": "新增", "标题": "t", "需求描述": "新增样例描述", "是否重要": "否", "是否紧急": "否",
           "处理建议": "拒绝", "可接受建议": "迭代/待评估", "拒绝要点": "a", "五维": "认知", "样例类型": "应拒绝"}]
    new, log = G.apply_feedback(_golden(), fb, "v2")
    assert new[-1]["acceptable_decisions"] == ["拒绝"]
    assert sum("可接受建议" in line and "警告" in line for line in log) == 1, log


def test_acceptable_containing_the_decision_or_left_blank_is_silent():
    rows = _golden()
    g = rows[0]
    fb = [{"golden_id": g["golden_id"], "操作": "修改", "处理建议": "待评估", "可接受建议": "待评估/迭代", "标注理由": "t"},
          {"golden_id": rows[1]["golden_id"], "操作": "修改", "处理建议": "待评估", "可接受建议": "", "标注理由": "t"}]
    new, log = G.apply_feedback(rows, fb, "v2")
    assert next(r for r in new if r["golden_id"] == g["golden_id"])["acceptable_decisions"] == ["待评估", "迭代"]
    assert not [line for line in log if "可接受建议" in line], log


def test_cli_prints_the_warning_in_the_closing_summary(tmp_path):
    rows = _golden()
    golden = tmp_path / "golden_set.jsonl"
    golden.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    csv = tmp_path / "fb.csv"
    g = rows[0]
    csv.write_text("golden_id,feedback_id,操作,处理建议,可接受建议,标注理由\n"
                   f"{g['golden_id']},{g['feedback_id']},修改,迭代,待评估,现场改口径\n", encoding="utf-8")
    r = subprocess.run([PY, "-I", f"{C.TOOLS}/golden_tool.py", "--golden", str(golden),
                        "apply-feedback", "--feedback", str(csv), "--dry-run"], capture_output=True, text=True, cwd=C.REPO)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out                      # a warning, not an error: the row is still applied
    lines = out.splitlines()
    bang = [i for i, line in enumerate(lines) if line.startswith("!" * 20)]
    assert len(bang) >= 2, out                         # repeated in a loud block at the end, not only inline
    block = "\n".join(lines[bang[-2]:bang[-1] + 1])
    assert "可接受建议" in block and g["golden_id"] in block and "待评估" in block and "第 2 行" in block, out
