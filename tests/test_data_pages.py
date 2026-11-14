"""Step 2.7: tools/publish-data-pages.sh -> labkit.data_pages (+ labkit.publish). No AWS calls: S3 is a stub."""
import copy
import hashlib
import html
import json
import os
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from labkit import aws  # noqa: E402
from labkit import data_pages as D  # noqa: E402
from labkit import publish as P  # noqa: E402
from labkit import pull_templates as PT  # noqa: E402

DATA = ROOT / "data"
CATALOG, FEEDBACK, GOLDEN = DATA / "capability_catalog.json", DATA / "feedback_last_month.jsonl", DATA / "golden" / "golden_set.jsonl"
FILES = {k: name for k, (name, _) in D.PAGES.items()}
PRIVATE = {k: True for k in PT._PAB_KEYS}
OWNER = "123456789012"


def _jsonl(p):
    return [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines() if x.strip()]


def _write_jsonl(p, rows):
    Path(p).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _read(d, kind):
    return (Path(d) / FILES[kind]).read_text(encoding="utf-8")


def _sha12(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:12]


class Tags(HTMLParser):
    """Every start tag with its attributes, as a browser would parse the page."""

    def __init__(self, page):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def _run(argv):
    return D.main([str(a) for a in argv])


# ---------------------------------------------------------------- rendering from the repo data
def test_repo_data_pages_are_self_contained_and_complete(tmp_path):
    assert _run(["--no-upload", "--out-dir", tmp_path]) == 0
    assert sorted(os.listdir(tmp_path)) == sorted(FILES.values())
    for kind in FILES:
        page = _read(tmp_path, kind)
        assert D.external_refs(page) == [], kind
        for leftover in ("assets/style.css", "assets/site.js", "data-repo=", "data-download=", "data-handbook-only",
                         'id="toc"', "<!--"):
            assert leftover not in page, (kind, leftover)
        assert "<style>" in page and 'http-equiv="Content-Security-Policy"' in page and "default-src 'none'" in page
        assert "这是 EC2 上数据文件的当前版本" in page and "原始样例" in page
        tags = Tags(page).tags
        assert all(not a.get("src") for _, a in tags) and not [t for t, _ in tags if t in ("link", "img", "iframe")]
        assert all((a.get("href") or "#").startswith("#") for t, a in tags if t == "a"), kind

    cat = json.loads(CATALOG.read_text(encoding="utf-8"))
    page = _read(tmp_path, "catalog")
    assert _sha12(CATALOG) in page and f"{len(cat['capabilities'])} 项" in page
    for c in cat["capabilities"]:
        assert f'<tr id="{c["id"]}">' in page and html.escape(c["name"]) in page

    fb = _jsonl(FEEDBACK)
    page = _read(tmp_path, "mock")
    assert _sha12(FEEDBACK) in page and f"上个月的 mock 需求反馈（{len(fb)} 条，合成）" in page
    assert page.count("<tr data-row ") == len(fb)
    for r in fb:
        assert f'id="{r["id"]}"' in page and html.escape(r["title"]) in page

    golden = _jsonl(GOLDEN)
    page = _read(tmp_path, "golden")
    assert _sha12(GOLDEN) in page and f"黄金集（{len(golden)} 条）" in page and "已冻结" in page
    rows = page.split("<tr data-row ")[1:]
    assert len(rows) == len(golden)
    for g, row in zip(golden, rows):
        row = row.split("</tr>", 1)[0]
        assert row.startswith(f'id="{g["golden_id"]}"') and g["feedback_id"] in row
        assert html.escape(g["input"]["title"]) in row and html.escape(g["input"]["content"]) in row
        e = g["expected"]
        assert '重要 <span class="pill' in row and f">{e['important']}</span>" in row and f">{e['decision']}</span>" in row
        for d in g["acceptable_decisions"] + g["dimensions"] + [g["case_type"]]:
            assert f">{d}</span>" in row, (g["golden_id"], d)
        assert ("不可自动拒绝" in row) == (g["can_auto_reject"] is False), g["golden_id"]
        for pt in g.get("reject_points", []) + g.get("reply_points", []):
            assert f"<li>{html.escape(pt)}</li>" in row, (g["golden_id"], pt)
        assert html.escape(g["rationale"]) in row
        for c in g.get("matched_capabilities", []):
            assert f">{c}</code>" in row


