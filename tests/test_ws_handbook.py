"""The Workshop Studio handbook (workshop-studio/content): structure, Workshop Studio markdown rules, wording rules,
time budgets, and that every script, file, link, anchor and image it references is real. No AWS calls."""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WS = ROOT / "workshop-studio"
CONTENT = WS / "content"
PAGES = sorted(CONTENT.rglob("index.zh.md"))
FENCE = re.compile(r"^(`{3,})[^\n]*\n.*?^\1\s*$", re.S | re.M)
MAIN = {"01-environment": 1, "02-define": 2, "03-build": 3, "04-evaluate": 4, "05-optimize": 5,
        "06-release-observe": 6, "08-summary": 8, "09-cleanup": 9}
CHECKED = {"", "01-environment", "02-define/25-discussion", "03-build", "04-evaluate", "05-optimize",
           "06-release-observe", "08-summary", "09-cleanup"} | {f"90-extensions/{d.name}" for d in (CONTENT / "90-extensions").glob("e*")}


def rel(p: Path) -> str:
    return p.parent.relative_to(CONTENT).as_posix().replace(".", "")


def text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def front(p: Path) -> dict:
    m = re.match(r"---\n(.*?)\n---\n", text(p), re.S)
    assert m, f"{rel(p)}: front matter must start at byte 0"
    out = {}
    for line in m.group(1).splitlines():
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip()
    return out


def body(p: Path) -> str:
    return re.sub(r"\A---\n.*?\n---\n", "", text(p), count=1, flags=re.S)


def prose(p: Path) -> str:
    """Page text without fenced code and inline code."""
    t = FENCE.sub("", body(p))
    return re.sub(r"`[^`\n]*`", "", t)


def all_text() -> str:
    return "\n".join(text(p) for p in PAGES)


def test_every_folder_has_an_index_page_and_ascii_names():
    dirs = [CONTENT] + [d for d in CONTENT.rglob("*") if d.is_dir()]
    for d in dirs:
        assert (d / "index.zh.md").is_file(), f"{d.relative_to(WS)} has no index.zh.md (Workshop Studio build error)"
        assert re.fullmatch(r"[\w\-.]+", d.name, re.A), d
    assert not [p for p in CONTENT.rglob("*.md") if p.name != "index.zh.md"]


def test_front_matter_has_only_a_quoted_title_and_a_positive_weight():
    for p in PAGES:
        raw = p.read_bytes()
        assert raw.startswith(b"---\n"), rel(p)
        fm = front(p)
        assert set(fm) == {"title", "weight"}, (rel(p), fm)
        assert re.fullmatch(r'"[^"]+"', fm["title"]) and int(fm["weight"]) > 0, (rel(p), fm)


def test_nav_weights_are_unique_among_siblings_and_follow_the_chapter_order():
    by_parent = {}
    for p in PAGES:
        if p.parent == CONTENT:
            continue
        by_parent.setdefault(p.parent.parent, []).append((int(front(p)["weight"]), p.parent.name))
    for parent, items in by_parent.items():
        weights = [w for w, _ in items]
        assert len(weights) == len(set(weights)), (parent, items)
        assert [n for _, n in sorted(items)] == sorted(n for _, n in items), (parent, items)   # weight order = name order


def test_chapter_map_matches_the_pages_and_leaves_feedback_out():
    names = {p.parent.name for p in PAGES if p.parent.parent == CONTENT}
    assert names == set(MAIN) | {"90-extensions"}
    assert not [n for n in names if n.startswith("07")]           # 7 回流 is not written yet (phase 4)
    for d, n in MAIN.items():
        assert front(CONTENT / d / "index.zh.md")["title"].startswith(f'"{n} '), d
    assert front(CONTENT / "index.zh.md")["title"].startswith('"0 ')


def _minutes(p: Path) -> int:
    m = re.search(r"^\*\*用时\*\*：[^\n]*?约 (\d+) 分钟", body(p), re.M)
    assert m, f"{rel(p)} has no **用时** line"
    return int(m.group(1))


