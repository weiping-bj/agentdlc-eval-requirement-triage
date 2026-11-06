"""2.4 before 2.5: when 2.4 renumbered the mock ids (month / id_prefix), the morning CSV still has the old ids."""
import common as C
import golden_lib as G


def _renumbered(rows):
    """What generate_mock_data does to the golden set when the month changes: same suffix, new prefix."""
    out = []
    for r in rows:
        r = dict(r, feedback_id=r["feedback_id"].replace("FB-2610-", "FB-2611-"))
        r["input"] = dict(r["input"], id=r["feedback_id"])
        out.append(r)
    return out


def test_keep_rows_with_the_old_feedback_id_are_not_reported_as_changed():
    old = C.read_jsonl(C.GOLDEN_PATH)
    morning_csv = [{k: ("" if v is None else str(v)) for k, v in r.items()} for r in G.to_feedback_rows(old)]
    new, log = G.apply_feedback(_renumbered(old), morning_csv, "v2")
    assert not [line for line in log if "保留" in line and "未生效" in line], log[:3]
    assert new == _renumbered(old)


def test_add_row_pointing_at_an_id_outside_the_pool_warns():
    rows = _renumbered(C.read_jsonl(C.GOLDEN_PATH))
    pool = {r["id"].replace("FB-2610-", "FB-2611-"): dict(r, id=r["id"].replace("FB-2610-", "FB-2611-"))
            for r in C.read_jsonl(C.FEEDBACK_PATH)}
    fb = [{"golden_id": "", "feedback_id": "FB-2610-012", "操作": "新增", "是否重要": "否", "是否紧急": "否",
           "处理建议": "待评估", "五维": "质量", "样例类型": "模糊"}]
    _, log = G.apply_feedback(rows, fb, "v2", pool)
    warn = [line for line in log if "FB-2610-012" in line and "找不到" in line]
    assert len(warn) == 1 and "第 2 行" in warn[0], log


def test_no_warning_when_the_add_row_brings_its_own_text_or_the_pool_is_empty_but_text_given():
    rows = C.read_jsonl(C.GOLDEN_PATH)
    fb = [{"golden_id": "", "feedback_id": "CUST-0007", "操作": "新增", "标题": "客户条目", "需求描述": "客户自己的需求原文",
           "是否重要": "否", "是否紧急": "否", "处理建议": "待评估", "五维": "质量", "样例类型": "模糊"}]
    for pool in ({}, {r["id"]: r for r in C.read_jsonl(C.FEEDBACK_PATH)}):
        _, log = G.apply_feedback(rows, fb, "v2", pool)
        assert not [line for line in log if "找不到" in line], log