def test_handbook_pages_keep_the_original_sample_note(tmp_path):
    for name in ("catalog.html", "mock-data.html"):
        page = (ROOT / "docs" / name).read_text(encoding="utf-8")
        assert "data-handbook-only" in page and "原始样例" in page and "./tools/publish-data-pages.sh" in page
        assert 'href="02-data.html#s-2-7"' in page
    step = (ROOT / "docs" / "02-data.html").read_text(encoding="utf-8").split('id="s-2-7"', 1)[1].split('<div class="step"', 1)[0]
    assert "<pre><code>./tools/publish-data-pages.sh</code></pre>" in step and "原始样例" in step and "--no-upload" in step
    sh = ROOT / "tools" / "publish-data-pages.sh"
    assert os.access(sh, os.X_OK) and "labkit.data_pages" in sh.read_text(encoding="utf-8")


# ---------------------------------------------------------------- a modified dataset shows up as it is now
@pytest.fixture
def modified(tmp_path):
    d = tmp_path / "data"
    (d / "golden").mkdir(parents=True)
    cat = json.loads(CATALOG.read_text(encoding="utf-8"))
    cat["capabilities"].append({"id": "CAP-999", "name": "测试新增能力-当前版本", "module": "测试", "status": "规划中",
                                "description": "现场补充的能力", "limits": "", "workaround": "", "how_to": ""})
    cat["capabilities"][0]["name"] = "改过名字的能力-当前版本"
    (d / "capability_catalog.json").write_text(json.dumps(cat, ensure_ascii=False), encoding="utf-8")
    fb = _jsonl(FEEDBACK)
    dropped_fb = [r["id"] for r in fb[-3:]]
    fb = fb[:-3]
    fb[0]["title"] = "改过的标题-当前版本"
    _write_jsonl(d / "feedback_last_month.jsonl", fb)
    golden = _jsonl(GOLDEN)
    dropped_g = [g["golden_id"] for g in golden[-2:]]
    golden = golden[:-2]
    g = golden[0]
    g["expected"]["decision"] = "拒绝"
    g.update(acceptable_decisions=["拒绝", "待评估"], can_auto_reject=False, reject_points=["测试拒绝要点甲", "测试拒绝要点乙"],
             reply_points=["测试话术要点"], dimensions=["成本"], case_type="对抗", rationale="测试标注理由", version="v2")
    _write_jsonl(d / "golden" / "golden_set.jsonl", golden)
    shutil.copy(DATA / "golden" / "MANIFEST.json", d / "golden" / "MANIFEST.json")  # records the OLD sha: not frozen
    return {"dir": d, "catalog": d / "capability_catalog.json", "feedback": d / "feedback_last_month.jsonl",
            "golden": d / "golden" / "golden_set.jsonl", "n_cat": len(cat["capabilities"]), "n_fb": len(fb),
            "n_g": len(golden), "dropped_fb": dropped_fb, "dropped_g": dropped_g, "g01": g["golden_id"]}


def test_pages_reflect_the_current_modified_data(tmp_path, modified):
    out = tmp_path / "out"
    assert _run(["--no-upload", "--out-dir", out, "--catalog", modified["catalog"], "--feedback", modified["feedback"],
                 "--golden", modified["golden"]]) == 0
    page = _read(out, "catalog")
    assert f"{modified['n_cat']} 项" in page and "CAP-999" in page and "测试新增能力-当前版本" in page
    assert "改过名字的能力-当前版本" in page and _sha12(modified["catalog"]) in page

    page = _read(out, "mock")
    n = modified["n_fb"]
    assert f"<title>mock 需求（当前版本，{n} 条）" in page and f"（{n} 条）" in page and "改过的标题-当前版本" in page
    assert page.count("<tr data-row ") == n and _sha12(modified["feedback"]) in page
    for fid in modified["dropped_fb"]:
        assert f'id="{fid}"' not in page
    dist = page.split('<div id="dist-category">', 1)[1].split("</table>", 1)[0]
    counts = [int(c.split("</td>")[0]) for c in dist.split("</td><td>")[1::2]]
    assert sum(counts) == n

    page = _read(out, "golden")
    assert f"黄金集（{modified['n_g']} 条）" in page and _sha12(modified["golden"]) in page and "还没有冻结" in page
    row = page.split(f'<tr data-row id="{modified["g01"]}">', 1)[1].split("</tr>", 1)[0]
    for s in ('建议 <span class="pill r">拒绝</span>', '<span class="pill o">待评估</span>', "不可自动拒绝",
              "<li>测试拒绝要点甲</li>", "<li>测试拒绝要点乙</li>", "<li>测试话术要点</li>", ">成本</span>", ">对抗</span>",
              "测试标注理由", "v2"):
        assert s in row, s
    for gid in modified["dropped_g"]:
        assert f'id="{gid}"' not in page

    repo = tmp_path / "repo"
    assert _run(["--no-upload", "--out-dir", repo]) == 0
    for kind in FILES:
        assert _read(repo, kind) != _read(out, kind)