def test_time_budgets_add_up():
    home = body(CONTENT / "index.zh.md")
    table = home[home.index("## 0.4"):home.index("## 0.5")]
    rows = re.findall(r"^\| (\d) [^|]+\|[^|]*\|[^|]*\| (\d+) 分钟 \|$", table, re.M)
    plan = {int(n): int(m) for n, m in rows}
    assert sorted(plan) == [0, 1, 2, 3, 4, 5, 6, 8, 9]
    total = int(re.search(r"\*\*合计\*\* \| \| \| \*\*(\d+) 分钟\*\*", table).group(1))
    assert total == sum(plan.values()) and 120 <= total <= 180 and total + 20 <= 180   # + reserved 回流 chapter
    assert plan[0] == _minutes(CONTENT / "index.zh.md")
    for d, n in MAIN.items():
        assert plan[n] == _minutes(CONTENT / d / "index.zh.md"), d
    sub = [CONTENT / "02-define" / s / "index.zh.md" for s in ("21-scenario", "22-fishbone", "23-criteria", "24-golden",
                                                              "25-discussion")]
    assert sum(_minutes(p) for p in sub) == plan[2]
    for d in ("04-evaluate", "05-optimize"):
        steps = re.findall(r"^\| \d\.\d \|[^|]+\| (\d+) 分钟", body(CONTENT / d / "index.zh.md"), re.M)
        assert sum(map(int, steps)) == plan[MAIN[d]], d


def test_every_page_says_where_you_are_and_chapters_end_with_a_check_question():
    for p in PAGES:
        if p.parent == CONTENT / "90-extensions":
            continue
        assert "**你在这里**" in body(p).split("\n## ", 1)[0], rel(p)
    for r in CHECKED:
        p = CONTENT / r / "index.zh.md"
        last = body(p).rsplit("\n## ", 1)[1]
        assert last.startswith("检查一下"), (r, last[:40])
        assert "？" in last, r


def test_no_route_letters_github_or_scenario_12_anywhere_in_the_handbook():
    t = all_text() + text(WS / "SHOTLIST.md")
    banned = [r"[AB] ?版", r"路线", r"A/B", r"github", r"git clone", r"场景 ?12", r"(?<![\w-])[sS]12(?![\w])",
              r"--path [ab]", r"\b(?:10-A|11-A|12-A|20-B|21-B)-"]
    hits = [b for b in banned if re.search(b, t, re.I if b == r"github" else 0)]
    assert not hits, hits
    assert "TODO-LIVE" not in t


def test_no_raw_html_and_no_unescaped_ascii_colon_in_prose():
    for p in PAGES:
        t = prose(p)
        assert not re.search(r"<(?:/?[a-zA-Z][a-zA-Z0-9]*)(?:\s[^>]*)?>", t), rel(p)
        for line in t.splitlines():
            s = line.strip()
            if re.match(r"^:{2,4}(?:[a-z]|$)|^\*?图 ", s) or not s:
                continue
            s = re.sub(r"\]\([^)]*\)", "]", s)                      # link targets
            s = re.sub(r":(image|button|link|code)\[[^\]]*\]\{[^}]*\}", "", s)
            s = s.replace("\\:", "")
            assert ":" not in s, f"{rel(p)}: unescaped ':' in {line.strip()[:80]!r}"


def _anchors(p: Path) -> list:
    return re.findall(r"^#{2,4} .*\{#([a-z0-9\-]+)\}\s*$", body(p), re.M)


def test_every_heading_has_a_unique_ascii_anchor():
    for p in PAGES:
        heads = re.findall(r"^#{2,4} .*$", FENCE.sub("", body(p)), re.M)
        assert all(re.search(r"\{#[a-z0-9\-]+\}\s*$", h) for h in heads), (rel(p), [h for h in heads if "{#" not in h])
        a = _anchors(p)
        assert len(a) == len(set(a)), rel(p)


def test_internal_links_and_anchors_resolve():
    routes = {"/" + rel(p) if rel(p) else "/": p for p in PAGES}
    bad = []
    for p in PAGES:
        for target in re.findall(r"\]\((/[^)\s]*)\)", FENCE.sub("", body(p))):
            path, _, anchor = target.partition("#")
            page = routes.get(path.rstrip("/") or "/")
            if page is None or (anchor and anchor not in _anchors(page)):
                bad.append(f"{rel(p)} -> {target}")
    assert not bad, bad


RUNTIME_PATHS = ("results/", "outputs/", ".lab-state/", "build/", "reports/", "config/local.env", "data/golden/history",
                 "agent/adlc_agent/prompts/v1.md", "agent/adlc_agent/prompts/v1.changes.md",
                 "data/templates/extra-feedback.jsonl", "~/")


