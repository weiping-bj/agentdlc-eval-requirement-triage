"""Tests for data/tools: mock generation, golden set tooling, customer-data ingest."""
import csv
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

import common as C
import generate_mock_data as GM
import golden_lib as G
import golden_tool as GT
import ingest_customer_data as ING

PY = sys.executable


def _run(*args):
    return subprocess.run([PY, "-I", *args], capture_output=True, text=True, cwd=C.REPO)


# ---------------------------------------------------------------- committed data sanity
def test_pool_and_feedback_shape():
    pool = C.read_jsonl(C.POOL_PATH)
    assert len(pool) == 60
    assert len({r["id"] for r in pool}) == 60
    gids = sorted(r["_meta"]["golden_id"] for r in pool if r["_meta"]["golden_id"])
    assert gids == [f"G{i:02d}" for i in range(1, 31)]
    fb = C.read_jsonl(C.FEEDBACK_PATH)
    assert len(fb) == 60
    assert all("_meta" not in r for r in fb), "agent input must not leak labels"
    for r in fb:
        for f in C.REQUIRED_FIELDS:
            assert r.get(f), (r["id"], f)
        assert set(r) <= set(C.INPUT_FIELDS)


def test_mock_data_is_synthetic_and_pii_free():
    texts = open(C.FEEDBACK_PATH, encoding="utf-8").read() + open(C.GOLDEN_PATH, encoding="utf-8").read()
    for kind, rx in ING.PII.items():
        assert not rx.search(texts), f"{kind} found in mock data"
    for r in C.read_jsonl(C.FEEDBACK_PATH):
        assert r["customer"].endswith("（示例）")


def test_similar_request_clusters_have_three_distinct_customers():
    fb = C.read_jsonl(C.FEEDBACK_PATH)
    for kws in (("批量",), ("推送", "推到"), ("离线", "没信号", "没有信号")):
        custs = {r["customer"] for r in fb if any(k in r["title"] + r["content"] for k in kws)}
        assert len(custs) >= 3, kw


def test_golden_matches_discussion_examples():
    gold = {r["golden_id"]: r for r in C.read_jsonl(C.GOLDEN_PATH)}
    want = {"G01": ("是", "否", "迭代"), "G02": ("否", "否", "拒绝"), "G03": ("是", "是", "迭代"),
            "G04": ("否", "否", "拒绝"), "G05": ("是", "否", "待评估")}
    for gid, (imp, urg, dec) in want.items():
        e = gold[gid]["expected"]
        assert (e["important"], e["urgent"], e["decision"]) == (imp, urg, dec), gid


def test_golden_validates_and_catalog_refs_exist():
    rows = C.read_jsonl(C.GOLDEN_PATH)
    errors, _ = G.validate(rows, "binary", GT._catalog_ids())
    assert errors == []
    _, warns = G.validate(rows, "binary", GT._catalog_ids())
    assert not any("引用了能力目录里不存在" in w for w in warns)
    st = G.stats(rows)
    assert st["n"] == 30 and all(st["decision"][d] >= 3 for d in C.DECISIONS)
    assert all(st["dimensions"].get(d, 0) >= 2 for d in C.DIMENSIONS)


def test_catalog_shape():
    doc = json.load(open(C.CATALOG_PATH, encoding="utf-8"))
    caps = doc["capabilities"]
    ids = [c["id"] for c in caps]
    assert len(ids) == len(set(ids))
    assert {c["status"] for c in caps} <= {"已支持", "部分支持", "规划中", "明确不做"}
    assert any(c["status"] == "明确不做" for c in caps)
    for c in caps:
        assert c["name"] and c["description"] and isinstance(c["keywords"], list)


def test_schemas_are_valid_json():
    for n in ("feedback_item", "agent_output", "golden_item"):
        json.load(open(os.path.join(C.DATA, "schema", f"{n}.schema.json"), encoding="utf-8"))


# ---------------------------------------------------------------- generator
def test_default_params_reproduce_committed_files(tmp_path):
    out = tmp_path / "fb.jsonl"
    gold = tmp_path / "g.jsonl"
    rc = GM.main(["--out", str(out), "--write-golden", "--golden-out", str(gold)])
    assert rc == 0
    assert out.read_text(encoding="utf-8") == open(C.FEEDBACK_PATH, encoding="utf-8").read()
    committed = C.read_jsonl(C.GOLDEN_PATH)
    if {r.get("version") for r in committed} == {"v1"} and all(r.get("source") == "synthetic" for r in committed):
        assert C.read_jsonl(str(gold)) == committed
    else:
        pytest.skip("golden set was edited on site (version != v1); reproduction check not applicable")