def test_golden_page_without_manifest_asks_to_freeze(tmp_path, modified):
    os.remove(modified["dir"] / "golden" / "MANIFEST.json")
    out = tmp_path / "o"
    assert _run(["--no-upload", "--only", "golden", "--out-dir", out, "--golden", modified["golden"]]) == 0
    assert "没找到冻结记录" in _read(out, "golden") and sorted(os.listdir(out)) == [FILES["golden"]]


def test_golden_rows_and_counts_agree_on_non_standard_values(tmp_path):
    """can_auto_reject null, dimensions as a string, a boolean label, a missing level: the row, the tables and
    the banner count must say the same thing (and the evaluators' reading: null = not auto-rejectable)."""
    golden = _jsonl(GOLDEN)
    a, b, c = golden[0], golden[1], golden[2]
    assert a["can_auto_reject"] is True
    a["can_auto_reject"] = None
    b["dimensions"] = "认知"
    c["expected"]["important"] = True
    c["expected"].pop("level", None)
    p = tmp_path / "g.jsonl"
    _write_jsonl(p, golden)
    out = tmp_path / "o"
    assert _run(["--no-upload", "--out-dir", out, "--only", "golden", "--golden", p]) == 0
    page = _read(out, "golden")
    row = page.split(f'<tr data-row id="{a["golden_id"]}">', 1)[1].split("</tr>", 1)[0]
    assert "不可自动拒绝" in row
    n_not = sum(1 for g in golden if not bool(g.get("can_auto_reject", True)))
    assert f"不可自动拒绝 {n_not} 条" in page
    dims = page.split("五维覆盖（一条可有多个维度）</h4>", 1)[1].split("</table>", 1)[0]
    assert ">认</td>" not in dims and ">知</td>" not in dims
    imp = page.split("<h4>是否重要</h4>", 1)[1].split("</table>", 1)[0]
    assert "True" not in imp and "-" not in imp.replace("—", "")
    assert "校验有" in page and "个错误" in page