def test_every_script_and_repo_file_the_handbook_names_exists():
    t = all_text()
    scripts = set(re.findall(r"\./((?:\d\d|ext)-[\w-]+\.sh|tools/[\w-]+\.sh)", t))
    assert scripts, "no scripts found"
    for s in sorted(scripts):
        f = ROOT / s
        assert f.is_file() and f.stat().st_mode & 0o111, s
    paths = set(re.findall(r"`((?:agent|data|labkit|evaluators|tools|telemetry|config|\.claude|ec2)/[\w./\-]+)`", t))
    missing = [x for x in sorted(paths) if not x.startswith(RUNTIME_PATHS) and "<" not in x
               and not (ROOT / x.rstrip("/")).exists() and not list(ROOT.glob(x))]
    assert not missing, missing


def test_numbered_scripts_run_in_their_own_chapter_first():
    """Script number = chapter: a main chapter's command blocks only run scripts of that chapter or earlier ones,
    and each 10-60 script's first command-block use is in the chapter its tens digit names."""
    first = {}
    for d, n in sorted(MAIN.items(), key=lambda kv: kv[1]):
        pages = [CONTENT / d / "index.zh.md"] + sorted((CONTENT / d).glob("*/index.zh.md"))
        for p in pages:
            for block in re.findall(r"^```bash\n(.*?)^```", body(p), re.S | re.M):
                for num in re.findall(r"\./(\d\d)-[\w-]+\.sh", block):
                    k = int(num) // 10
                    assert k <= n or num == "99", (d, num)
                    first.setdefault(num, n)
    for num, n in first.items():
        if num != "99":
            assert int(num) // 10 == n, (num, n)
    assert first.get("99") == 9


def test_images_are_marker_svgs_or_planned_screenshots_with_captions():
    shot = text(WS / "SHOTLIST.md")
    planned = set(re.findall(r"^\| `([\w\-]+\.png)` \|", shot, re.M))
    used = set()
    for p in PAGES:
        lines = body(p).splitlines()
        for i, line in enumerate(lines):
            m = re.search(r':image\[[^\]]+\]\{src="/static/images/([^"]+)"', line)
            if not m:
                continue
            name = m.group(1)
            if name.endswith(".svg"):
                assert (WS / "static" / "images" / name).is_file(), name
                continue
            used.add(name)
            assert name in planned or (WS / "static" / "images" / name).is_file(), name
            # caption = its own paragraph right below (a blank line, else Workshop Studio renders it beside the image)
            assert lines[i + 1] == "" and lines[i + 2].startswith("*图 ") and lines[i + 2].endswith("*"), (rel(p), name)
    assert used == planned, (sorted(planned - used), sorted(used - planned))


def test_flywheel_svgs_are_current_and_self_contained():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "ws_flywheel.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    for svg in (WS / "static" / "images").glob("*.svg"):
        s = svg.read_text(encoding="utf-8")
        assert "href" not in s and "http" not in s.replace("http://www.w3.org/2000/svg", ""), svg.name
        assert not re.search(r"<script|<foreignObject|@import|@font-face|\son\w+=|url\((?!#)", s, re.I), svg.name


def test_switch_version_command_accepts_the_handbook_version_names(tmp_path):
    """0.6 / 5.3: v1-ref and v0 switch; v1 is refused (with the 5.2 hint) until prompts/v1.md exists; junk is refused."""
    import shutil
    (tmp_path / "agent" / "adlc_agent" / "prompts").mkdir(parents=True)
    (tmp_path / "config").mkdir()
    shutil.copy(ROOT / "50-switch-version.sh", tmp_path / "50-switch-version.sh")
    for v in ("v0", "v1-ref"):
        shutil.copy(ROOT / "agent" / "adlc_agent" / "prompts" / f"{v}.md", tmp_path / "agent" / "adlc_agent" / "prompts")
    run = lambda v: subprocess.run(["bash", str(tmp_path / "50-switch-version.sh"), v], capture_output=True, text=True)  # noqa: E731
    assert run("v1-ref").returncode == 0
    assert (tmp_path / "config" / "local.env").read_text(encoding="utf-8").splitlines() == ["AGENT_VERSION=v1-ref"]
    r = run("v1")
    assert r.returncode == 1 and "5.2" in r.stderr and "v1-ref" in r.stderr
    (tmp_path / "agent" / "adlc_agent" / "prompts" / "v1.md").write_text("x", encoding="utf-8")
    assert run("v1").returncode == 0 and "AGENT_VERSION=v1" in (tmp_path / "config" / "local.env").read_text(encoding="utf-8")
    assert run("../x").returncode == 1 and run("V1").returncode == 1
    for v in ("v1", "v1-ref"):
        assert f"./50-switch-version.sh {v}" in all_text()


