"""tools/pull-templates.sh (labkit.pull_templates): bucket -> data/templates/. No AWS calls."""
import io
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from labkit import pull_templates as pt  # noqa: E402


@pytest.fixture(autouse=True)
def no_real_aws(monkeypatch):
    """These tests must never reach AWS: a developer laptop's config/local.env can name a real
    profile (an early red run of the --prepare/--dry-run test created a real bucket that way)."""
    from labkit import aws

    def boom(*a, **k):
        raise AssertionError("unit test tried to call real AWS")
    for name in ("session", "client", "account_id", "bucket_name"):
        monkeypatch.setattr(aws, name, boom)

MD = "discussion-feedback.md"
CSV = "golden-set-feedback.csv"


def _pristine(name):
    """The committed template (git HEAD), not the working copy: on a lab EC2 the working copy is
    exactly what pull-templates.sh just replaced with the group's edited file."""
    return pt.git_default(ROOT, name) or (ROOT / "data" / "templates" / name).read_bytes()


DEFAULT_MD = _pristine(MD)
DEFAULT_CSV = _pristine(CSV)


class FakeS3:
    def __init__(self, objects):
        # key -> (bytes, LastModified)
        self.objects = objects
        self.gets = []
        self.kwargs = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        fake = self

        class P:
            def paginate(self, Bucket, **kw):
                fake.kwargs.append(("list", kw))
                keys = sorted(fake.objects)
                for i in range(0, len(keys), 2):  # several pages
                    yield {"Contents": [{"Key": k, "Size": len(fake.objects[k][0]), "LastModified": fake.objects[k][1]}
                                        for k in keys[i:i + 2]]}
        return P()

    def get_object(self, Bucket, Key, **kw):
        self.gets.append(Key)
        self.kwargs.append(("get", kw))
        return {"Body": io.BytesIO(self.objects[Key][0])}


def t(h):
    return datetime(2026, 11, 7, h, 0, tzinfo=timezone.utc)


def edited_csv_gbk():
    text = DEFAULT_CSV.decode("utf-8-sig")
    lines = text.splitlines()
    lines[10] = lines[10].replace(",保留,", ",修改,", 1)
    lines.append("," + ",新增,是,否,迭代,迭代,P1,是,认知,简单,,,测试理由,新标题,新需求描述足够长的一段文字,示例客户（示例）,普通,报表,新需求,否,")
    return "\r\n".join(lines).encode("gb18030")


@pytest.fixture
def repo(tmp_path):
    tdir = tmp_path / "data" / "templates"
    tdir.mkdir(parents=True)
    (tdir / MD).write_bytes(DEFAULT_MD)
    (tdir / CSV).write_bytes(DEFAULT_CSV)
    return tmp_path


def run(repo, s3, **kw):
    lines = []
    kw.setdefault("default_reader", lambda root, name: {MD: DEFAULT_MD, CSV: DEFAULT_CSV}[name])
    rc = pt.Puller(s3, "s12lab-zhang3-000000000000-us-west-2", root=repo, now=datetime(2026, 11, 7, 14, 5, 9),
                   out=lines.append, **kw).run()
    return rc, "\n".join(lines)


def test_copies_both_converts_gbk_and_backs_up(repo):
    md = DEFAULT_MD.decode("utf-8").replace("小组：【填写】", "小组：第 3 组").encode("utf-8")
    s3 = FakeS3({"templates/" + MD: (md, t(6)), "templates/" + CSV: (edited_csv_gbk(), t(6))})
    rc, out = run(repo, s3)
    assert rc == 0, out
    tdir = repo / "data" / "templates"
    assert (tdir / MD).read_bytes() == md
    got = (tdir / CSV).read_bytes()
    assert got.startswith(b"\xef\xbb\xbf") and "新增" in got.decode("utf-8-sig")  # GBK -> UTF-8 with BOM
    assert "GBK → UTF-8" in out and "修改 1" in out and "新增 1" in out
    bdir = tdir / ".backup" / "20261107-140509"
    assert (bdir / MD).read_bytes() == DEFAULT_MD and (bdir / CSV).read_bytes() == DEFAULT_CSV
    assert "已备份到 data/templates/.backup/20261107-140509/" in out
    assert "默认模板完全一样" not in out