def test_yaml_subset_parser_matches_pyyaml():
    path = os.path.join(C.templates_dir(), "mock-data-params.yaml")
    yaml = pytest.importorskip("yaml")
    expected = yaml.safe_load(open(path, encoding="utf-8"))
    import builtins
    real_import = builtins.__import__

    def no_yaml(name, *a, **k):
        if name == "yaml":
            raise ImportError
        return real_import(name, *a, **k)
    builtins.__import__ = no_yaml
    try:
        got = C.load_yaml(path)
    finally:
        builtins.__import__ = real_import
    assert got == expected


def test_yaml_subset_nested(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("count: 40  # c\nmonth: \"2026-11\"\ncategory_mix:\n  gap_generic: 10\n  incident: 5\nindustries:\n  - 制造\n  - 快消\nflag: true\n", encoding="utf-8")
    import builtins
    real_import = builtins.__import__
    builtins.__import__ = lambda n, *a, **k: (_ for _ in ()).throw(ImportError) if n == "yaml" else real_import(n, *a, **k)
    try:
        d = C.load_yaml(str(p))
    finally:
        builtins.__import__ = real_import
    assert d == {"count": 40, "month": "2026-11", "category_mix": {"gap_generic": 10, "incident": 5},
                 "industries": ["制造", "快消"], "flag": True}


def test_category_mix_month_and_overrides():
    pool = C.read_jsonl(C.POOL_PATH)
    items, warns = GM.build({"count": 40, "month": "2026-11", "category_mix": {"gap_generic": 20, "incident": 20},
                             "industries": ["制造"], "seed": 1}, pool, [])
    cats = {r["_meta"]["category"] for r in items}
    assert {"gap_generic", "incident"} <= cats
    assert any("种子库只有" in w for w in warns)  # incident shortfall reported
    assert any("强制保留" in w for w in warns)  # golden items kept regardless of mix
    assert all(r["id"].startswith("FB-2611-") and r["submitted_at"].startswith("2026-11-") for r in items)
    assert {r["industry"] for r in items} == {"制造"}
    assert len({r["id"] for r in items}) == len(items)
    again, _ = GM.build({"count": 40, "month": "2026-11", "category_mix": {"gap_generic": 20, "incident": 20},
                         "industries": ["制造"], "seed": 1}, pool, [])
    assert [r["id"] for r in again] == [r["id"] for r in items]


def test_extra_items_file_is_used(tmp_path):
    extra = tmp_path / "extra.jsonl"
    shutil.copyfile(os.path.join(C.DATA, "templates", "extra-feedback.example.jsonl"), extra)
    params = tmp_path / "p.yaml"
    params.write_text(f"count: 61\nextra_items_file: {extra}\n", encoding="utf-8")
    out = tmp_path / "fb.jsonl"
    assert GM.main(["--params", str(params), "--out", str(out)]) == 0
    rows = C.read_jsonl(str(out))
    assert len(rows) == 61 and any(r["customer"] == "示例新客户（示例）" for r in rows)


def test_extra_items_without_meta_rejected(tmp_path):
    extra = tmp_path / "extra.jsonl"
    extra.write_text(json.dumps({"id": "X", "title": "t", "content": "c"}, ensure_ascii=False) + "\n", encoding="utf-8")
    params = tmp_path / "p.yaml"
    params.write_text(f"extra_items_file: {extra}\n", encoding="utf-8")
    assert GM.main(["--params", str(params), "--out", str(tmp_path / "o.jsonl")]) == 2


def test_write_golden_refuses_overwrite_without_force(tmp_path, capsys):
    g = tmp_path / "g.jsonl"
    g.write_text("{}\n", encoding="utf-8")
    GM.main(["--out", str(tmp_path / "o.jsonl"), "--write-golden", "--golden-out", str(g)])
    assert g.read_text(encoding="utf-8") == "{}\n"
    assert "未覆盖" in capsys.readouterr().out


# ---------------------------------------------------------------- golden feedback workflow
def _golden():
    return C.read_jsonl(C.GOLDEN_PATH)


def test_feedback_template_all_keep_is_identity():
    rows = _golden()
    fb = [{k: ("" if v is None else str(v)) for k, v in r.items()} for r in G.to_feedback_rows(rows)]
    new, log = G.apply_feedback(rows, fb, "v2")
    assert new == rows and log == []


def test_apply_feedback_modify_delete_add():
    rows = _golden()
    fb = [
        {"golden_id": "G01", "操作": "修改", "是否紧急": "是", "标注理由": "现场：运营月底集中改字段"},
        {"golden_id": "G02", "操作": "删除"},
        {"golden_id": "", "feedback_id": "FB-2610-012", "操作": "新增", "是否重要": "否", "是否紧急": "否",
         "处理建议": "待评估", "五维": "质量", "样例类型": "模糊"},
        {"golden_id": "", "操作": "新增", "标题": "希望支持语音录入拜访", "需求描述": "外勤希望用语音直接录入拜访记录",
         "是否重要": "否", "是否紧急": "否", "处理建议": "拒绝", "拒绝要点": "已支持；见能力目录", "话术要点": "给用法",
         "五维": "认知/质量", "样例类型": "应拒绝"},
        {"golden_id": "G03", "操作": "胡写"},
    ]
    pool = {r["id"]: r for r in C.read_jsonl(C.FEEDBACK_PATH)}
    new, log = G.apply_feedback(rows, fb, "v2", pool)
    by = {r["golden_id"]: r for r in new}
    assert "G02" not in by
    assert by["G01"]["expected"]["urgent"] == "是" and by["G01"]["version"] == "v2"
    added = [r for r in new if r["golden_id"] not in {x["golden_id"] for x in rows}]
    assert len(added) == 2
    a1 = next(r for r in added if r["feedback_id"] == "FB-2610-012")
    assert a1["input"]["title"] == pool["FB-2610-012"]["title"]
    a2 = next(r for r in added if r["feedback_id"].startswith("FB-NEW-"))
    assert a2["reject_points"] == ["已支持", "见能力目录"] and a2["dimensions"] == ["认知", "质量"]
    assert any("未知操作" in l for l in log)
    errors, _ = G.validate(new)
    assert errors == []


def test_validation_catches_contradictions():
    rows = _golden()
    g03 = next(r for r in rows if r["golden_id"] == "G03")
    bad = json.loads(json.dumps(g03, ensure_ascii=False))
    bad["expected"]["decision"] = "拒绝"
    bad["acceptable_decisions"] = ["拒绝"]
    bad2 = json.loads(json.dumps(rows[0], ensure_ascii=False))
    bad2["expected"]["important"] = "maybe"
    errors, _ = G.validate([bad, bad2, bad2])
    joined = "\n".join(errors)
    assert "不可自动拒绝" in joined and "reject_points" in joined and "是/否" in joined and "重复" in joined
    errors, _ = G.validate([dict(rows[0], expected=dict(rows[0]["expected"], level=None))], scheme="level")
    assert any("分级模式" in e for e in errors)


def test_golden_tool_cli_end_to_end(tmp_path):
    g = tmp_path / "golden_set.jsonl"
    shutil.copyfile(C.GOLDEN_PATH, g)
    fbcsv = tmp_path / "fb.csv"
    rows = G.to_feedback_rows(C.read_jsonl(str(g)))
    rows[0]["操作"] = "修改"
    rows[0]["处理建议"] = "待评估"
    C.write_csv(str(fbcsv), rows, G.FEEDBACK_CSV_FIELDS)
    r = _run("data/tools/golden_tool.py", "--golden", str(g), "apply-feedback", "--feedback", str(fbcsv))
    assert r.returncode == 0, r.stdout + r.stderr
    new = C.read_jsonl(str(g))
    assert new[0]["expected"]["decision"] == "待评估" and new[0]["acceptable_decisions"] == ["待评估"]
    assert new[0]["version"] == "v2"
    assert (tmp_path / "golden_set.csv").exists() and (tmp_path / "agentcore_dataset.json").exists()
    assert list((tmp_path / "history").iterdir())
    r = _run("data/tools/golden_tool.py", "--golden", str(g), "validate")
    assert r.returncode == 0
    r = _run("data/tools/golden_tool.py", "--golden", str(g), "fill-levels")
    assert r.returncode == 0 and all(x["expected"]["level"] in C.LEVELS for x in C.read_jsonl(str(g)))


def test_golden_tool_apply_refuses_invalid(tmp_path):
    g = tmp_path / "golden_set.jsonl"
    shutil.copyfile(C.GOLDEN_PATH, g)
    before = g.read_text(encoding="utf-8")
    fbcsv = tmp_path / "fb.csv"
    C.write_csv(str(fbcsv), [{"golden_id": "G03", "操作": "修改", "处理建议": "拒绝"}], G.FEEDBACK_CSV_FIELDS)
    r = _run("data/tools/golden_tool.py", "--golden", str(g), "apply-feedback", "--feedback", str(fbcsv))
    assert r.returncode == 1 and "未写入" in r.stdout
    assert g.read_text(encoding="utf-8") == before


def test_agentcore_dataset_projection():
    ds = G.to_agentcore_dataset(_golden())
    assert len(ds["scenarios"]) == 30
    s = ds["scenarios"][0]
    assert s["turns"][0]["input"].startswith("需求编号: ")
    exp = json.loads(s["turns"][0]["expected_response"])
    assert {"important", "urgent", "decision", "acceptable_decisions"} <= set(exp)


# ---------------------------------------------------------------- ingest
def _write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def test_ingest_csv_maps_masks_and_reports(tmp_path):
    src = tmp_path / "cust.csv"
    rows = []
    for i in range(35):
        rows.append([f"R{i}", f"需求{i}", f"客户反馈第{i}条，联系 13812345678 或 a{i}@example.com，审批报错无法提交", f"客户{i % 7}",
                     "重点" if i % 2 else "S", "流程", "2026/10/%d" % (i % 28 + 1), "bug" if i % 3 == 0 else "需求",
                     "是" if i % 2 else "否", "否", "拒绝" if i % 4 == 0 else "迭代", "多余列"])
    _write_csv(src, ["需求编号", "需求标题", "需求描述", "客户名称", "客户等级", "模块", "提交时间", "需求类型",
                     "是否重要", "是否紧急", "处理建议", "备注说明"], rows)
    out = tmp_path / "out"
    assert ING.main(["--in", str(src), "--out-dir", str(out)]) == 0
    items = C.read_jsonl(str(out / "feedback.normalized.jsonl"))
    assert len(items) == 35
    it = items[0]
    assert it["id"] == "R0" and it["submitted_at"] == "2026-10-01" and it["customer_tier"] == "战略"
    assert it["feedback_type"] == "功能缺陷" and items[1]["feedback_type"] == "新需求" and items[1]["customer_tier"] == "KA"
    assert "13812345678" not in it["content"] and "@example.com" in it["content"] and "a0@" not in it["content"]
    assert it["extra"] == {"备注说明": "多余列"}
    rep = (out / "report.md").read_text(encoding="utf-8")
    assert "充足" in rep and "手机号" in rep
    cands = C.read_jsonl(str(out / "golden_candidates.jsonl"))
    assert len(cands) == 35 and cands[0]["expected"]["decision"] == "拒绝" and cands[0]["complete"]


def test_ingest_insufficient_and_unmappable(tmp_path):
    src = tmp_path / "thin.json"
    src.write_text(json.dumps({"items": [{"反馈正文": "太慢"}, {"反馈正文": "不好用"}]}, ensure_ascii=False), encoding="utf-8")
    assert ING.main(["--in", str(src), "--out-dir", str(tmp_path / "o")]) == 2  # no title/content column
    m = tmp_path / "m.json"
    m.write_text(json.dumps({"反馈正文": "content"}, ensure_ascii=False), encoding="utf-8")
    assert ING.main(["--in", str(src), "--mapping", str(m), "--out-dir", str(tmp_path / "o"), "--strict"]) == 1
    rep = (tmp_path / "o" / "report.md").read_text(encoding="utf-8")
    assert "不足" in rep
    items = C.read_jsonl(str(tmp_path / "o" / "feedback.normalized.jsonl"))
    assert items[0]["id"] == "CUST-0001" and items[0]["title"] == "太慢"


def test_ingest_xlsx(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["编号", "标题", "详情", "创建时间"])
    ws.append(["X1", "批量编辑", "希望批量编辑自定义字段", "2026-10-05"])
    ws.append([None, None, None, None])
    ws.append(["X2", "离线", "希望离线录入", 46300])
    p = tmp_path / "c.xlsx"
    wb.save(p)
    rows = C.read_xlsx(str(p))
    assert len(rows) == 2 and rows[0]["标题"] == "批量编辑"
    assert ING.main(["--in", str(p), "--out-dir", str(tmp_path / "o")]) == 0
    items = C.read_jsonl(str(tmp_path / "o" / "feedback.normalized.jsonl"))
    assert items[1]["submitted_at"] == "2026-10-05"  # Excel serial 46300


def test_norm_helpers():
    assert C.norm_date("2026年10月3日") == "2026-10-03"
    assert C.norm_date("20261003") == "2026-10-03"
    assert C.norm_date("bad") is None
    assert C.norm_decision("已排期开发") == "迭代" and C.norm_decision("需技术评估") == "待评估" and C.norm_decision("驳回") == "拒绝"
    assert C.norm_yes_no(True) == "是" and C.norm_yes_no("No") == "否" and C.norm_yes_no("?") is None
    assert C.split_list("多组织/分子公司；第二点") == ["多组织/分子公司", "第二点"]
    assert C.split_list("认知/质量", C.TAG_SEPS) == ["认知", "质量"]