def _sources_for(cmd: str) -> str:
    """The script plus every python module / tool it runs (labkit.X, data/tools/*.py) as one text."""
    out = text(ROOT / cmd)
    for mod in re.findall(r"-m labkit\.(\w+)", out):
        out += text(ROOT / "labkit" / f"{mod}.py")
    if "label_scheme" in out:
        out += text(ROOT / "labkit" / "label_scheme.py")
    return out


def test_every_flag_in_a_handbook_command_exists_in_what_it_runs():
    bad = []
    for p in PAGES:
        for block in re.findall(r"^```bash\n(.*?)^```", body(p), re.S | re.M):
            for line in block.splitlines():
                for m in re.finditer(r"(?:^|&&|;)\s*(\./[\w./-]+\.sh|python3 [\w./-]+\.py)([^;&|]*)", line):
                    target, args = m.group(1), m.group(2)
                    src = _sources_for(target[2:]) if target.startswith("./") else text(ROOT / target.split()[1])
                    for flag in re.findall(r"(?<![\w-])--[a-z][a-z-]*", args):
                        if flag not in src:
                            bad.append(f"{rel(p)}: {target} {flag}")
    assert not bad, bad


def test_expected_outputs_use_the_current_output_format():
    t = all_text()
    assert "▶ 运行 31 条需求 · AgentCore Runtime · 版本 v0" in t                    # labkit.batch header
    assert "adlc_ws_online_runtime" in t and "adlc_ws_online_b" not in t           # labkit.online config name
    batch = text(ROOT / "labkit" / "batch.py")
    assert 'f"▶ 运行 {len(rows)} 条需求 · {where} · 版本 {version}' in batch and '"AgentCore Runtime"' in batch
    assert "✅ 环境检查通过。下一步：手册第 2 章（定义：什么是好）" in t
    assert "✅ 环境检查通过。下一步：手册第 2 章（定义：什么是好）" in text(ROOT / "labkit" / "checkenv.py")


def test_the_scenario_stays_in_marked_places():
    """Scenario specifics live in data/scenario/ and in the marked 场景包 boxes; participant pages never point at
    the facilitator's id mapping of the role card."""
    assert (ROOT / "data" / "scenario" / "role-card.md").is_file()
    t = all_text()
    assert t.count('header="场景包') >= 3 and "数据对照" not in t


def test_no_account_ids_or_full_instance_ids_in_the_handbook():
    t = all_text() + text(WS / "SHOTLIST.md")
    assert not re.search(r"(?<!\d)\d{12}(?!\d)", t), "a 12-digit AWS account id is in the handbook (use <account>)"
    assert not re.search(r"\bi-[0-9a-f]{17}\b", t), "a full EC2 instance id is in the handbook (shorten it)"


def test_screenshot_files_match_the_shot_list_and_are_about_1600_px_without_metadata():
    """Every committed PNG has exactly one SHOTLIST row whose status starts with 已拍 (已拍（替身） / 已拍（待重拍）
    flag the ones to replace), every 已拍 row has its file, widths are 1200-1600 px, and no ancillary chunk that can
    carry metadata (text, EXIF, XMP, colour profile) survives."""
    import struct
    shot = text(WS / "SHOTLIST.md")
    rows = re.findall(r"^\| `([\w\-]+\.png)` \|[^|]*\| ([^|]+?) \|", shot, re.M)
    names = [n for n, _ in rows]
    assert len(names) == len(set(names)), "duplicate SHOTLIST rows"
    status = dict(rows)
    pngs = {p.name: p for p in (WS / "static" / "images").glob("*.png")}
    shot_done = {n for n, s in status.items() if s.startswith("已拍")}
    assert shot_done == set(pngs), (sorted(shot_done - set(pngs)), sorted(set(pngs) - shot_done))
    for name, p in pngs.items():
        data = p.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", name
        width = struct.unpack(">I", data[16:20])[0]
        assert 1200 <= width <= 1600, (name, width)
        pos, chunks = 8, []
        while pos < len(data):
            n, typ = struct.unpack(">I4s", data[pos:pos + 8])
            chunks.append(typ)
            pos += 12 + n
        assert not set(chunks) & {b"tEXt", b"iTXt", b"zTXt", b"eXIf", b"iCCP", b"tIME"}, (name, chunks)



