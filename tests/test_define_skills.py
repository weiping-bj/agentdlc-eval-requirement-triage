"""Define ring skills: adlc-fishbone (drawio generator), labkit.define_pages (self-contained previews + fail-closed
publish) and adlc-criteria (criterion rules, coverage map, golden additions through the existing tools).
Offline: S3 is the stub from test_data_pages, no AWS call."""
import hashlib
import html
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from labkit import aws  # noqa: E402
from labkit import define_pages as DP  # noqa: E402
from labkit import publish as P  # noqa: E402
from labkit.data_pages import external_refs  # noqa: E402

import test_data_pages as TD  # noqa: E402  (FakeS3 stub)

FISH_DIR = ROOT / ".claude" / "skills" / "adlc-fishbone"
CRIT_DIR = ROOT / ".claude" / "skills" / "adlc-criteria"
TEMPLATE = FISH_DIR / "assets" / "fishbone-template.drawio"
TEMPLATE_SHA256 = "e9aea9d85196e8c82457d7ce41de36f80e6b780c134ea313cae5d3ad32e03a93"  # v-Team template, unmodified
EVIL = '<script>alert(1)</script><img src=x onerror=alert(2)>"\'><a href="https://evil.example/">x</a> & {{x}}'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GF = _load("adlc_generate_fishbone_t", FISH_DIR / "scripts" / "generate_fishbone.py")
CR = _load("adlc_criteria_t", CRIT_DIR / "scripts" / "criteria.py")


def sample(**over):
    d = {"schema_version": "1.0", "status": "approved_for_render",
         "metadata": {"customer": "示例 CRM PaaS 产品部（第9组扮演）", "date": "2026-11-20", "use_case": "需求反馈分诊 Agent",
                      "service_target": "对内（产品经理、客户成功经理）", "participants": "第9组（4 人）", "mode": "express",
                      "scenario": "lab"},
         "coverage": {"cognition": "confirmed", "quality": "explored_empty", "responsibility": "confirmed",
                      "cost": "confirmed", "performance": "confirmed", "other": "unresolved"},
         "barriers": {
             "cognition": [{"sticky_text": "把已支持的查重合并当新需求排进迭代", "customer_quote": "早就有", "evidence": "研发做到一半",
                            "confirmed": True, "selected": True}],
             "quality": [],
             "responsibility": [{"sticky_text": "把数据丢失类缺陷当体验问题拒绝", "customer_quote": "月底结账对不上",
                                 "evidence": "投诉到高层", "confirmed": True, "selected": True},
                                {"sticky_text": "话术里写了别家客户的名字", "confirmed": True, "selected": False}],
             "cost": [{"sticky_text": "每条反馈模型费用超过 5 毛钱", "confirmed": True, "selected": True}],
             "performance": [{"sticky_text": "60 条要在 30 分钟内跑完，跑一半超时", "confirmed": True, "selected": True}],
             "other": []},
         "parking_lot": [{"original": "加个知识库", "converted_to": "cognition[0]"}]}
    d.update(over)
    return d


def session(tmp_path, data=None, name="第9组-2026-11-20"):
    d = tmp_path / "outputs" / "fishbone" / name
    d.mkdir(parents=True)
    (d / "fishbone-data.json").write_text(json.dumps(data or sample(), ensure_ascii=False), encoding="utf-8")
    return d


def _cell(root, cid):
    found = [c for c in root.iter("mxCell") if c.get("id") == cid]
    assert len(found) == 1, cid
    return found[0]


# ---------------------------------------------------------------- skill files
def test_skill_files_are_present_tracked_and_not_ignored():
    files = [FISH_DIR / "SKILL.md", FISH_DIR / "scripts" / "generate_fishbone.py", TEMPLATE,
             CRIT_DIR / "SKILL.md", CRIT_DIR / "scripts" / "criteria.py", CRIT_DIR / "evaluator-coverage.json",
             ROOT / "data" / "scenario" / "role-card.md", ROOT / "tools" / "publish-define-pages.sh"]
    for f in files:
        assert f.is_file(), f
        r = subprocess.run(["git", "check-ignore", "-q", str(f)], cwd=str(ROOT))
        assert r.returncode == 1, f"{f} is git-ignored: a clone / bundle of the repo would not ship it"
    for skill in (FISH_DIR, CRIT_DIR):  # Claude Code project skill frontmatter
        head = (skill / "SKILL.md").read_text(encoding="utf-8").split("---")
        assert head[0] == "" and f"name: {skill.name}" in head[1] and "description:" in head[1]
    assert hashlib.sha256(TEMPLATE.read_bytes()).hexdigest() == TEMPLATE_SHA256
    assert os.access(ROOT / "tools" / "publish-define-pages.sh", os.X_OK)


def test_skills_use_no_quick_tools_and_no_disk_scan():
    text = (FISH_DIR / "SKILL.md").read_text(encoding="utf-8") + (CRIT_DIR / "SKILL.md").read_text(encoding="utf-8")
    for quick in ("fdfind", "run_python", "open_file", "open_in_session_tab", "file_write"):
        assert quick not in text
    assert "find /" not in text and "assets/fishbone-template.drawio" in text
    assert "express" in text and "./40-create-evaluators.sh" in text


def test_no_scenario_numbering_and_no_customer_names_in_new_files():
    for f in [*FISH_DIR.rglob("*"), *CRIT_DIR.rglob("*"), ROOT / "data" / "scenario" / "role-card.md",
              ROOT / "labkit" / "define_pages.py", ROOT / "tools" / "publish-define-pages.sh"]:
        if f.is_file() and "__pycache__" not in f.parts:
            t = f.read_text(encoding="utf-8")
            for bad in ("场景 12", "场景12", "s12_", "Align Tech", "AlignTech", "爱齐", "Align Technology"):
                assert bad not in t, (f, bad)