def test_rerun_is_idempotent_no_second_backup(repo):
    md = DEFAULT_MD.replace("【填写】".encode(), b"X", 1)
    s3 = FakeS3({MD: (md, t(6)), CSV: (edited_csv_gbk(), t(6))})
    assert run(repo, s3)[0] == 0
    rc, out = run(repo, s3)
    assert rc == 0 and out.count("与 EC2 上现有文件相同") == 2
    assert len([d for d in (repo / "data" / "templates" / ".backup").iterdir() if d.is_dir()]) == 1


def test_missing_file_warns_keeps_local_and_exit_zero(repo):
    s3 = FakeS3({CSV: (edited_csv_gbk(), t(6))})
    rc, out = run(repo, s3)
    assert rc == 0
    assert f"⚠️  {MD}：桶里没找到这个文件" in out
    assert (repo / "data" / "templates" / MD).read_bytes() == DEFAULT_MD


def test_nothing_uploaded_fails(repo):
    rc, out = run(repo, FakeS3({"reports/compare.html": (b"<html>", t(6))}))
    assert rc == 1 and "一个模板都没找到" in out


def test_unchanged_upload_is_flagged_even_after_excel_resave(repo):
    # Excel on Chinese Windows: plain Save -> GBK, CRLF, no BOM; content identical to the repo default
    resaved = DEFAULT_CSV.decode("utf-8-sig").replace("\n", "\r\n").encode("gb18030")
    s3 = FakeS3({CSV: (resaved, t(6)), MD: (DEFAULT_MD, t(6))})
    rc, out = run(repo, s3)
    assert rc == 0
    assert f"⚠️  {CSV}：内容和仓库里的默认模板完全一样" in out
    assert f"⚠️  {MD}：内容和仓库里的默认模板完全一样" in out


def test_newest_exact_wins_and_near_name_used_only_as_fallback(repo):
    newer = DEFAULT_MD.replace("【填写】".encode(), b"NEW", 1)
    s3 = FakeS3({MD: (DEFAULT_MD, t(5)), "templates/" + MD: (newer, t(7)),
                 "golden-set-feedback (1).csv": (edited_csv_gbk(), t(6))})
    rc, out = run(repo, s3)
    assert rc == 0
    assert (repo / "data" / "templates" / MD).read_bytes() == newer
    assert f"忽略没改过的 {MD}" in out
    assert "改用名字相近的 golden-set-feedback (1).csv" in out


def test_wrong_file_type_is_explained(repo):
    s3 = FakeS3({"golden-set-feedback.xlsx": (b"PK\x03\x04", t(6)), MD: (DEFAULT_MD + b"x", t(6))})
    rc, out = run(repo, s3)
    assert rc == 0
    assert "找到 golden-set-feedback.xlsx，但文件类型不对" in out and "CSV UTF-8" in out
    assert pt.Puller is not None and "xlsx" not in "".join(s3.gets)  # never downloaded


def test_csv_without_expected_header_is_not_copied(repo):
    s3 = FakeS3({CSV: ("a,b\n1,2\n".encode("utf-8"), t(6))})
    rc, out = run(repo, s3)
    assert rc == 1 and "不像黄金集反馈表" in out
    assert (repo / "data" / "templates" / CSV).read_bytes() == DEFAULT_CSV


def test_dry_run_writes_nothing(repo):
    s3 = FakeS3({CSV: (edited_csv_gbk(), t(6))})
    rc, out = run(repo, s3, dry_run=True)
    assert rc == 0 and "将拷贝" in out and "将备份到" in out
    assert (repo / "data" / "templates" / CSV).read_bytes() == DEFAULT_CSV
    assert not (repo / "data" / "templates" / ".backup").exists()