def _assigned(path: Path, name: str):
    """A module-level literal (list / tuple / dict) from a source file, without importing it (labkit needs boto3)."""
    import ast
    for node in ast.parse(text(path)).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path.name}")


def _evaluator_facts() -> dict:
    """Evaluator -> (on demand, online, pillars) from the code (binary label scheme): 40- registers CODE_EVALUATORS plus
    the native judge, 43- evaluates every registered one plus BUILTINS, 41- attaches ONLINE_*, and the compare page's
    ROLES (labkit/compare_story.py) say which pillars each feeds."""
    reg = text(ROOT / "labkit" / "evaluators_reg.py")
    code = [suffix for suffix, _key, _desc in _assigned(ROOT / "labkit" / "evaluators_reg.py", "CODE_EVALUATORS")]
    judges = [n for n in re.findall(r'ev_name\("(\w+)"\)', reg) if n not in code]
    assert judges == ["reject_quality_llm"] * len(judges) and judges, judges
    names = code + ["reject_quality_llm"] + list(_assigned(ROOT / "labkit" / "evaluate.py", "BUILTINS"))
    assert "evaluators = [(k, v[\"evaluatorId\"]) for k, v in ev_state.items()" in text(ROOT / "labkit" / "evaluate.py")
    online = set(_assigned(ROOT / "labkit" / "online.py", "ONLINE_EVALUATORS")) | set(
        _assigned(ROOT / "labkit" / "online.py", "ONLINE_BUILTINS"))
    roles = _assigned(ROOT / "labkit" / "compare_story.py", "ROLES")
    return {n: (True, n in online, set(roles[n][0])) for n in names}


def _section(p: Path, anchor: str) -> str:
    b = body(p)
    start = b.index("{#" + anchor + "}")
    nxt = b.find("\n## ", start)
    return b[start:nxt if nxt != -1 else len(b)]


PILLAR_WORDS = {"red": "红线", "gate": "门禁", "obs": "观测"}


def _flywheel_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ws_flywheel", ROOT / "tools" / "ws_flywheel.py")
    fw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fw)
    return fw


def test_chapter4_evaluator_map_matches_what_the_scripts_register_attach_and_score():
    """4.1: the three evaluation modes, one map row per evaluator whose 按需 / 在线 columns and pillar roles match
    labkit, and the golden-set diagram, drawn by tools/ws_flywheel.py from rows that must match the same code."""
    p = CONTENT / "04-evaluate" / "index.zh.md"
    s41 = _section(p, "s-4-1")
    for mode in ("**按需评估**（on-demand）", "**在线评估**（online）", "**批量评估**（batch）"):
        assert mode in s41, mode
    assert "| 注册名 | 类型 | 按需 | 在线 | 读黄金集的什么 | 在结论里的角色 | 打什么分 |" in s41
    rows = {}
    for line in s41.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        m = re.fullmatch(r"`([\w.]+)`", cells[0]) if len(cells) == 7 else None
        if m:
            assert cells[2] in ("是", "否") and cells[3] in ("是", "否"), line
            role = {k for k, w in PILLAR_WORDS.items() if w in cells[5]}
            rows[m.group(1)] = (cells[2] == "是", cells[3] == "是", role)
    want = _evaluator_facts()
    assert rows == want, (rows, want)
    assert 'src="/static/images/eval-golden-map.svg"' in s41
    fw = _flywheel_module()
    drawn = {}
    for names, _typ, _fields, ondemand, online, pills in fw.EVAL_ROWS:
        for n in names.split(" · "):
            drawn[n] = (ondemand, online, {k for k, _label in pills})
    assert drawn == want, (drawn, want)
    assert (WS / "static" / "images" / "eval-golden-map.svg").read_text(encoding="utf-8") == fw.evalmap_svg()
    # the golden fields each drawn row says it reads are the ones that evaluator's code reads
    ev = ROOT / "evaluators"
    reads = {"acc": "acceptable_decisions", "car": "can_auto_reject", "pts": "reject_points", "cat": "capability_catalog"}
    label_src = text(ev / "adlc_label_accuracy" / "label_scoring.py") + text(ev / "adlc_label_accuracy" / "lambda_function.py")
    reject_src = text(ev / "adlc_reject_quality" / "judge.py") + text(ev / "adlc_reject_quality" / "lambda_function.py")
    for names, _typ, fields, *_rest in fw.EVAL_ROWS:
        src = label_src if names.startswith("label_") else reject_src if names == "reject_quality" else None
        for k in fields:
            if k in reads and src is not None:
                assert reads[k] in src, (names, k)
        if names == "reject_quality":
            assert "reply_points" in src and "acceptable_decisions" not in text(ev / "adlc_reject_quality" / "judge.py")
        if names.startswith("label_"):
            assert "reject_points" not in text(ev / "adlc_label_accuracy" / "label_scoring.py")
        if "none" in fields and names == "reject_quality_llm":
            assert "{expected_response}" not in text(ev / "llm_judge_config" / "adlc_reject_quality.json")
        if "ref" in fields:
            assert "evaluationReferenceInputs" in text(ROOT / "labkit" / "evaluate.py")