# ---------------------------------------------------------------- drawio generator
def test_template_cell_ids_validate():
    ok, errors = GF.validate_template(ET.parse(str(TEMPLATE)).getroot())
    assert ok and errors == []
    assert len(GF.ALL_REQUIRED_IDS) == 22 and len(set(GF.ALL_REQUIRED_IDS)) == 22
    r = subprocess.run([sys.executable, str(FISH_DIR / "scripts" / "generate_fishbone.py"), "--check-template"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and json.loads(r.stdout)["success"] is True


def test_generate_fills_slots_clears_placeholders_and_reports_every_check(tmp_path):
    d = session(tmp_path)
    r = subprocess.run([sys.executable, str(FISH_DIR / "scripts" / "generate_fishbone.py"), str(d)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout
    res = json.loads(r.stdout)
    assert res["success"] and set(res["checks"].values()) == {"PASS"} and len(res["checks"]) == 13
    root = ET.parse(str(d / "fishbone.drawio")).getroot()
    data = sample()
    for cid in GF.ALL_REQUIRED_IDS:
        val = _cell(root, cid).get("value", "")
        assert not any(ph in val for ph in GF.PLACEHOLDER_TEXTS), (cid, val)
    for field, cid in GF.META_CELLS.items():
        assert html.unescape(_cell(root, cid).get("value")) == data["metadata"][field]
    for dim, slots in GF.DIMENSION_SLOTS.items():
        sel = [b["sticky_text"] for b in data["barriers"][dim] if b["selected"]]
        vals = [_cell(root, cid).get("value", "") for cid in slots]
        assert [html.unescape(v) for v in vals[:len(sel)]] == sel and vals[len(sel):] == [""] * (3 - len(sel))
    report = (d / "validation-report.txt").read_text(encoding="utf-8")
    assert "Overall: PASS" in report and report.count("[PASS]") == 13
    ids = [c.get("id") for c in root.iter("mxCell")]  # the template's two stale layers are gone, nothing else
    assert all(i in ("0", "1") or i.startswith(GF.CURRENT_LAYER_PREFIX) for i in ids)
    assert len(ids) == len([i for i in ids if i.startswith(GF.CURRENT_LAYER_PREFIX)]) + 2
    for c in root.iter("mxCell"):
        assert not any(m in (c.get("value") or "") for m in GF.PLACEHOLDER_MARKS), c.get("id")


def test_a_note_may_contain_placeholder_words(tmp_path):
    data = sample()
    data["barriers"]["cost"][0]["sticky_text"] = "表单里写着“请填写”，它就当成空值"
    d = session(tmp_path, data)
    assert GF.generate_fishbone(d / "fishbone-data.json", TEMPLATE, d / "fishbone.drawio")["success"]


def test_untrusted_sticky_text_is_escaped_for_html_cells(tmp_path):
    data = sample()
    data["barriers"]["quality"] = [{"sticky_text": EVIL, "confirmed": True, "selected": True}]
    data["coverage"]["quality"] = "confirmed"
    data["metadata"]["customer"] = "<b>业务方</b>"
    d = session(tmp_path, data)
    res = GF.generate_fishbone(d / "fishbone-data.json", TEMPLATE, d / "fishbone.drawio")
    assert res["success"], res
    root = ET.parse(str(d / "fishbone.drawio")).getroot()
    val = _cell(root, GF.DIMENSION_SLOTS["quality"][0]).get("value")
    assert "<script" not in val and "<img" not in val and "&lt;script&gt;" in val  # drawio shows it as text
    assert html.unescape(val) == EVIL
    assert _cell(root, GF.META_CELLS["customer"]).get("value") == "&lt;b&gt;业务方&lt;/b&gt;"


@pytest.mark.parametrize("mutate, check", [
    (lambda d: d.update(status="draft"), "status"),
    (lambda d: d.update(schema_version="2.0"), "json"),
    (lambda d: d["metadata"].update(use_case=""), "metadata"),
    (lambda d: d["coverage"].pop("other"), "coverage"),
    (lambda d: d["coverage"].update(quality="maybe"), "coverage"),
    (lambda d: d["coverage"].update(cognition="explored_empty"), "coverage"),  # has a confirmed barrier
    (lambda d: d["coverage"].update(quality="confirmed"), "coverage"),  # but no barrier
    (lambda d: d["barriers"]["cost"].append({"sticky_text": "x", "confirmed": False, "selected": True}), "confirmed"),
    (lambda d: d["barriers"]["cost"].append({"sticky_text": " ", "confirmed": True, "selected": True}), "confirmed"),
    (lambda d: d["barriers"]["cost"].extend({"sticky_text": f"c{i}", "confirmed": True, "selected": True} for i in range(3)), "max3"),
    (lambda d: d["barriers"]["cost"][0].update(confirmed="false"), "json"),        # strings are not booleans
    (lambda d: d["barriers"]["cost"][0].update(selected="yes"), "json"),
    (lambda d: d["barriers"].update(security=[{"sticky_text": "x", "confirmed": True, "selected": True}]), "json"),
    (lambda d: d["coverage"].update(security="confirmed"), "json"),
    (lambda d: d.update(barriers=[{"sticky_text": "x"}]), "json"),
    (lambda d: d["barriers"].update(other=5), "json"),
    (lambda d: d["barriers"]["other"].append("x"), "json"),
])
def test_invalid_json_fails_the_named_check_and_writes_no_drawio(tmp_path, mutate, check):
    data = sample()
    mutate(data)
    d = session(tmp_path, data)
    res = GF.generate_fishbone(d / "fishbone-data.json", TEMPLATE, d / "fishbone.drawio")
    assert not res["success"] and res["checks"][check] == "FAIL" and not (d / "fishbone.drawio").exists()
    GF.write_validation_report(res, d / "validation-report.txt")
    rep = (d / "validation-report.txt").read_text(encoding="utf-8")
    assert "Overall: FAIL" in rep and "[FAIL]" in rep and "[SKIP]" in rep  # never a blanket PASS


def test_long_sticky_warns_by_display_width(tmp_path):
    data = sample()
    data["barriers"]["cost"][0]["sticky_text"] = "长" * 51
    data["barriers"]["cognition"][0]["sticky_text"] = "整批（上个月 60 条、大促后 200 条）要 30 分钟内跑完，单条最好 1 分钟内出结果"
    _, warns = GF.validate_json(data)
    assert len(warns) == 1 and warns[0].startswith("cost:")


@pytest.mark.parametrize("kind", ["missing", "duplicate"])
def test_changed_template_cell_ids_hard_stop(tmp_path, kind):
    text = TEMPLATE.read_text(encoding="utf-8")
    cid = GF.DIMENSION_SLOTS["cost"][1]
    if kind == "missing":
        text = text.replace(f'id="{cid}"', 'id="renamed-cell"')
    else:
        text = text.replace(f'id="{GF.DIMENSION_SLOTS["cost"][2]}"', f'id="{cid}"')
    tpl = tmp_path / "t.drawio"
    tpl.write_text(text, encoding="utf-8")
    d = session(tmp_path)
    res = GF.generate_fishbone(d / "fishbone-data.json", tpl, d / "fishbone.drawio")
    assert not res["success"] and any(("MISSING" if kind == "missing" else "DUPLICATE") in e for e in res["validation_errors"])
    assert not (d / "fishbone.drawio").exists()


# ---------------------------------------------------------------- preview pages
def _criteria_doc():
    return {"metadata": {"customer": EVIL, "use_case": "需求反馈分诊 Agent", "date": "2026-11-20"},
            "criteria": [{"id": "C-01", "text": "Agent " + EVIL, "sticky_text": EVIL, "dimension": "责任", "tier": "红线",
                          "threshold": None, "executor": "代码断言", "coverage": "部分覆盖", "coverage_notes": [EVIL],
                          "golden_ids": [EVIL], "checks": [{"id": "misreject", "evaluator": EVIL, "field": EVIL}]}],
            "warnings": [EVIL],
            "golden_proposals": [{"criterion": "C-01", "feedback_id": EVIL, "source": "extra", "title": EVIL,
                                  "labels": EVIL, "why": EVIL}]}


def _assert_safe(page):
    assert external_refs(page) == []  # also flags any on* attribute, i.e. injected markup
    low = page.lower()
    assert "<script" not in low and "<img" not in low and "<a " not in low and 'href="https://evil' not in low
    assert 'http-equiv="Content-Security-Policy"' in page and "default-src 'none'" in page and "script-src 'none'" in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_fishbone_page_is_self_contained_and_escapes_untrusted_text():
    data = sample()
    data["barriers"]["responsibility"][0].update(sticky_text=EVIL, customer_quote=EVIL, evidence=EVIL)
    data["metadata"]["use_case"] = EVIL
    data["parking_lot"] = [{"original": EVIL, "converted_to": EVIL}]
    page = DP.render_fishbone(data, {"path": EVIL, "sha": "abc"})
    _assert_safe(page)
    assert "Agent 无法<br>稳定上线" in page and page.count('class="sticky"') == 4
    assert "话术里写了别家客户的名字" in page and "备选（未入图）" in page  # backlog shown in the detail table


def test_criteria_page_is_self_contained_and_escapes_untrusted_text():
    page = DP.render_criteria(_criteria_doc(), {"path": "x", "sha": "abc"})
    _assert_safe(page)
    assert "0 违例（不打分）" in page and "./40-create-evaluators.sh" in page


def test_finish_refuses_a_page_that_loads_anything():
    with pytest.raises(DP.DefineError):
        DP.finish('<html><head><meta charset="utf-8">' + DP._CSP_META + '</head><body><img src="https://x/y.png"></body></html>', "x")


def test_build_turns_a_generator_crash_into_a_message(tmp_path, monkeypatch):
    d = session(tmp_path)
    gen = DP._generator()
    monkeypatch.setattr(DP, "_generator", lambda: type("G", (), {
        "TEMPLATE_PATH": gen.TEMPLATE_PATH, "write_validation_report": gen.write_validation_report,
        "generate_fishbone": staticmethod(lambda *a: (_ for _ in ()).throw(AttributeError("boom")))}))
    with pytest.raises(DP.DefineError, match="AttributeError"):
        DP.build(d)


def test_criteria_built_from_an_older_fishbone_is_not_published(tmp_path, s3, capsys):
    fake = s3()
    d = session(tmp_path)
    _with_criteria(d, stale=True)
    assert DP.main([str(d)]) == 1
    keys = [c[2] for c in fake.calls if c[0] == "upload_file"]
    assert [k.rsplit("/", 1)[1] for k in keys] == ["fishbone.html", "fishbone.drawio"]
    assert not (d / "criteria.html").exists() and "旧的鱼骨图" in capsys.readouterr().out


def test_build_refuses_an_unapproved_fishbone_and_writes_nothing(tmp_path):
    d = session(tmp_path, sample(status="draft"))
    with pytest.raises(DP.DefineError, match="校验没通过"):
        DP.build(d)
    assert not (d / "fishbone.html").exists() and not (d / "fishbone.drawio").exists()
    assert "Overall: FAIL" in (d / "validation-report.txt").read_text(encoding="utf-8")


def test_session_slug_is_one_safe_key_segment(tmp_path):
    assert DP.session_slug(tmp_path / "第3组-2026-11-20") == "第3组-2026-11-20"
    a, b = DP.session_slug(tmp_path / "a b"), DP.session_slug(tmp_path / "a-b")
    assert a != b and a.startswith("a-b-") and b == "a-b"  # a sanitized name never collides with a clean one
    for weird in ("a b?c#d", "x/../y", "...", "$(id);x", "a\\b"):
        slug = DP.session_slug(tmp_path / weird)
        assert slug and "/" not in slug and slug not in (".", "..") and all(c.isalnum() or c in "._-" for c in slug)


def test_no_upload_cli_needs_no_aws(tmp_path):
    d = session(tmp_path)
    env = dict(os.environ, AWS_SHARED_CREDENTIALS_FILE="/dev/null", AWS_CONFIG_FILE="/dev/null",
               AWS_EC2_METADATA_DISABLED="true")
    env.pop("LAB_SUFFIX", None)
    r = subprocess.run(["bash", str(ROOT / "tools" / "publish-define-pages.sh"), str(d), "--no-upload"],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (d / "fishbone.html").is_file() and (d / "fishbone.drawio").is_file() and "没有上传" in r.stdout


# ---------------------------------------------------------------- publish (fail closed)
@pytest.fixture
def s3(monkeypatch):
    def install(**kw):
        fake = TD.FakeS3(**kw)
        monkeypatch.setattr(aws, "client", lambda name: fake if name == "s3" else pytest.fail(f"unexpected client {name}"))
        monkeypatch.setattr(aws, "bucket_name", lambda: TD.BUCKET)
        monkeypatch.setattr(aws, "account_id", lambda: TD.OWNER)
        monkeypatch.setattr(aws, "cfg", lambda: {"AWS_REGION": "us-west-2", "LAB_SUFFIX": "test", "LAB_ROOT": str(ROOT)})
        return fake
    return install


def _with_criteria(d, stale=False):
    doc = _criteria_doc()
    doc["source"] = {"fishbone_sha256": "0" * 12 if stale else DP.sha12(d / "fishbone-data.json")}
    (d / "criteria.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_publish_uploads_pages_and_drawio_under_reports_define_after_the_privacy_check(tmp_path, s3, capsys):
    fake = s3()
    d = session(tmp_path)
    _with_criteria(d)
    assert DP.main([str(d)]) == 0
    prefix = "reports/define/第9组-2026-11-20"
    ups = [c for c in fake.calls if c[0] == "upload_file"]
    assert [c[2] for c in ups] == [f"{prefix}/fishbone.html", f"{prefix}/fishbone.drawio", f"{prefix}/criteria.html"]
    assert [c[3]["ContentType"] for c in ups] == ["text/html; charset=utf-8", DP.DRAWIO_TYPE, "text/html; charset=utf-8"]
    assert all(c[1] == TD.BUCKET and set(c[3]) == {"ContentType", "ExpectedBucketOwner"} and c[3]["ExpectedBucketOwner"] == TD.OWNER
               for c in ups)  # no ACL, owner-checked
    names = [c[0] for c in fake.calls]
    for i, n in enumerate(names):
        if n == "upload_file":
            assert fake.calls[i - 1] == ("get_public_access_block", TD.BUCKET, TD.OWNER)
    assert fake.objects[f"{prefix}/fishbone.drawio"][0] == (d / "fishbone.drawio").read_bytes()
    out = capsys.readouterr().out
    quoted = "reports/define/%E7%AC%AC9%E7%BB%84-2026-11-20/fishbone.html"
    assert f"https://us-west-2.console.aws.amazon.com/s3/object/{TD.BUCKET}?region=us-west-2&prefix={quoted}" in out


@pytest.mark.parametrize("kw", [{"unreadable": True}, {"owner": "999999999999"}])
def test_publish_uploads_nothing_when_the_bucket_is_not_provably_private(tmp_path, s3, capsys, kw):
    fake = s3(**kw)
    d = session(tmp_path)
    assert DP.main([str(d)]) == 1
    assert not [c for c in fake.calls if c[0] in ("upload_file", "put_public_access_block")]
    assert [c[0] for c in fake.calls].count("get_public_access_block") == 1 and "未上传" in capsys.readouterr().out


def test_publish_reapplies_a_missing_public_access_block_first(tmp_path, s3):
    fake = s3(pab=None)
    d = session(tmp_path)
    assert DP.main([str(d)]) == 0
    names = [c[0] for c in fake.calls]
    assert names.index("put_public_access_block") < names.index("upload_file")


def test_an_invalid_fishbone_is_never_uploaded(tmp_path, s3):
    fake = s3()
    d = session(tmp_path, sample(status="draft"))
    assert DP.main([str(d)]) == 1
    assert fake.calls == []


@pytest.mark.parametrize("key", ["data/x.html", "reports", "reports/", "reports/../x", "reports/a//b", "reports/./a",
                                 "reports/a\\b", "reports/a\nb"])
def test_publish_key_must_stay_under_reports(key, s3, tmp_path):
    fake = s3()
    f = tmp_path / "x.html"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        P.publish(str(f), key=key)
    assert fake.calls == []


def test_publish_default_key_and_content_type_are_unchanged(s3, tmp_path):
    fake = s3()
    f = tmp_path / "compare-x.html"
    f.write_text("x", encoding="utf-8")
    P.publish(str(f))
    assert [c[2:] for c in fake.calls if c[0] == "upload_file"] == [("reports/compare-x.html", TD.HTML_ARGS)]


# ---------------------------------------------------------------- criteria: coverage map
def test_coverage_map_matches_the_lab_evaluators():
    cat = CR.load_catalog()
    sys.path.insert(0, str(ROOT / "evaluators" / "adlc_reject_quality"))
    import rubric  # noqa: E402
    from labkit import evaluate
    for c in cat.values():
        assert c["executor"] in CR.EXECUTORS and c["tier"] in CR.TIERS and c["coverage"] in ("covered", "partial")
        assert c["coverage"] == "covered" or c.get("gap"), c["id"]
        assert (c["tier"] == "红线") == (c["threshold"] is None), c["id"]
        if c["id"].startswith("reject.flag."):
            assert c["field"].removeprefix("红线：") in rubric.RED_FLAGS, c["id"]
        if c["id"].startswith("builtin."):
            assert c["evaluator"].split("（")[0] in evaluate.BUILTINS
    flags = {c["field"].removeprefix("红线：") for c in cat.values() if c["id"].startswith("reject.flag.")}
    assert flags == set(rubric.RED_FLAGS[:4])  # the 5th flag (misreject) is the misreject check
    assert cat["misreject"]["golden_requires"] == {"can_auto_reject": False}
    assert all(cat[k]["executor"] == "指标读数" for k in ("metric.latency", "metric.tokens", "metric.errors"))


def crit(**over):
    c = {"id": "C-01", "obstacle_id": "responsibility-1", "text": "Agent 不把功能缺陷类反馈判为“拒绝”", "dimension": "责任",
         "tier": "红线", "threshold": None, "executor": "代码断言", "checks": ["misreject"], "golden_ids": ["G03"]}
    c.update(over)
    return c


@pytest.mark.parametrize("over, msg", [
    ({"text": "让客户觉得话术专业"}, "主语"),
    ({"text": "话术要专业"}, "Agent"),
    ({"threshold": "0.9"}, "红线不打分"),
    ({"executor": "裁判", "checks": ["reject.flag.fabricated"]}, "判不成红线"),
    ({"dimension": "成本", "tier": "门禁", "threshold": "≤ 0.5 元", "executor": "裁判"}, "指标读数"),
    ({"tier": "门禁", "threshold": ""}, "阈值"),
    ({"checks": ["no.such.check"]}, "未知的 check"),
    ({"dimension": "安全"}, "维度"),
    ({"executor": "人工"}, "执行方式"),
])
def test_criterion_rules(over, msg):
    errors, _ = CR.check_rules(crit(**over), CR.load_catalog())
    assert any(msg in e for e in errors), errors


def test_judge_gate_and_assertable_judge_are_warned():
    _, warns = CR.check_rules(crit(tier="门禁", threshold="≥ 0.7", executor="裁判", checks=["reject.flag.promise_date"]),
                              CR.load_catalog())
    assert any("校准" in w for w in warns) and any("代码断言" in w for w in warns)


GOLDEN = {str(r["golden_id"]): r for r in CR.golden_rows()}


@pytest.mark.parametrize("over, level, fixable", [
    ({}, "已覆盖", False),
    ({"golden_ids": []}, "部分覆盖", True),                                   # evaluator can judge, no sample
    ({"checks": []}, "未覆盖", False),
    ({"checks": ["reject.flag.leak"], "golden_ids": ["G04"]}, "部分覆盖", False),  # judge, criterion wants code
    ({"tier": "门禁", "threshold": "≥ 0.7", "checks": ["label.decision"], "golden_ids": ["G06"]}, "已覆盖", False),
    ({"checks": ["label.decision"], "golden_ids": ["G06"]}, "部分覆盖", False),     # red line wanted, gate offered
    ({"dimension": "性能", "tier": "门禁", "threshold": "单条 ≤ 60 秒", "executor": "指标读数", "checks": ["metric.latency"],
      "golden_ids": []}, "部分覆盖", False),
])
def test_coverage_levels(over, level, fixable):
    c = crit(**over)
    assert CR.check_rules(c, CR.load_catalog())[0] == []
    got, notes, fx, _ = CR.coverage(c, CR.load_catalog(), GOLDEN)
    assert (got, fx) == (level, fixable), notes
    assert (got == "已覆盖") == (notes == [])


def test_a_stated_golden_gap_makes_it_partial_and_fixable():
    level, notes, fixable, _ = CR.coverage(crit(golden_gap="没有看起来很小的缺陷"), CR.load_catalog(), GOLDEN)
    assert (level, fixable) == ("部分覆盖", True) and any("看起来很小" in n for n in notes)
    level, _, fixable, _ = CR.coverage(crit(dimension="成本", tier="门禁", threshold="x", executor="指标读数",
                                            checks=["metric.tokens"], golden_gap="x"), CR.load_catalog(), GOLDEN)
    assert not fixable  # a metric reading is not fixed by golden samples


def test_golden_ids_must_exist_and_satisfy_the_check():
    with pytest.raises(CR.CriteriaError, match="G99"):
        CR.coverage(crit(golden_ids=["G99"]), CR.load_catalog(), GOLDEN)
    level, _, fixable, warns = CR.coverage(crit(golden_ids=["G05"]), CR.load_catalog(), GOLDEN)  # G05 is auto-rejectable
    assert level == "部分覆盖" and fixable and any("G05" in w for w in warns)


def _draft(d, criteria, proposals=()):
    (d / CR.DRAFT).write_text(json.dumps({"criteria": criteria, "golden_proposals": list(proposals)}, ensure_ascii=False),
                              encoding="utf-8")


ALL = [crit(id="C-01", obstacle_id="responsibility-1", golden_ids=[]),
       crit(id="C-02", obstacle_id="responsibility-2", text="Agent 的拒绝话术不出现其他客户名称", checks=["reject.flag.leak"],
            golden_ids=["G04"]),
       crit(id="C-03", obstacle_id="cognition-1", text="Agent 对已支持的能力判“拒绝”并给出用法", dimension="认知", tier="门禁",
            threshold="准确率 ≥ 0.70", checks=["label.decision"], golden_ids=["G06", "G07"]),
       crit(id="C-04", obstacle_id="cost-1", text="Agent 处理一条反馈的模型费用 ≤ 0.5 元", dimension="成本", tier="门禁",
            threshold="每条 ≤ 0.5 元", executor="指标读数", checks=["metric.tokens"], golden_ids=[]),
       crit(id="C-05", obstacle_id="performance-1", text="Agent 60 条整批 30 分钟内处理完", dimension="性能", tier="门禁",
            threshold="整批 ≤ 30 分钟", executor="指标读数", checks=["metric.latency"], golden_ids=[])]
P048 = {"criterion": "C-01", "feedback_id": "FB-2610-048", "why": "缺小缺陷样本",
        "expected": {"important": "否", "urgent": "否", "decision": "迭代", "level": "P2"}, "acceptable_decisions": ["迭代"],
        "can_auto_reject": False, "dimensions": ["责任"], "case_type": "简单", "rationale": "线上功能缺陷不可自动拒绝。"}
PNEW = {"criterion": "C-01", "category": "incident", "why": "安全漏洞",
        "new_feedback": {"title": "离职员工的 App 账号还能看到客户", "content": "停用账号后 App 里没退出，还能看客户电话。",
                         "customer": "拓途物流（示例）", "industry": "物流", "customer_tier": "腰部", "channel": "工单系统",
                         "module": "权限与安全", "submitted_at": "2026-10-30", "feedback_type": "功能缺陷", "contract_commitment": "否"},
        "expected": {"important": "是", "urgent": "是", "decision": "迭代", "level": "P0"}, "acceptable_decisions": ["迭代"],
        "can_auto_reject": False, "dimensions": ["责任"], "case_type": "简单", "rationale": "安全漏洞且正在发生。"}


def _data_shas():
    return {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "data").rglob("*") if p.is_file()}


def test_build_writes_the_table_and_previews_without_touching_data(tmp_path):
    d = session(tmp_path)
    _draft(d, ALL, [P048, PNEW])
    before = _data_shas()
    doc = CR.build(d)
    assert _data_shas() == before
    assert [r["coverage"] for r in doc["criteria"]] == ["部分覆盖", "部分覆盖", "已覆盖", "部分覆盖", "部分覆盖"]
    assert doc["summary"]["golden_fixable"] == ["C-01"] and doc["criteria"][3]["threshold"] == "每条 ≤ 0.5 元"
    assert json.loads((d / CR.OUT_JSON).read_text(encoding="utf-8"))["criteria"][0]["id"] == "C-01"
    md = (d / CR.OUT_MD).read_text(encoding="utf-8")
    assert "| C-03 |" in md and "./40-create-evaluators.sh" in md and "./30-deploy-runtime.sh" in md
    rows = list(__import__("csv").DictReader((d / CR.PROPOSAL_CSV).open(encoding="utf-8-sig")))
    sys.path.insert(0, str(ROOT / "data" / "tools"))
    import golden_lib
    assert list(rows[0]) == golden_lib.FEEDBACK_CSV_FIELDS and {r["操作"] for r in rows} == {"新增"}
    assert [r["feedback_id"] for r in rows] == ["FB-2610-048", "FB-2610-061"] and rows[0]["可自动拒绝"] == "否"
    extra = [json.loads(x) for x in (d / CR.PROPOSAL_EXTRA).read_text(encoding="utf-8").splitlines()]
    assert len(extra) == 1 and extra[0]["id"] == "FB-2610-061" and extra[0]["_meta"]["category"] == "incident"
    assert extra[0]["_meta"]["golden_id"] is None and extra[0]["_meta"]["can_auto_reject"] is False
    assert DP.render_criteria(doc, {"path": "x", "sha": "y"})  # the page renders the real document


def test_applied_samples_are_recorded_and_must_exist(tmp_path):
    d = session(tmp_path)
    _draft(d, ALL)
    draft = json.loads((d / CR.DRAFT).read_text(encoding="utf-8"))
    draft["golden_applied"] = [{"criterion": "C-01", "feedback_id": "FB-2610-003"}]
    (d / CR.DRAFT).write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    doc = CR.build(d)
    assert doc["golden_applied"][0]["golden_id"] == "G03" and "已写入黄金集的样本" in (d / CR.OUT_MD).read_text(encoding="utf-8")
    assert "已写入黄金集的样本" in DP.render_criteria(doc, {"path": "x", "sha": "y"})
    draft["golden_applied"] = [{"criterion": "C-01", "feedback_id": "FB-2610-048"}]  # not written yet
    (d / CR.DRAFT).write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CR.CriteriaError, match="黄金集里没有"):
        CR.build(d)


@pytest.mark.parametrize("criteria, msg", [
    (ALL[:-1], "还没有判据"),
    (ALL + [crit(id="C-06", obstacle_id="cost-1")], "写了 2 条"),
    (ALL[:-1] + [crit(id="C-05", obstacle_id="nope-1")], "不是鱼骨图里已确认的障碍"),
    ([dict(ALL[0], id="C-02")] + ALL[1:], "重复"),
])
def test_build_requires_one_criterion_per_confirmed_obstacle(tmp_path, criteria, msg):
    d = session(tmp_path)
    _draft(d, criteria)
    with pytest.raises(CR.CriteriaError, match=msg):
        CR.build(d)
    assert not (d / CR.OUT_JSON).exists()


@pytest.mark.parametrize("props, msg", [
    ([dict(P048, feedback_id="FB-2610-003")], "已经在黄金集里"),
    ([dict(P048, feedback_id="FB-9999-001")], "没有"),
    ([P048, dict(P048, feedback_id="FB-2610-012"), dict(P048, feedback_id="FB-2610-057"), dict(P048, feedback_id="FB-2610-044")], "最多"),
    ([dict(PNEW, new_feedback=dict(PNEW["new_feedback"], customer="某真实公司"))], "示例"),
    ([dict(PNEW, category="nope")], "category"),
    ([dict(P048, can_auto_reject=False, expected=dict(P048["expected"], decision="拒绝"), acceptable_decisions=["拒绝"])], "试运行"),
    ([dict(P048, criterion="C-99")], "不在判据表里"),
])
def test_bad_golden_proposals_are_refused(tmp_path, props, msg):
    d = session(tmp_path)
    _draft(d, ALL, props)
    with pytest.raises(CR.CriteriaError, match=msg):
        CR.build(d)
    assert not (d / CR.PROPOSAL_CSV).exists()


def test_proposals_apply_through_the_existing_tools(tmp_path):
    """In a copy of the repo: preview -> extra-feedback + generate_mock_data -> golden_tool apply-feedback -> freeze -> verify."""
    repo = tmp_path / "repo"
    ign = shutil.ignore_patterns("img", "__pycache__", "history", ".pytest_cache")
    for sub in ("data", "evaluators", "docs", ".claude"):
        shutil.copytree(ROOT / sub, repo / sub, ignore=ign)
    d = repo / "outputs" / "fishbone" / "g"
    d.mkdir(parents=True)
    (d / "fishbone-data.json").write_text(json.dumps(sample(), ensure_ascii=False), encoding="utf-8")
    _draft(d, ALL, [P048, PNEW])
    py = [sys.executable]
    crit_py = str(repo / ".claude" / "skills" / "adlc-criteria" / "scripts" / "criteria.py")

    def run(*args):
        r = subprocess.run(py + list(args), cwd=str(repo), capture_output=True, text=True, timeout=180)
        return r.returncode, r.stdout + r.stderr

    rc, out = run(crit_py, "build", str(d))
    assert rc == 0, out
    extra_file = repo / "data" / "templates" / "extra-feedback.jsonl"
    extra_file.write_text((d / CR.PROPOSAL_EXTRA).read_text(encoding="utf-8"), encoding="utf-8")
    params = repo / "data" / "templates" / "mock-data-params.yaml"
    params.write_text(params.read_text(encoding="utf-8").replace("\ncount: 60\n", "\ncount: 61\n"), encoding="utf-8")
    rc, out = run("data/tools/generate_mock_data.py")
    assert rc == 0 and "已生成 61 条" in out, out
    rc, out = run(crit_py, "verify", str(d))
    assert rc == 1 and "❌" not in out and "还没运行 apply-feedback" in out, out
    rc, out = run("data/tools/golden_tool.py", "apply-feedback", "--feedback", str(d / CR.PROPOSAL_CSV))
    assert rc == 0 and "新增 G31" in out and "新增 G32" in out, out
    rc, out = run("data/tools/golden_tool.py", "freeze")
    assert rc == 0, out
    rc, out = run(crit_py, "verify", str(d))
    assert rc == 0 and "G31" in out and "./40-create-evaluators.sh" in out, out
    synced = json.loads((repo / "evaluators" / "adlc_label_accuracy" / "golden_set.json").read_text(encoding="utf-8"))
    assert synced["items"]["FB-2610-048"]["can_auto_reject"] is False and "FB-2610-061" in synced["items"]


# ---------------------------------------------------------------- review round 1 regressions
def _build_with(tmp_path, props=(), criteria=None, data=None):
    d = session(tmp_path, data)
    _draft(d, criteria if criteria is not None else ALL, props)
    return d, CR.build(d)


@pytest.mark.parametrize("value, ok", [("否", False), (False, False), ("是", None), ("maybe", None), (None, None)])
def test_can_auto_reject_must_be_explicit_and_typed(tmp_path, value, ok):
    p = dict(P048, can_auto_reject=value)
    if value is None:
        p.pop("can_auto_reject")
    if ok is None:  # "是" is refused too: FB-2610-048 is a 功能缺陷
        with pytest.raises(CR.CriteriaError, match="can_auto_reject|功能缺陷"):
            _build_with(tmp_path, [p])
        return
    d, _ = _build_with(tmp_path, [p])
    row = next(__import__("csv").DictReader((d / CR.PROPOSAL_CSV).open(encoding="utf-8-sig")))
    assert row["可自动拒绝"] == "否"


def test_string_lists_are_split_like_the_csv_and_bad_types_refused(tmp_path):
    rej = dict(P048, feedback_id="FB-2610-008", can_auto_reject=True, case_type="应拒绝",
               expected={"important": "否", "urgent": "否", "decision": "拒绝", "level": "P2"}, acceptable_decisions="拒绝",
               reject_points="已支持多币种（CAP-018）；属于配置问题", reply_points="理解诉求；给出多币种设置入口；可安排客户成功经理协助")
    d, doc = _build_with(tmp_path, [rej])
    row = next(__import__("csv").DictReader((d / CR.PROPOSAL_CSV).open(encoding="utf-8-sig")))
    assert row["拒绝要点"] == "已支持多币种（CAP-018）；属于配置问题" and row["可接受建议"] == "拒绝"
    with pytest.raises(CR.CriteriaError, match="reject_points"):
        _build_with(tmp_path / "b", [dict(rej, reject_points=[1, 2])])


def test_matched_capabilities_are_flagged_as_not_stored(tmp_path):
    _, doc = _build_with(tmp_path, [dict(P048, matched_capabilities=["CAP-003"])])
    assert any("matched_capabilities 不会写进黄金集" in w for w in doc["warnings"])


def test_duplicate_proposals_are_refused(tmp_path):
    with pytest.raises(CR.CriteriaError, match="两次"):
        _build_with(tmp_path, [P048, dict(P048)])


def test_seed_disagreement_is_warned(tmp_path):
    _, doc = _build_with(tmp_path, [dict(P048, expected=dict(P048["expected"], important="是"))])
    assert any("和种子库标注不一致" in w for w in doc["warnings"])


def test_red_line_cannot_use_a_builtin_judge():
    errors, _ = CR.check_rules(crit(executor="内置评估器", checks=["builtin.correctness"]), CR.load_catalog())
    assert any("判不成红线" in e for e in errors)


def test_unapproved_fishbone_and_own_scenario(tmp_path):
    with pytest.raises(CR.CriteriaError, match="还没被业务方最终确认"):
        _build_with(tmp_path / "a", data=sample(status="draft"))
    with pytest.raises(CR.CriteriaError, match="还没被业务方最终确认"):
        d = session(tmp_path / "b", sample(status="draft"))
        CR.skeleton(d)
    own = sample()
    own["metadata"]["scenario"] = "own"
    _build_with(tmp_path / "c", data=own)  # criteria are fine for an own scenario ...
    with pytest.raises(CR.CriteriaError, match="自己的场景"):  # ... golden additions are not
        _build_with(tmp_path / "d", [P048], data=own)


def test_a_changed_fishbone_is_caught_by_the_sticky_text(tmp_path):
    bad = [dict(c) for c in ALL]
    bad[0]["sticky_text"] = "鱼骨图改动前的另一条便签"
    with pytest.raises(CR.CriteriaError, match="鱼骨图改过"):
        _build_with(tmp_path, criteria=bad)
    ok = [dict(c) for c in ALL]
    ok[0]["sticky_text"] = sample()["barriers"]["responsibility"][0]["sticky_text"]
    _build_with(tmp_path / "ok", criteria=ok)


@pytest.mark.parametrize("over", [{"obstacle_id": ["responsibility-1"]}, {"golden_ids": "G06"}, {"text": ["Agent x"]},
                                  {"checks": [{"id": "misreject"}]}, {"id": 7}])
def test_malformed_criteria_fields_are_errors_not_crashes(tmp_path, over):
    bad = [dict(ALL[0], **over)] + ALL[1:]
    with pytest.raises(CR.CriteriaError):
        _build_with(tmp_path, criteria=bad)


@pytest.mark.parametrize("over", [{"expected": ["否"]}, {"criterion": ["C-01"]}, {"feedback_id": ["FB-2610-048"]}])
def test_malformed_proposals_are_errors_not_crashes(tmp_path, over):
    with pytest.raises(CR.CriteriaError):
        _build_with(tmp_path, [dict(P048, **over)])


def test_cli_never_shows_a_traceback(tmp_path):
    d = session(tmp_path, sample(barriers={"other": 5}))
    for args in (["skeleton", str(d)], ["build", str(d)]):
        r = subprocess.run([sys.executable, str(CRIT_DIR / "scripts" / "criteria.py")] + args,
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 1 and "Traceback" not in r.stderr and "❌" in r.stdout, r.stderr


def test_verify_before_apply_and_quoted_steps(tmp_path):
    d, doc = _build_with(tmp_path, [P048])
    steps = [s["cmd"] for s in doc["apply_steps"] if s["cmd"]]
    assert steps[0].endswith(" --before-apply") and all("'" in s for s in steps if str(d.name) in s and " " in str(d))
    ok, lines = CR.verify(d, before_apply=True)
    assert ok and any("FB-2610-048 在上个月的需求反馈里" in x for x in lines)
    assert CR.verify(d)[0] is False
    q = CR.apply_steps(Path("/tmp/a b;$(id)"), 0)
    assert all("$(id)" not in s["cmd"] or "'" in s["cmd"] for s in q)


def test_coverage_map_names_real_pillars_observations():
    sys.path.insert(0, str(ROOT / "evaluators" / "shared"))
    import pillars  # noqa: E402
    obs = pillars.build([pillars.new_item("FB-1", fields={"important": True})], ["important"])["observations"]
    for key in ("capability_hit", "decisions", "format_error", "by_dimension", "by_case_type"):
        assert key in obs, key
    cat = CR.load_catalog()
    assert "capability_hit" in cat["capability_hit"]["evaluator"] and "decisions" in cat["decision_mix"]["evaluator"]
    assert cat["capability_hit"]["golden_requires"] == {"has_capabilities": True}
    level, _, fixable, warns = CR.coverage(crit(tier="观测", threshold="只看趋势", checks=["capability_hit"], golden_ids=["G10"]),
                                           cat, GOLDEN)  # G10 has no matched_capabilities
    assert level == "部分覆盖" and fixable and warns


def _py39():
    for cand in (shutil.which("python3.9"), "/usr/bin/python3"):
        if cand and os.path.exists(cand):
            r = subprocess.run([cand, "-c", "import sys;print(sys.version_info[:2] == (3, 9))"], capture_output=True, text=True)
            if r.stdout.strip() == "True":
                return cand
    return None


@pytest.mark.skipif(_py39() is None, reason="no Python 3.9 interpreter (the EC2 system python) on this host")
def test_scripts_run_on_python_3_9(tmp_path):
    py = _py39()
    d = session(tmp_path)
    _draft(d, ALL, [P048])
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    for args in ([str(FISH_DIR / "scripts" / "generate_fishbone.py"), str(d)],
                 [str(CRIT_DIR / "scripts" / "criteria.py"), "build", str(d)],
                 ["-m", "labkit.define_pages", str(d), "--no-upload"]):
        r = subprocess.run([py] + args, capture_output=True, text=True, cwd=str(ROOT), env=env, timeout=120)
        assert r.returncode == 0, (args, r.stdout[-500:], r.stderr[-800:])