def test_a_customer_file_is_not_called_synthetic(tmp_path, modified):
    cat = json.loads(Path(modified["catalog"]).read_text(encoding="utf-8"))
    cat["synthetic"] = False
    cat["platform"]["name"] = "客户自己的平台"
    Path(modified["catalog"]).write_text(json.dumps(cat, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "o"
    assert _run(["--no-upload", "--out-dir", out, "--catalog", modified["catalog"], "--feedback", modified["feedback"],
                 "--golden", modified["golden"]]) == 0
    for kind in FILES:
        page = _read(out, kind)
        assert "合成" not in page and "（示例）”" not in page, kind
        assert "来源不是仓库自带的示例数据文件" in page and "不要下载后外传" in page, kind
    repo = tmp_path / "repo"
    assert _run(["--no-upload", "--out-dir", repo]) == 0
    assert "（60 条，合成）" in _read(repo, "mock") and "来源不是仓库自带的示例数据文件" not in _read(repo, "mock")


def test_banner_wording_and_csp_per_page(tmp_path):
    assert _run(["--no-upload", "--out-dir", tmp_path]) == 0
    assert "手册里没有黄金集网页版" in _read(tmp_path, "golden") and "实验包里自带的是" not in _read(tmp_path, "golden")
    assert all("A/B 通用" not in _read(tmp_path, k) for k in ("catalog", "mock", "golden"))
    for kind in ("catalog", "mock"):
        assert "实验包里自带的是" in _read(tmp_path, kind) and "GitHub" not in _read(tmp_path, kind)
    import base64
    import re as _re
    for kind, n in (("catalog", 0), ("mock", 1), ("golden", 1)):
        page = _read(tmp_path, kind)
        csp = Tags(page).tags
        csp = next(a["content"] for t, a in csp if t == "meta" and a.get("http-equiv") == "Content-Security-Policy")
        script_src = csp.split("script-src ", 1)[1].split(";", 1)[0]
        assert "unsafe-inline" not in script_src and "default-src 'none'" in csp, kind
        scripts = _re.findall(r"<script>(.*?)</script>", page, _re.S)
        assert len(scripts) == n, kind
        want = ["'sha256-" + base64.b64encode(hashlib.sha256(js.encode()).digest()).decode() + "'" for js in scripts]
        assert script_src == (" ".join(want) or "'none'"), kind


# ---------------------------------------------------------------- escaping
EVIL = '<script>alert("x")</script><img src=x onerror=alert(1)>" onmouseover="alert(2)\'</style></textarea>url(https://evil.example/a)'


def test_every_data_field_is_html_escaped(tmp_path):
    d = tmp_path / "data"
    (d / "golden").mkdir(parents=True)
    cat = json.loads(CATALOG.read_text(encoding="utf-8"))
    cat["platform"]["name"] = cat["platform"]["positioning"] = EVIL
    cat["platform"]["principles"] = [EVIL]
    cat["version"] = EVIL
    cat["capabilities"][0].update({k: EVIL for k in ("id", "name", "module", "status", "description", "limits",
                                                     "workaround", "how_to")})
    (d / "c.json").write_text(json.dumps(cat, ensure_ascii=False), encoding="utf-8")
    fb = _jsonl(FEEDBACK)
    fb[0].update({k: EVIL for k in ("id", "title", "content", "customer", "industry", "customer_tier", "channel", "module",
                                    "submitted_at", "feedback_type", "contract_commitment")})
    _write_jsonl(d / "f.jsonl", fb)
    golden = _jsonl(GOLDEN)
    g = golden[0]
    g.update(golden_id=EVIL, feedback_id=EVIL, acceptable_decisions=[EVIL], reject_points=[EVIL], reply_points=[EVIL],
             rationale=EVIL, dimensions=[EVIL], case_type=EVIL, matched_capabilities=[EVIL], version=EVIL)
    g["input"] = {k: EVIL for k in g["input"]}
    g["expected"] = {k: EVIL for k in g["expected"]}
    _write_jsonl(d / "golden" / "g.jsonl", golden)
    assert _run(["--no-upload", "--out-dir", tmp_path / "o", "--catalog", d / "c.json", "--feedback", d / "f.jsonl",
                 "--golden", d / "golden" / "g.jsonl"]) == 0
    esc = html.escape(EVIL, quote=True)
    for kind, scripts in (("catalog", 0), ("mock", 1), ("golden", 1)):
        page = _read(tmp_path / "o", kind)
        assert '<script>alert' not in page and "<img" not in page and "</textarea>" not in page, kind
        assert esc in page, kind
        assert D.external_refs(page) == [], kind   # the text "url(https://…)" is data, not a load
        tags = Tags(page).tags
        assert [t for t, _ in tags if t in ("img", "textarea", "iframe", "link")] == [], kind
        assert not [(t, k) for t, a in tags for k in a if k.startswith("on")], kind
        assert [a for t, a in tags if t == "script"] == [{}] * scripts, kind
        assert page.count("</style>") == 1 and page.index("</style>") < page.index("</head>"), kind


def test_external_refs_flags_markup_but_not_escaped_text():
    for bad in ('<link rel="stylesheet" href="x.css">', '<script src="https://cdn/x.js"></script>', '<img src="a.png">',
                '<a href="https://github.com/x">x</a>', '<a href="catalog.html">x</a>', "<style>@import 'x.css';</style>",
                "<style>body{background:url(https://x/y.png)}</style>", '<div style="background:url(x.png)">',
                '<iframe></iframe>', '<form action="/x">', '<meta http-equiv="refresh" content="0;url=https://x">',
                '<object data="x.swf">', '<svg><use href="x.svg#a"></use></svg>', '<input type="image" src="x">'):
        assert D.external_refs(bad), bad
    ok = ('<td>&lt;img src=x&gt; url(https://x) @import src="y"</td><a href="#st-1">x</a>'
          '<tr data-row id="G1" data-repo="x"><style>.a{background:url(data:image/png;base64,AA)}</style>')
    assert D.external_refs(ok) == []


def test_a_page_that_would_load_something_is_never_written(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(D, "load_css", lambda: "@import url(https://fonts.example/x.css);")
    assert _run(["--no-upload", "--out-dir", tmp_path, "--only", "catalog"]) == 1
    assert "不是自包含的" in capsys.readouterr().out and os.listdir(tmp_path) == []


# ---------------------------------------------------------------- CLI behaviour
def test_only_flag_and_missing_source(tmp_path, capsys):
    assert _run(["--no-upload", "--out-dir", tmp_path, "--only", "catalog,mock"]) == 0
    assert sorted(os.listdir(tmp_path)) == sorted([FILES["catalog"], FILES["mock"]])
    out = tmp_path / "o2"
    assert _run(["--no-upload", "--out-dir", out, "--golden", tmp_path / "nope.jsonl"]) == 1
    assert "❌ 黄金集：无法生成" in capsys.readouterr().out
    assert sorted(os.listdir(out)) == sorted(FILES.values())
    page = _read(out, "golden")
    assert "黄金集这次没能生成" in page and "这是 EC2 上数据文件的当前版本" not in page and D.external_refs(page) == []
    for bad in (["--only", "everything"], ["--only", ""], ["--only", ","], ["--no-up"]):  # no abbreviations either
        with pytest.raises(SystemExit):
            _run(["--no-upload", "--out-dir", out] + bad if bad != ["--no-up"] else ["--out-dir", out] + bad)


def test_a_failed_rebuild_replaces_the_old_current_page(tmp_path, capsys):
    out = tmp_path / "o"
    assert _run(["--no-upload", "--out-dir", out, "--only", "golden"]) == 0
    assert "这是 EC2 上数据文件的当前版本" in _read(out, "golden")
    broken = tmp_path / "broken.jsonl"
    broken.write_text(GOLDEN.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")
    assert _run(["--no-upload", "--out-dir", out, "--only", "golden", "--golden", broken]) == 1
    page = _read(out, "golden")
    assert "这次没能生成" in page and "这是 EC2 上数据文件的当前版本" not in page and "不是合法 JSON" in page
    assert "已写入一页“生成失败”说明" in capsys.readouterr().out


def test_no_upload_makes_no_aws_call(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("AWS must not be called with --no-upload")
    monkeypatch.setattr(aws, "client", boom)
    monkeypatch.setattr(P, "publish", boom)
    assert _run(["--no-upload", "--out-dir", tmp_path]) == 0


def test_shell_wrapper_runs_locally_without_lab_config(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AWS_", "LAB_"))}
    env.update(AWS_SHARED_CREDENTIALS_FILE="/dev/null", AWS_CONFIG_FILE="/dev/null", AWS_EC2_METADATA_DISABLED="true",
               ADLC_LOCAL_ENV=str(tmp_path / "none.env"))
    r = subprocess.run(["bash", str(ROOT / "tools" / "publish-data-pages.sh"), "--no-upload", "--out-dir", str(tmp_path)],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert sorted(os.listdir(tmp_path)) == sorted(FILES.values()) and "没有上传" in r.stdout


# ---------------------------------------------------------------- upload path (stubbed S3)
def _client_error(code, op):
    from botocore.exceptions import ClientError
    return ClientError({"Error": {"Code": code, "Message": code}}, op)


class FakeS3:
    """Records calls; any S3 API not defined here (put_bucket_policy, put_object_acl, ...) fails the test.
    owner: the account that owns the bucket (a call with another ExpectedBucketOwner gets AccessDenied)."""

    def __init__(self, exists=True, pab=PRIVATE, fail_upload_of=None, owner=OWNER, unreadable=False):
        self.calls, self.objects, self.exists, self.pab = [], {}, exists, pab
        self.fail, self.owner, self.unreadable = fail_upload_of, owner, unreadable

    def _own(self, expected, op):
        if expected is not None and expected != self.owner:
            raise _client_error("AccessDenied", op)

    def head_bucket(self, Bucket, ExpectedBucketOwner=None):
        self.calls.append(("head_bucket", Bucket))
        if not self.exists:
            raise _client_error("404", "HeadBucket")

    def create_bucket(self, Bucket, **kw):
        self.calls.append(("create_bucket", Bucket, kw))
        self.exists = True

    def put_public_access_block(self, Bucket, PublicAccessBlockConfiguration, ExpectedBucketOwner=None):
        self.calls.append(("put_public_access_block", Bucket, dict(PublicAccessBlockConfiguration), ExpectedBucketOwner))
        self._own(ExpectedBucketOwner, "PutPublicAccessBlock")
        self.pab = dict(PublicAccessBlockConfiguration)

    def put_bucket_tagging(self, Bucket, Tagging):
        self.calls.append(("put_bucket_tagging", Bucket))

    def get_public_access_block(self, Bucket, ExpectedBucketOwner=None):
        self.calls.append(("get_public_access_block", Bucket, ExpectedBucketOwner))
        self._own(ExpectedBucketOwner, "GetPublicAccessBlock")
        if self.unreadable:
            raise _client_error("AccessDenied", "GetPublicAccessBlock")
        if self.pab is None:
            raise _client_error("NoSuchPublicAccessBlockConfiguration", "GetPublicAccessBlock")
        return {"PublicAccessBlockConfiguration": dict(self.pab)}

    def upload_file(self, Filename, Bucket, Key, ExtraArgs=None):
        self.calls.append(("upload_file", Bucket, Key, ExtraArgs))
        self._own((ExtraArgs or {}).get("ExpectedBucketOwner"), "PutObject")
        if self.fail and Key.endswith(self.fail):
            raise _client_error("AccessDenied", "PutObject")
        self.objects[Key] = (Path(Filename).read_bytes(), ExtraArgs)

    def __getattr__(self, name):
        raise AssertionError(f"unexpected S3 call: {name}")


BUCKET = "adlc-test-123456789012-us-west-2"
HTML_ARGS = {"ContentType": "text/html; charset=utf-8", "ExpectedBucketOwner": OWNER}


@pytest.fixture
def s3(monkeypatch):
    def install(**kw):
        fake = FakeS3(**kw)
        monkeypatch.setattr(aws, "client", lambda name: fake if name == "s3" else pytest.fail(f"unexpected client {name}"))
        monkeypatch.setattr(aws, "bucket_name", lambda: BUCKET)
        monkeypatch.setattr(aws, "account_id", lambda: OWNER)
        monkeypatch.setattr(aws, "cfg", lambda: {"AWS_REGION": "us-west-2", "LAB_SUFFIX": "test", "LAB_ROOT": str(ROOT)})
        return fake
    return install


def _names(fake):
    return [c[0] for c in fake.calls]


def test_upload_puts_each_page_under_reports_with_html_content_type(tmp_path, s3, capsys):
    fake = s3()
    assert _run(["--out-dir", tmp_path]) == 0
    keys = [f"reports/{FILES[k]}" for k in ("catalog", "mock", "golden")]
    ups = [c for c in fake.calls if c[0] == "upload_file"]
    assert [c[2] for c in ups] == keys
    assert all(c[1] == BUCKET and c[3] == HTML_ARGS for c in ups)  # exactly: no ACL, owner-checked
    for k in ("catalog", "mock", "golden"):
        assert fake.objects[f"reports/{FILES[k]}"][0] == (tmp_path / FILES[k]).read_bytes()
    names = _names(fake)
    assert "create_bucket" not in names and "put_public_access_block" not in names
    for i, n in enumerate(names):  # every upload is preceded by an owner-checked privacy check on the same bucket
        if n == "upload_file":
            assert fake.calls[i - 1] == ("get_public_access_block", BUCKET, OWNER)
    out = capsys.readouterr().out
    for key in keys:
        assert f"https://us-west-2.console.aws.amazon.com/s3/object/{BUCKET}?region=us-west-2&prefix={key}" in out
    assert "点右上角「打开」" in out


def test_new_bucket_is_created_private_before_the_first_upload(tmp_path, s3):
    fake = s3(exists=False, pab=None)
    assert _run(["--out-dir", tmp_path, "--only", "golden"]) == 0
    names = _names(fake)
    assert names.index("create_bucket") < names.index("put_public_access_block") < names.index("upload_file")
    assert next(c for c in fake.calls if c[0] == "put_public_access_block")[2] == PRIVATE


@pytest.mark.parametrize("pab", [None, dict(PRIVATE, RestrictPublicBuckets=False), dict(PRIVATE, BlockPublicAcls=None)])
def test_missing_or_partial_public_access_block_is_reapplied_before_upload(tmp_path, s3, capsys, pab):
    fake = s3(pab=pab)
    assert _run(["--out-dir", tmp_path, "--only", "golden"]) == 0
    names = _names(fake)
    put = next(c for c in fake.calls if c[0] == "put_public_access_block")
    assert put[2] == PRIVATE and put[3] == OWNER and names.index("put_public_access_block") < names.index("upload_file")
    assert "已重新开启" in capsys.readouterr().out


@pytest.mark.parametrize("kw", [{"unreadable": True}, {"owner": "999999999999"}])
def test_upload_is_refused_when_the_bucket_is_not_provably_ours_and_private(tmp_path, s3, capsys, kw):
    fake = s3(**kw)
    assert _run(["--out-dir", tmp_path]) == 1
    out = capsys.readouterr().out
    assert not [c for c in fake.calls if c[0] in ("upload_file", "put_public_access_block")]
    assert "未上传" in out and _names(fake).count("get_public_access_block") == 1  # stops at the first page
    with pytest.raises(P.NotPrivateError):  # the same guard for 51-compare.sh / 60-triage-report.sh
        P.publish(str(tmp_path / FILES["catalog"]))


def test_a_failed_build_uploads_the_failure_page(tmp_path, s3):
    fake = s3()
    assert _run(["--out-dir", tmp_path, "--only", "golden", "--golden", tmp_path / "nope.jsonl"]) == 1
    body, args = fake.objects[f"reports/{FILES['golden']}"]
    assert "这次没能生成" in body.decode("utf-8") and args == HTML_ARGS


def test_one_failed_upload_is_reported_and_the_rest_still_go(tmp_path, s3, capsys):
    fake = s3(fail_upload_of=FILES["mock"])
    assert _run(["--out-dir", tmp_path]) == 1
    out = capsys.readouterr().out
    assert "❌ mock 需求：上传失败" in out and "AccessDenied" in out
    assert sorted(fake.objects) == sorted([f"reports/{FILES['catalog']}", f"reports/{FILES['golden']}"])


def test_publish_cli_exits_1_when_the_bucket_is_not_private(tmp_path):
    """`python -m labkit.publish` (51-compare.sh, 60-triage-report.sh): a refused upload fails the script, not a traceback."""
    page = tmp_path / "x.html"
    page.write_text("<p>x</p>", encoding="utf-8")
    code = ("import sys, runpy; sys.argv=['labkit.publish', %r]\n"
            "from botocore.exceptions import ClientError\n"
            "from labkit import aws, runtime\n"
            "class S3:\n"
            "    def head_bucket(self, Bucket, **k): pass\n"
            "    def get_public_access_block(self, Bucket, **k):\n"
            "        raise ClientError({'Error': {'Code': 'AccessDenied'}}, 'GetPublicAccessBlock')\n"
            "    def upload_file(self, *a, **k): raise SystemExit('uploaded!')\n"
            "aws.client=lambda n: S3(); aws.bucket_name=lambda: 'b'; aws.account_id=lambda: '1'\n"
            "runpy.run_module('labkit.publish', run_name='__main__')\n") % str(page)
    env = dict(os.environ, AWS_SHARED_CREDENTIALS_FILE="/dev/null", AWS_CONFIG_FILE="/dev/null", PYTHONPATH=str(ROOT),
               LAB_SUFFIX="test", AWS_REGION="us-west-2", AWS_EC2_METADATA_DISABLED="true")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=60)
    assert r.returncode == 1 and "❌" in r.stderr and "未上传" in r.stderr and "uploaded!" not in r.stderr + r.stdout
    assert "Traceback" not in r.stderr


def test_modified_fixture_is_not_the_repo_data(modified):
    """Guard: the 'current data' test really changes every file (else it would prove nothing)."""
    for k, src in (("catalog", CATALOG), ("feedback", FEEDBACK), ("golden", GOLDEN)):
        assert Path(modified[k]).read_bytes() != src.read_bytes(), k
    assert copy.deepcopy(modified["dropped_g"])