def test_chapter4_dissects_one_recorded_failure_and_ends_with_the_faq():
    """4.7 walks one real V0 failure (the recorded run the expected outputs come from); its numbers follow from the
    judge's own scoring rule. A FAQ of collapsible answers sits right before the check question, and chapter 6 points
    at it. Section ids other pages link to stay."""
    import importlib
    p = CONTENT / "04-evaluate" / "index.zh.md"
    b = body(p)
    anchors = _anchors(p)
    assert anchors == ["s-4-1", "s-4-2", "s-4-3", "s-4-4", "s-4-5", "s-4-6", "s-4-7", "s-4-faq", "s-4-check"], anchors
    s47 = _section(p, "s-4-7")
    m = re.search(r'^::::expand\{header="拆一条失败：(FB-\d{4}-\d{3})[^"]*"\}\n(.*?)^::::$', s47, re.S | re.M)
    assert m, "4.7 has no dissect expand block"
    fid, block = m.group(1), m.group(2)
    flag = "话术称可经API/连接器受控跨租户访问，目录无此能力"
    s44, s45 = _section(p, "s-4-4"), _section(p, "s-4-5")
    assert "runtime-v0-20261114T134711Z" in block and "runtime-v0-20261114T134711Z" in s44      # same recorded run
    assert f"[29/31] {fid}       16.7s  重要=是 紧急=否 建议=拒绝" in s44 and "`重要=是 紧急=否 建议=拒绝`" in block
    row = f"[31/31] {fid}    三项=Fail     重要=Fail  紧急=Pass  建议=Pass  话术=Fail"
    assert row in s45 and "`三项=Fail 重要=Fail 紧急=Pass 建议=Pass 话术=Fail`" in block
    assert f"⛔ {fid}（G29）话术红线：{flag}" in s45 and flag in block
    golden = [ln for ln in text(ROOT / "data" / "golden" / "golden_set.jsonl").splitlines() if f'"{fid}"' in ln]
    assert golden and '"G29"' in golden[0] and "G29" in block
    import json
    g = json.loads(golden[0])
    assert g["input"]["content"] in block and all(x in block for x in g["reject_points"] + g["reply_points"])
    # sub-scores 4,4,4,3,2 -> 0.6 without a flag, capped to 0.2 Fail with one (the judge's own rule)
    sys.path.insert(0, str(ROOT / "evaluators" / "adlc_reject_quality"))
    try:
        judge = importlib.import_module("judge")
        keys = [k for k, _n, _d in judge.rubric.DIMENSIONS]
        scores = dict(zip(keys, (4, 4, 4, 3, 2)))
        assert judge.score_verdict({"scores": scores, "red_flags": []})["value"] == 0.6
        capped = judge.score_verdict({"scores": scores, "red_flags": [flag]})
        assert (capped["value"], capped["label"]) == (0.2, "Fail")
    finally:
        sys.path.remove(str(ROOT / "evaluators" / "adlc_reject_quality"))
    assert "理由专业性 4，理解与礼貌 4，原因说明 4，替代方案 3，风险控制 2" in block and "本来是 0.6" in block
    for step in ("① 黄金样本", "② V0 的输出", "③ 评估器的判断", "④ 进结论", "⑤ 五维归因", "⑥ V1 改了什么"):
        assert step in block, step
    faq = _section(p, "s-4-faq")
    assert 5 <= len(re.findall(r'^::::expand\{header="[^"]+？"\}$', faq, re.M)) <= 8
    assert faq.count("::::expand") * 2 == faq.count("::::")
    assert "(/04-evaluate#s-4-faq)" in body(CONTENT / "06-release-observe" / "index.zh.md")
    assert b.index("{#s-4-faq}") < b.index("{#s-4-check}")