def test_refuses_symlinked_destination(repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "victim.csv"
    outside.write_bytes(b"keep")
    dest = repo / "data" / "templates" / CSV
    dest.unlink()
    dest.symlink_to(outside)
    rc, out = run(repo, FakeS3({CSV: (edited_csv_gbk(), t(6))}))
    assert rc == 1 and "拒绝写入" in out
    assert outside.read_bytes() == b"keep"


def test_refuses_symlinked_backup_dir(repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (repo / "data" / "templates" / ".backup").symlink_to(outside, target_is_directory=True)
    rc, out = run(repo, FakeS3({CSV: (edited_csv_gbk(), t(6))}))
    assert rc == 1 and "拒绝写入" in out
    assert list(outside.iterdir()) == []


def test_only_allowlisted_names_and_real_templates_dir(repo):
    with pytest.raises(pt.PullError):
        pt.safe_dest(repo / "data" / "templates", repo, "../../config/local.env")
    with pytest.raises(pt.PullError):
        pt.safe_dest(repo / "elsewhere", repo, CSV)


@pytest.mark.parametrize("arg,ok", [
    ("s12lab-zhang3-123456789012-us-west-2", True), ("s3://s12lab-zhang3-123456789012-us-west-2/", True),
    ("my-company-data", False), ("s12lab-", False), ("s12lab-../x", False), ("S12LAB-ZHANG3", False)])
def test_bucket_argument_limited_to_lab_buckets(arg, ok):
    if ok:
        assert pt.normalize_bucket(arg) == "s12lab-zhang3-123456789012-us-west-2"
    else:
        with pytest.raises(pt.PullError):
            pt.normalize_bucket(arg)


def test_decode_variants():
    s = "操作,golden_id\n保留,G01\n"
    assert pt.decode(s.encode("gb18030")) == (s, "GBK")
    assert pt.decode(b"\xef\xbb\xbf" + s.encode("utf-8")) == (s, "UTF-8")
    assert pt.decode(s.encode("utf-16")) == (s, "UTF-16")


def test_output_is_readable_by_golden_tool_reader(repo):
    """The copied CSV must parse with the same reader apply-feedback uses."""
    sys.path.insert(0, str(ROOT / "data" / "tools"))
    import common  # noqa: E402
    s3 = FakeS3({CSV: (edited_csv_gbk(), t(6))})
    assert run(repo, s3)[0] == 0
    rows = common.read_csv(str(repo / "data" / "templates" / CSV))
    assert [r["操作"] for r in rows].count("新增") == 1 and [r["操作"] for r in rows].count("修改") == 1


def test_newer_upload_under_another_name_or_type_is_flagged(repo):
    """Live run finding: an exact-name file was used while a NEWER 'golden-set-feedback.xlsx' /
    'golden-set-feedback (1).csv' (the copy actually edited last) was ignored without a word."""
    s3 = FakeS3({CSV: (edited_csv_gbk(), t(5)),
                 "golden-set-feedback (1).csv": (edited_csv_gbk() + b"\r\n", t(6)),
                 "golden-set-feedback.xlsx": (b"PK\x03\x04", t(7)),
                 MD: (DEFAULT_MD + b"x", t(6)), "old/" + MD: (DEFAULT_MD + b"x", t(4))})
    rc, out = run(repo, s3)
    assert rc == 0
    assert "忽略内容相同的 golden-set-feedback (1).csv" in out  # same content: nothing to choose
    assert f"⚠️  {CSV}：桶里还有更新的 golden-set-feedback.xlsx" in out
    assert f"忽略内容相同的 old/{MD}" in out and f"桶里还有更新的 old/{MD}" not in out


# ---- adversarial review round 1 (see code-review.md) ----------------------------------------------
def test_r1_edited_near_name_beats_unedited_exact_name_uploaded_together(repo):
    """F1: exact-name unedited copy + edited '(1)' copy dragged in together (same second)."""
    s3 = FakeS3({CSV: (DEFAULT_CSV, t(6)), "golden-set-feedback (1).csv": (edited_csv_gbk(), t(6))})
    rc, out = run(repo, s3)
    assert rc == 0, out
    got = (repo / "data" / "templates" / CSV).read_bytes().decode("utf-8-sig")
    assert "新增" in got and "改用名字相近的 golden-set-feedback (1).csv" in out
    assert f"忽略没改过的 {CSV}" in out


def test_r1_two_different_edited_copies_stop_and_ask(repo):
    other = edited_csv_gbk().decode("gb18030").replace("测试理由", "另一个理由").encode("gb18030")
    s3 = FakeS3({CSV: (edited_csv_gbk(), t(6)), "templates/" + CSV: (other, t(7))})
    rc, out = run(repo, s3)
    assert rc == 1 and "2 份内容不同" in out
    assert (repo / "data" / "templates" / CSV).read_bytes() == DEFAULT_CSV


def test_r1_unedited_upload_does_not_overwrite_edits_made_on_ec2(repo):
    """F2: the md was edited on EC2 (Claude Code fallback); the bucket only has the untouched one."""
    md = repo / "data" / "templates" / MD
    md.write_bytes(DEFAULT_MD.replace("【填写】".encode(), b"EC2-EDIT", 1))
    rc, out = run(repo, FakeS3({MD: (DEFAULT_MD, t(6))}))
    assert rc == 0 and b"EC2-EDIT" in md.read_bytes()
    assert "保留 EC2 上的" in out and "--force" in out
    rc, out = run(repo, FakeS3({MD: (DEFAULT_MD, t(6))}), force=True)
    assert rc == 0 and md.read_bytes() == DEFAULT_MD


def test_r1_cr_only_csv_is_copied_and_readable(repo):
    """F3: classic Mac 'CSV' line endings (CR only) used to raise _csv.Error mid-run."""
    cr = edited_csv_gbk().replace(b"\r\n", b"\r")
    rc, out = run(repo, FakeS3({CSV: (cr, t(6)), MD: (DEFAULT_MD + b"x", t(6))}))
    assert rc == 0, out
    sys.path.insert(0, str(ROOT / "data" / "tools"))
    import common  # noqa: E402
    rows = common.read_csv(str(repo / "data" / "templates" / CSV))
    assert [r["操作"] for r in rows].count("新增") == 1


def test_r1_lost_chinese_header_gets_the_right_hint(repo):
    mangled = DEFAULT_CSV.decode("utf-8-sig").replace("操作", "??").encode("utf-8")
    rc, out = run(repo, FakeS3({CSV: (mangled, t(6))}))
    assert rc == 1 and "中文" in out and "CSV UTF-8" in out


@pytest.mark.parametrize("key", ["golden-set-feedback - 副本.csv", "第3组-golden-set-feedback.csv",
                                 "golden-set-feedback copy.csv", "golden-set-feedback.csv.txt"])
def test_r1_renamed_copies_are_used_with_a_rename_hint(repo, key):
    """F4: renamed copies of the right type are not 'wrong type'."""
    rc, out = run(repo, FakeS3({key: (edited_csv_gbk(), t(6))}))
    assert rc == 0 and f"改用名字相近的 {key}" in out and "文件类型不对" not in out


def test_r1_summary_mentions_warnings(repo):
    """F6: a run with warnings must not end with a plain 'done'."""
    rc, out = run(repo, FakeS3({CSV: (edited_csv_gbk(), t(6))}))
    assert rc == 0 and "条提醒" in out


def test_r1_prepare_with_dry_run_is_rejected_before_any_aws_call():
    """F9: --dry-run promises no writes, but --prepare creates a bucket."""
    assert pt.main(["--prepare", "--dry-run"]) == 1


def test_r1_bucket_owner_is_pinned(repo):
    """F11: an s12lab-* bucket in another account must not be read."""
    s3 = FakeS3({CSV: (edited_csv_gbk(), t(6))})
    lines = []
    pt.Puller(s3, "s12lab-zhang3-000000000000-us-west-2", root=repo, owner="000000000000",
              default_reader=lambda r, n: None, out=lines.append).run()
    assert s3.kwargs and all(kw.get("ExpectedBucketOwner") == "000000000000" for _, kw in s3.kwargs)


# ---- adversarial review round 2 (see code-review.md) ----------------------------------------------
def _edited_v2():
    return edited_csv_gbk().decode("gb18030").replace("测试理由", "第二版理由").encode("gb18030")


def test_r2_ec2_edit_after_pull_survives_a_changed_upload(repo):
    """F1: pull, then the coding agent edits the CSV on EC2, then the recorder uploads a new version."""
    dest = repo / "data" / "templates" / CSV
    assert run(repo, FakeS3({CSV: (edited_csv_gbk(), t(6))}))[0] == 0
    dest.write_bytes(dest.read_bytes().replace("测试理由".encode(), "AGENT".encode()))
    rc, out = run(repo, FakeS3({CSV: (_edited_v2(), t(7))}))
    assert rc == 0 and "AGENT".encode() in dest.read_bytes() and "保留 EC2 上的" in out
    rc, out = run(repo, FakeS3({CSV: (_edited_v2(), t(7))}), force=True)
    assert rc == 0 and "第二版理由".encode() in dest.read_bytes()


def test_r2_rerun_with_unchanged_bucket_keeps_ec2_edits(repo):
    dest = repo / "data" / "templates" / CSV
    s3 = FakeS3({CSV: (edited_csv_gbk(), t(6))})
    assert run(repo, s3)[0] == 0
    dest.write_bytes(dest.read_bytes().replace("测试理由".encode(), "AGENT".encode()))
    rc, out = run(repo, s3)
    assert rc == 0 and "AGENT".encode() in dest.read_bytes() and "保留 EC2 上的" in out


def test_r2_unknown_repo_default_never_overwrites_silently(repo):
    """F6: no git (zip download) -> cannot tell pristine from edited: keep and say so."""
    (repo / "data" / "templates" / MD).write_bytes(DEFAULT_MD.replace("【填写】".encode(), b"EC2", 1))
    rc, out = run(repo, FakeS3({MD: (DEFAULT_MD + b"x", t(6))}), default_reader=lambda r, n: None)
    assert rc == 0 and "读不到仓库里的默认模板" in out and "保留 EC2 上的" in out


def test_r2_only_edited_copy_invalid_is_an_error_not_a_silent_default(repo):
    """F4: the edited copy fails validation -> do not fall back to the unedited one."""
    renamed = edited_csv_gbk().decode("gb18030").replace(",操作,", ",动作,", 1).encode("gb18030")
    rc, out = run(repo, FakeS3({CSV: (DEFAULT_CSV, t(6)), "golden-set-feedback (1).csv": (renamed, t(6))}))
    assert rc == 1 and "表头被改" in out
    assert (repo / "data" / "templates" / CSV).read_bytes() == DEFAULT_CSV


def test_r2_all_candidates_are_considered(repo):
    """F5: many newer unedited copies must not hide the older edited one."""
    objs = {f"golden-set-feedback ({i}).csv": (DEFAULT_CSV, t(10)) for i in range(1, 8)}
    objs[CSV] = (DEFAULT_CSV, t(10))
    objs["golden-set-feedback (0).csv"] = (edited_csv_gbk(), t(5))  # oldest, 9th candidate
    rc, out = run(repo, FakeS3(objs))
    assert rc == 0 and "新增" in (repo / "data" / "templates" / CSV).read_bytes().decode("utf-8-sig")


def test_r2_doubled_quotes_after_the_sniffed_prefix_are_kept(repo, tmp_path):
    """F3: Excel writes  "he said ""hi"", ok"  -- the 4 KB sniff sample has no quotes, so the
    sniffed dialect had doublequote=False and the cell split into the next columns."""
    sys.path.insert(0, str(ROOT / "data" / "tools"))
    import common  # noqa: E402
    text = DEFAULT_CSV.decode("utf-8-sig")
    assert '"' not in text[:4096]
    row = ',,新增,是,否,迭代,迭代,P1,是,认知,简单,,,"he said ""hi"", ok",新标题,新需求描述足够长的一段文字,示例（示例）,普通,报表,新需求,否,'
    p = tmp_path / "q.csv"
    p.write_text(text.rstrip("\n") + "\n" + row + "\n", encoding="utf-8-sig")
    last = common.read_csv(str(p))[-1]
    assert last["标注理由"] == 'he said "hi", ok' and last["标题"] == "新标题"
    assert pt.comparable(CSV, p.read_text(encoding="utf-8-sig"))[-1][13] == 'he said "hi", ok'


def test_r2_prepare_reapplies_public_access_block():
    """F8: an existing lab bucket without the full block is made private again (fail closed)."""
    from botocore.exceptions import ClientError

    class S3:
        puts = []

        def get_public_access_block(self, **kw):
            raise ClientError({"Error": {"Code": "NoSuchPublicAccessBlockConfiguration"}}, "GetPublicAccessBlock")

        def put_public_access_block(self, **kw):
            self.puts.append(kw)

    s3 = S3()
    assert pt.ensure_private(s3, "s12lab-zhang3-000000000000-us-west-2", "000000000000") == "reapplied"
    assert all(s3.puts[0]["PublicAccessBlockConfiguration"].values())

    class Denied(S3):
        def get_public_access_block(self, **kw):
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetPublicAccessBlock")

    with pytest.raises(pt.PullError):
        pt.ensure_private(Denied(), "s12lab-zhang3-000000000000-us-west-2", "000000000000")


def test_r2_config_error_is_a_friendly_message(monkeypatch, capsys):
    """F10: python -m labkit.pull_templates without a suffix must not dump a traceback."""
    from labkit import aws, config
    monkeypatch.setattr(aws, "cfg", lambda: (_ for _ in ()).throw(config.ConfigError("LAB_SUFFIX 未设置")))
    assert pt.main([]) == 1 and "LAB_SUFFIX" in capsys.readouterr().err


def test_unit_tests_cannot_reach_aws():
    """Guard for the guard: a real --prepare from the test suite must fail before any AWS call."""
    try:
        rc = pt.main(["--prepare"])
    except AssertionError as e:
        assert "real AWS" in str(e)
    else:
        assert rc == 1  # no LAB_SUFFIX on this machine: stopped by the config check


# ----------------------------------------------------------------------------- next steps (2.4 before 2.5)
def _next_steps(out):
    lines = out.splitlines()
    i = next(k for k, line in enumerate(lines) if line.startswith("下一步"))
    return lines[i + 1:]


def _step(steps, sec):
    """(index, line) of the numbered step for manual section `sec` ("  1. 2.4 ..."), not a mere mention."""
    return next((k, line) for k, line in enumerate(steps) if re.match(rf"\s*\d\. {re.escape(sec)} ", line))


def test_next_steps_put_mock_data_2_4_before_golden_set_when_section_5_is_filled(repo):
    md = DEFAULT_MD.decode("utf-8").replace("上个月大概有多少条需求反馈：【填写】", "上个月大概有多少条需求反馈：约 300 条")
    rc, out = run(repo, FakeS3({MD: (md.encode("utf-8"), t(6)), CSV: (edited_csv_gbk(), t(6))}))
    assert rc == 0, out
    steps = _next_steps(out)
    k24, l24 = _step(steps, "2.4")
    k25, l25 = _step(steps, "2.5")
    assert k24 < k25 < _step(steps, "2.6")[0]
    assert "02-regenerate-mock-data.md" in l24 and "跳过" not in l24
    assert "03-update-golden-set.md" in "\n".join(steps[k25:k25 + 2]) and "apply-feedback --dry-run" in "\n".join(steps)


def test_next_steps_mark_2_4_skipped_when_section_5_is_untouched(repo):
    md = DEFAULT_MD.decode("utf-8").replace("小组：【填写】", "小组：第 3 组")
    rc, out = run(repo, FakeS3({MD: (md.encode("utf-8"), t(6)), CSV: (edited_csv_gbk(), t(6))}))
    assert rc == 0, out
    steps = _next_steps(out)
    k24, l24 = _step(steps, "2.4")
    assert "跳过" in l24 and "02-regenerate-mock-data.md" not in l24
    assert k24 < _step(steps, "2.5")[0] < _step(steps, "2.6")[0]      # same numbered order either way
    assert [s.split(".", 1)[0].strip() for s in steps if s.strip()[:2] in ("1.", "2.", "3.", "4.")][:4] == ["1", "2", "3", "4"]


def test_section_5_detection():
    new = DEFAULT_MD.decode("utf-8")
    f = pt.mock_feedback_given
    assert f(new, new) is False
    assert f(new.replace("实际占多少）：【填写】", "实际占多少）：已有能力被重复提出大约占三成"), new) is True
    assert f(new + "\n- 希望多一些移动端离线的需求\n", new) is True                 # added bullet
    assert f(new + "\n另外：渠道里工单系统占比偏低。\n", new) is True               # added free text
    assert f(new.replace("：【填写】\n- 主要客户行业", "：【填写】 约 40%\n- 主要客户行业"), new) is True
    # an older copy of the template (different intro wording, placeholders untouched) is not "filled"
    sec5 = new.split("## 5.", 1)[1]
    old = new.split("## 5.", 1)[0] + "## 5. 对 mock 数据的意见（旧标题）\n\n" + "\n".join(
        line for line in sec5.splitlines() if line.startswith("- ")) + "\n"
    assert f(old, new) is False
    # "基本符合" / "无" answers mean no change: 2.4 is skipped
    ok = new.replace("实际占多少）：【填写】", "实际占多少）：基本符合").replace("举 1~2 个例子）：【填写】", "举 1~2 个例子）：无。")
    assert f(ok, new) is False
    assert f(ok.replace("上个月大概有多少条需求反馈：【填写】", "上个月大概有多少条需求反馈：基本符合，约 80 条"), new) is True
    # a question holding a link (https://...) is not mistaken for an answer, with or without the repo default
    linked = new.replace("实际占多少）：【填写】", "实际占多少，见 [分布](https://example.com/mock-data.html#dist-category)）：【填写】")
    assert f(linked, linked) is False and f(linked, None) is False
    assert f(linked.replace("#dist-category)）：【填写】", "#dist-category)）：三成"), linked) is True
    # without a repo default only the placeholders count
    assert f(new, None) is False
    assert f(new.replace("实际占多少）：【填写】", "实际占多少）：三成"), None) is True


def test_guide_expected_output_matches_the_next_steps_printed(repo):
    md = DEFAULT_MD.decode("utf-8").replace("上个月大概有多少条需求反馈：【填写】", "上个月大概有多少条需求反馈：约 300 条")
    rc, out = run(repo, FakeS3({MD: (md.encode("utf-8"), t(6)), CSV: (edited_csv_gbk(), t(6))}))
    lines = out.splitlines()
    block = lines[next(k for k, line in enumerate(lines) if line.startswith("下一步")):]
    guide = (ROOT / "docs" / "02-data.html").read_text(encoding="utf-8")
    s20 = guide.split('id="s-2-0"', 1)[1].split('id="s-2-1"', 1)[0]
    for line in block:
        assert line in s20, line
    assert 'id="s-2-0-next"' in s20 and all(f'href="#s-2-{k}"' in s20 for k in range(1, 7))


def test_section_3_catalog_detection():
    new = DEFAULT_MD.decode("utf-8")
    f = pt.catalog_feedback_given
    assert f(new, new) is False and f(new, None) is False
    row = new.replace("| 【填写】 | | | | |", "| 报表订阅推送到企业微信 | 部分支持 | 可订阅邮件 | 不能推 IM | 用开放 API |")
    assert f(row, new) is True and f(row, None) is True                       # a table row filled in
    fix = new.replace("能力目录里写错的地方：【填写，例如“CAP-012 报表订阅其实已支持 IM 推送”】", "能力目录里写错的地方：CAP-012 其实已支持 IM 推送")
    assert f(fix, new) is True
    none = new.replace("能力目录里写错的地方：【填写，例如“CAP-012 报表订阅其实已支持 IM 推送”】", "能力目录里写错的地方：无")
    assert f(none, new) is False
    assert pt.mock_feedback_given(row, new) is False                           # sections are independent


def test_next_steps_add_prompt_04_and_the_b_note_when_only_section_3_changed(repo):
    md = DEFAULT_MD.decode("utf-8").replace("| 【填写】 | | | | |", "| 报表订阅推送到企业微信 | 部分支持 | | | |")
    rc, out = run(repo, FakeS3({MD: (md.encode("utf-8"), t(6)), CSV: (edited_csv_gbk(), t(6))}))
    steps = _next_steps(out)
    assert "跳过" in _step(steps, "2.4")[1]
    block = "\n".join(steps)
    assert block.index("04-update-capability-catalog.md") < block.index("apply-feedback --dry-run") < block.index("03-update-golden-set.md")
    last = [line for line in steps if line.strip()][-1]
    assert last.lstrip().startswith("4.") and "./20-B-deploy-runtime.sh" in last and "能力目录" in last and "mock" not in last


def test_crlf_template_is_read_the_same():
    new = DEFAULT_MD.decode("utf-8")
    crlf = new.replace("\n", "\r\n")
    assert pt.mock_feedback_given(crlf, new) is False and pt.catalog_feedback_given(crlf, new) is False
    assert pt.mock_feedback_given(crlf.replace("反馈：【填写】", "反馈：约 300 条"), new) is True


def test_colons_inside_an_answer_are_kept():
    new = DEFAULT_MD.decode("utf-8")
    q3 = "能力目录里写错的地方：【填写，例如“CAP-012 报表订阅其实已支持 IM 推送”】"
    for ans in ("能力目录里写错的地方：CAP-012：其实已支持 IM 推送", "能力目录里写错的地方:CAP-012:其实已支持 IM 推送"):
        assert pt.catalog_feedback_given(new.replace(q3, ans), new) is True, ans   # needs the repo default (git)
    m = new.replace("- 主要客户行业、来源渠道：【填写】", "- 主要客户行业、来源渠道：行业：制造、零售；渠道：无")
    assert pt.mock_feedback_given(m, new) is True and pt.mock_feedback_given(m, None) is True


def test_section_3_answer_on_its_own_line_counts_but_intro_edits_do_not():
    new = DEFAULT_MD.decode("utf-8")
    q3 = "能力目录里写错的地方：【填写，例如“CAP-012 报表订阅其实已支持 IM 推送”】"
    below = new.replace(q3, q3 + "\nCAP-012 其实已支持 IM 推送")
    replaced = new.replace(q3, "CAP-012 其实已支持 IM 推送")
    assert pt.catalog_feedback_given(below, new) is True and pt.catalog_feedback_given(replaced, new) is True
    sec3 = new.split("## 3.", 1)[1].split("## 4.", 1)[0]
    intro = sec3.strip().splitlines()[0]
    assert pt.catalog_feedback_given(new.replace(intro, "（讲师改过的说明文字）"), new) is False


def test_lines_above_the_table_count_unless_an_older_template_version_has_them():
    new = DEFAULT_MD.decode("utf-8")
    sec3 = new.split("## 3.", 1)[1].split("## 4.", 1)[0]
    intro2 = [line for line in sec3.splitlines() if line.startswith("缺的能力")][0]
    added = new.replace(intro2, intro2 + "\n缺少企业微信推送能力")
    assert pt.catalog_feedback_given(added, new) is True
    old = pt.git_history(ROOT, MD)
    assert old, "repo history of the template"
    # an older template version uploaded against the current HEAD: its intro lines are known, not answers
    for raw in old:
        o = raw.decode("utf-8")
        assert pt.catalog_feedback_given(o, new, [r.decode("utf-8") for r in old]) is False
        assert pt.mock_feedback_given(o, new, [r.decode("utf-8") for r in old]) is False


def test_more_no_change_replies():
    for v in ("暂无意见", "无需修改", "没有补充", "没有问题", "基本一致，无需调整", "基本符合，没意见。", "无。"):
        assert pt._no_change(v), v
    for v in ("基本符合，但缺移动端需求", "约 300 条", "无线网络相关需求偏少"):
        assert not pt._no_change(v), v
