#!/usr/bin/env python3
"""障碍 → 判据 → 黄金集 (adlc-criteria skill): the deterministic half of the define ring's second step.

  python3 .claude/skills/adlc-criteria/scripts/criteria.py skeleton <dir>   # criteria-draft.json, one entry per confirmed obstacle
  python3 .claude/skills/adlc-criteria/scripts/criteria.py catalog          # what the lab's evaluators already check
  python3 .claude/skills/adlc-criteria/scripts/criteria.py golden           # the current golden set, one line per item
  python3 .claude/skills/adlc-criteria/scripts/criteria.py candidates       # last month's feedback not in the golden set
  python3 .claude/skills/adlc-criteria/scripts/criteria.py hints            # the sample scenario's pain point -> data ids
  python3 .claude/skills/adlc-criteria/scripts/criteria.py build <dir>      # validate the draft -> criteria.json + criteria.md
  python3 .claude/skills/adlc-criteria/scripts/criteria.py verify <dir>     # after the golden additions were applied

<dir> is the fishbone session directory (outputs/fishbone/<group>-<date>/) holding fishbone-data.json.

The agent (the skill) writes the judgement into criteria-draft.json: the criterion text, dimension, tier, threshold,
executor, which catalogued checks it maps to and which golden items exercise it. This script owns everything that
can be decided mechanically and is tested: the handbook's criterion rules (subject is the Agent, a red line has no
threshold and is never judged by an LLM, cost / performance are metric readings), coverage against
../evaluator-coverage.json (已覆盖 / 部分覆盖 / 未覆盖), golden ids against data/golden/golden_set.jsonl, and the
golden additions: they are written as PREVIEW files in the formats the existing tools read
(golden-proposals.csv = data/templates/golden-set-feedback.csv rows with 操作=新增, extra-feedback.proposed.jsonl =
data/templates/extra-feedback.jsonl lines) and dry-run through data/tools/golden_lib in memory. Nothing under data/
is written here; the skill applies the previews with generate_mock_data.py and golden_tool.py after the group agrees.

Standard library only; runs with the system python3 (3.9+). Exit code 0 = ok, 1 = errors (nothing written).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SKILL_DIR = Path(__file__).resolve().parent.parent
REPO = SKILL_DIR.parent.parent.parent
CATALOG_FILE = SKILL_DIR / "evaluator-coverage.json"
SCENARIO_MAP = SKILL_DIR / "scenario-map.json"  # facilitator data for the sample scenario (kept out of the role card)
TOOLS = REPO / "data" / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import common as C  # noqa: E402
import golden_lib as G  # noqa: E402

DIM_KEYS = {"cognition": "认知", "quality": "质量", "responsibility": "责任", "cost": "成本",
            "performance": "性能", "other": "其他"}
DIMENSIONS = ("认知", "质量", "责任", "成本", "性能", "其他")
TIERS = ("红线", "门禁", "观测")
EXECUTORS = ("代码断言", "裁判", "内置评估器", "指标读数")
METRIC_DIMS = ("成本", "性能")
TIER_RANK = {"观测": 0, "门禁": 1, "红线": 2}
BAD_SUBJECTS = ("客户觉得", "客户认为", "用户感觉", "用户觉得", "让客户", "让用户")
MAX_PROPOSALS = 3
BEIJING = timezone(timedelta(hours=8))
DRAFT, OUT_JSON, OUT_MD = "criteria-draft.json", "criteria.json", "criteria.md"
PROPOSAL_CSV, PROPOSAL_EXTRA = "golden-proposals.csv", "extra-feedback.proposed.jsonl"
COVER = ("已覆盖", "部分覆盖", "未覆盖")


class CriteriaError(RuntimeError):
    pass


# ---------------------------------------------------------------- inputs
def load_catalog(path: Path = CATALOG_FILE) -> Dict[str, dict]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {c["id"]: c for c in doc["checks"]}


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise CriteriaError(f"读不了 {path}：{type(e).__name__}: {e}") from e


def obstacles(fishbone: dict) -> List[dict]:
    """Every confirmed barrier with a stable id '<dimension key>-<1-based position>'."""
    out = []
    barriers = fishbone.get("barriers") if isinstance(fishbone.get("barriers"), dict) else {}
    for key, cn in DIM_KEYS.items():
        items = barriers.get(key) if isinstance(barriers.get(key), list) else []
        for i, b in enumerate(items, 1):
            if isinstance(b, dict) and b.get("confirmed") is True:
                out.append({"obstacle_id": f"{key}-{i}", "dimension_key": key, "fishbone_dimension": cn,
                            "sticky_text": str(b.get("sticky_text") or "").strip(),
                            "evidence": str(b.get("evidence") or "").strip(), "selected": b.get("selected") is True})
    return out


def check_fishbone(fishbone) -> None:
    """Criteria are only written from a fishbone the business owner approved (adlc-fishbone step 3)."""
    if not isinstance(fishbone, dict) or fishbone.get("schema_version") != "1.0":
        raise CriteriaError("fishbone-data.json 不是 schema 1.0 的鱼骨图数据")
    if fishbone.get("status") != "approved_for_render":
        raise CriteriaError(f"鱼骨图还没被业务方最终确认（status={fishbone.get('status')!r}）：先完成 adlc-fishbone 第 3 步")


def _lab_card() -> str:
    try:
        return Path(str(json.loads(SCENARIO_MAP.read_text(encoding="utf-8")).get("role_card") or "")).name or "role-card.md"
    except (OSError, ValueError, AttributeError):
        return "role-card.md"


def scenario_of(fishbone: dict, draft: Optional[dict] = None) -> str:
    """'lab' only when the draft says so AND the fishbone shows the lab's role card among its background material
    (or an older fishbone file says scenario=lab). A fishbone that says 'own' is never overridden."""
    meta = fishbone.get("metadata") if isinstance(fishbone.get("metadata"), dict) else {}
    legacy = str(meta.get("scenario") or "")
    if legacy == "own":
        return "own"
    want = draft.get("scenario").strip() if isinstance(draft, dict) and isinstance(draft.get("scenario"), str) else ""
    background = meta.get("background") if isinstance(meta.get("background"), list) else []
    lab_material = legacy == "lab" or any(isinstance(b, str) and Path(b).name == _lab_card() for b in background)
    if want == "lab":
        return "lab" if lab_material else "unverified"
    return want or ("lab" if legacy == "lab" else "")


def golden_rows(path: str = C.GOLDEN_PATH) -> List[dict]:
    return C.read_jsonl(path) if os.path.exists(path) else []


def feedback_rows(path: str = C.FEEDBACK_PATH) -> List[dict]:
    return C.read_jsonl(path) if os.path.exists(path) else []


# ---------------------------------------------------------------- criterion rules + coverage
def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def check_rules(c: dict, catalog: Dict[str, dict]) -> Tuple[List[str], List[str]]:
    """The handbook's criterion rules (1.10.3) for one draft entry: (errors, warnings)."""
    cid = c.get("id") if isinstance(c.get("id"), str) else "?"
    errors, warns = [], []
    for field, ok in (("id", isinstance(c.get("id"), str)), ("obstacle_id", isinstance(c.get("obstacle_id"), str)),
                      ("text", isinstance(c.get("text"), str)), ("dimension", isinstance(c.get("dimension"), str)),
                      ("tier", isinstance(c.get("tier"), str)), ("executor", isinstance(c.get("executor"), str)),
                      ("threshold", c.get("threshold") is None or isinstance(c.get("threshold"), (str, int, float))),
                      ("golden_ids", isinstance(c.get("golden_ids", []), list)
                       and all(isinstance(g, str) for g in c.get("golden_ids", []))),
                      ("golden_gap", isinstance(c.get("golden_gap", ""), str)), ("note", isinstance(c.get("note", ""), str)),
                      ("sticky_text", isinstance(c.get("sticky_text", ""), str))):
        if not ok:
            errors.append(f"{cid}：{field} 的类型不对（文字写字符串，golden_ids / checks 写字符串列表）")
    if errors:
        return errors, warns
    text = str(c.get("text") or "").strip()
    if not text:
        errors.append(f"{cid}：判据文字为空")
    elif "Agent" not in text and "agent" not in text:
        errors.append(f"{cid}：判据的主语要是 Agent（写成“Agent …”），现在是：{text}")
    for bad in BAD_SUBJECTS:
        if bad in text:
            errors.append(f"{cid}：“{bad}”的主语不是 Agent，客户感受不能直接观测；改写成 Agent 输出上可检查的谓语")
    dim, tier, ex = c.get("dimension"), c.get("tier"), c.get("executor")
    if dim not in DIMENSIONS:
        errors.append(f"{cid}：维度只能是 {' / '.join(DIMENSIONS)}，现在是 {dim!r}")
    if tier not in TIERS:
        errors.append(f"{cid}：档位只能是 {' / '.join(TIERS)}，现在是 {tier!r}")
    if ex not in EXECUTORS:
        errors.append(f"{cid}：执行方式只能是 {' / '.join(EXECUTORS)}，现在是 {ex!r}")
    if tier == "红线" and not _blank(c.get("threshold")):
        errors.append(f"{cid}：红线不打分，threshold 要留空（0 违例）")
    if tier in ("门禁", "观测") and _blank(c.get("threshold")):
        errors.append(f"{cid}：{tier}要写阈值（观测写“只看趋势”也可以）")
    if tier == "红线" and ex in ("裁判", "内置评估器"):  # the built-in evaluators are LLM judges too
        errors.append(f"{cid}：红线要一票否决，需要裁判（含内置评估器）才能判的东西判不成红线；改用代码断言，或降为门禁 / 观测")
    if dim in METRIC_DIMS and ex != "指标读数":
        errors.append(f"{cid}：{dim}维从 trace / 运行记录读数（执行方式写“指标读数”），不交给评估器或裁判")
    checks = c.get("checks") if isinstance(c.get("checks"), list) else None
    if checks is None or not all(isinstance(x, str) for x in checks):
        errors.append(f"{cid}：checks 要写成检查编号的列表（可以是空列表 []）")
        checks = []
    for x in checks:
        if x not in catalog:
            errors.append(f"{cid}：未知的 check {x!r}（运行 criteria.py catalog 查看可选项）")
    known = [catalog[x] for x in checks if x in catalog]
    if ex == "裁判" and tier == "门禁":
        warns.append(f"{cid}：裁判作门禁前要先校准（和人工判断的一致率），没校准前只能算观测")
    if ex == "裁判" and known and all(k.get("assertable") for k in known):
        warns.append(f"{cid}：这条能用代码断言判，不要交给裁判（能用代码判的永远不交给裁判）")
    return errors, warns


def _golden_ok(row: dict, req: Optional[dict]) -> bool:
    if not req:
        return True
    if "can_auto_reject" in req and bool(row.get("can_auto_reject", True)) != bool(req["can_auto_reject"]):
        return False
    if "decision_in" in req:
        decs = set(row.get("acceptable_decisions") or []) | {(row.get("expected") or {}).get("decision")}
        if not decs & set(req["decision_in"]):
            return False
    if req.get("has_capabilities") and not row.get("matched_capabilities"):
        return False  # batch_score._cap_hit skips items without matched_capabilities
    return True


def coverage(c: dict, catalog: Dict[str, dict], golden: Dict[str, dict]) -> Tuple[str, List[str], bool, List[str]]:
    """(已覆盖 / 部分覆盖 / 未覆盖, notes, golden_fixable, warnings) for one criterion that passed check_rules."""
    checks = [catalog[x] for x in c.get("checks") or [] if x in catalog]
    if not checks:
        return "未覆盖", ["现有评估器和运行读数都不检查这一条：要新写评估器或断言"], False, []
    level, notes, warns, fixable = "已覆盖", [], [], False
    for ch in checks:
        if ch.get("coverage") == "partial":
            level = "部分覆盖"
            notes.append(f"{ch['id']}：{ch.get('gap', '只覆盖一部分')}")
    best = max(checks, key=lambda ch: TIER_RANK.get(ch.get("tier"), 0))
    if TIER_RANK.get(c.get("tier"), 0) > TIER_RANK.get(best.get("tier"), 0):
        level = "部分覆盖"
        notes.append(f"判据要求{c.get('tier')}，现有评估器最多只把它当{best.get('tier')}")
    have = sorted({ch.get("executor") for ch in checks})
    if c.get("executor") not in have:
        level = "部分覆盖"
        notes.append(f"判据要求{c.get('executor')}，现有实现是{'、'.join(have)}")
    gids = [str(g) for g in c.get("golden_ids") or []]
    for g in gids:
        if g not in golden:
            raise CriteriaError(f"{c.get('id')}：黄金集里没有 {g}（运行 criteria.py golden 查看）")
    gap = str(c.get("golden_gap") or "").strip()
    if gap and any(ch.get("needs_golden") for ch in checks):
        level, fixable = "部分覆盖", True
        notes.append(f"现有黄金样本没覆盖障碍里的这个情形：{gap}（可以补黄金样本）")
    for ch in [ch for ch in checks if ch.get("needs_golden")]:
        ok = [g for g in gids if _golden_ok(golden[g], ch.get("golden_requires"))]
        bad = [g for g in gids if g not in ok]
        if bad and ch.get("golden_requires"):
            warns.append(f"{c.get('id')}：{'、'.join(bad)} 不满足 {ch['id']} 的条件 {json.dumps(ch['golden_requires'], ensure_ascii=False)}，"
                         "对这项检查不起作用")
        if not ok:
            level = "部分覆盖"
            fixable = True
            notes.append(f"{ch['id']} 能判，但黄金集里还没有这类样本（可以补黄金样本）")
    return level, list(dict.fromkeys(notes)), fixable, warns


# ---------------------------------------------------------------- golden additions (preview only)
def _mock_categories() -> Dict[str, str]:
    import generate_mock_data as M  # noqa: E402  (data/tools; imports only stdlib + common + golden_lib)
    return dict(M.CATEGORY_CN)


def _next_id(feedback: List[dict], taken: set) -> str:
    rx = re.compile(r"^(.*?)(\d+)$")
    nums, prefix = [], "FB-NEW-"
    for r in feedback:
        m = rx.match(str(r.get("id") or ""))
        if m:
            nums.append(int(m.group(2)))
            prefix = m.group(1)
    n = (max(nums) if nums else 0) + 1
    while f"{prefix}{n:03d}" in taken:
        n += 1
    return f"{prefix}{n:03d}"


def _labels(p: dict) -> str:
    e = p.get("expected") or {}
    s = f"重要 {e.get('important')} · 紧急 {e.get('urgent')} · 建议 {e.get('decision')}"
    acc = [a for a in p.get("acceptable_decisions") or [] if a != e.get("decision")]
    if acc:
        s += f"（也可：{'/'.join(acc)}）"
    return s + (" · 不可自动拒绝" if p.get("can_auto_reject") is False else "")


def mock_params_hint(n_new: int) -> List[str]:
    """What to change in mock-data-params.yaml so generate_mock_data.py keeps the new feedback items."""
    params_path = os.path.join(C.templates_dir(), "mock-data-params.yaml")
    params = C.load_yaml(params_path) if os.path.exists(params_path) else {}
    pool = C.read_jsonl(C.POOL_PATH) if os.path.exists(C.POOL_PATH) else []
    extra_file = params.get("extra_items_file") or "data/templates/extra-feedback.jsonl"
    extra_path = extra_file if os.path.isabs(extra_file) else os.path.join(C.REPO, extra_file)
    extra = C.read_jsonl(extra_path) if os.path.exists(extra_path) else []
    total = len(pool) + len(extra) + n_new
    out = [f"把这 {n_new} 条追加到 {os.path.relpath(extra_path, C.REPO)}（每行一条，格式同 extra-feedback.example.jsonl）",
           f"把 data/templates/mock-data-params.yaml 的 count 改成 {total}（种子库 {len(pool)} + 已有补充 {len(extra)} + 新增 {n_new}），"
           f"现在是 {params.get('count')}"]
    if params.get("category_mix"):
        out.append("⚠️ mock-data-params.yaml 设置了 category_mix：按配比抽样时新条目可能不被选中，生成后用 criteria.py verify 确认")
    if params.get("month") or params.get("id_prefix"):
        out.append("⚠️ 设置了 month / id_prefix：生成时编号会被改写，生成后用 criteria.py verify 确认编号，必要时改 golden-proposals.csv 的 feedback_id")
    return out


def proposals(draft: dict, crit_ids: set, golden: List[dict], feedback: List[dict]) -> Tuple[List[dict], List[dict], List[dict], List[str]]:
    """Validate draft['golden_proposals'] and dry-run them through golden_lib.

    Returns (summary rows for criteria.json, golden-set-feedback.csv rows, extra-feedback lines, warnings)."""
    props = draft.get("golden_proposals") or []
    if not isinstance(props, list):
        raise CriteriaError("golden_proposals 要写成列表")
    if not props:
        return [], [], [], []
    if len(props) > MAX_PROPOSALS:
        raise CriteriaError(f"一次最多提议 {MAX_PROPOSALS} 条黄金样本（现在 {len(props)} 条）：挑最能补上缺口的")
    pool = {str(r.get("id")): r for r in feedback}
    in_golden = {str(r.get("feedback_id")) for r in golden}
    cats = _mock_categories()
    taken = set(pool)
    summary, csv_rows, extra, errors, pwarns, seen = [], [], [], [], [], set()
    seeds = _seed_index()
    for i, p in enumerate(props, 1):
        tag = f"golden_proposals 第 {i} 条"
        if not isinstance(p, dict):
            errors.append(f"{tag}：要写成对象")
            continue
        if not isinstance(p.get("criterion"), str) or p.get("criterion") not in crit_ids:
            errors.append(f"{tag}：criterion {p.get('criterion')!r} 不在判据表里")
        new = p.get("new_feedback")
        if new is not None:
            if not isinstance(new, dict):
                errors.append(f"{tag}：new_feedback 要写成对象")
                continue
            new = {k: v for k, v in new.items() if k != "_meta"}
            meta_in = p["new_feedback"].get("_meta") if isinstance(p["new_feedback"].get("_meta"), dict) else {}
            category = p.get("category") or meta_in.get("category")
            if category not in cats:
                errors.append(f"{tag}：新写的需求反馈要有 category（{' / '.join(cats)}）")
            if not new.get("id"):
                new["id"] = _next_id(feedback, taken)
            fid = str(new["id"])
            if fid in pool:
                errors.append(f"{tag}：编号 {fid} 已在上个月的需求反馈里，直接写 feedback_id 即可，不要新写")
            missing = [k for k in C.REQUIRED_FIELDS + C.RECOMMENDED_FIELDS if _blank(new.get(k))]
            if missing:
                errors.append(f"{tag}：新写的需求反馈缺字段 {', '.join(missing)}")
            if new.get("customer") and "（示例）" not in str(new.get("customer")):
                errors.append(f"{tag}：客户名要用明显合成的“XX（示例）”")
            taken.add(fid)
            source = "extra"
            item = G.input_of(new)
        else:
            fid = p.get("feedback_id") if isinstance(p.get("feedback_id"), str) else ""
            if fid not in pool:
                errors.append(f"{tag}：上个月的需求反馈里没有 {fid!r}；要新写一条就用 new_feedback")
                continue
            source, item = "pool", G.input_of(pool[fid])
        if fid in in_golden:
            errors.append(f"{tag}：{fid} 已经在黄金集里")
        if fid in seen:  # apply-feedback would silently skip the second one
            errors.append(f"{tag}：{fid} 在提议里出现了两次")
        seen.add(fid)
        expected = p.get("expected")
        if not isinstance(expected, dict) or not all(isinstance(v, (str, type(None))) for v in expected.values()):
            errors.append(f"{tag}：expected 要写成对象，例如 {{\"important\": \"否\", \"urgent\": \"否\", \"decision\": \"迭代\"}}")
            continue
        auto = _yes_no(p.get("can_auto_reject", None))
        if auto is None:
            errors.append(f"{tag}：can_auto_reject 必须明确写 true / false（或 是 / 否），现在是 {p.get('can_auto_reject')!r}")
            continue
        if auto and item.get("feedback_type") == "功能缺陷":
            errors.append(f"{tag}：{fid} 是功能缺陷，按标注标准第 4 节不可自动拒绝（can_auto_reject 要写 false）")
        lists = {}
        for key, seps in (("acceptable_decisions", C.TAG_SEPS), ("dimensions", C.TAG_SEPS),
                          ("reject_points", C.LIST_SEPS), ("reply_points", C.LIST_SEPS),
                          ("matched_capabilities", C.TAG_SEPS)):
            v = _str_list(p.get(key), seps)
            if v is None:
                errors.append(f"{tag}：{key} 要写成字符串列表")
            lists[key] = v or []
        seed = seeds.get((item.get("title"), item.get("content")))  # by text: ids change when 2.4 renumbers
        if seed is not None and seed.get("can_auto_reject", True) is not auto:
            pwarns.append(f"警告：{tag}（{fid}）的 can_auto_reject={auto} 和种子库标注 {seed.get('can_auto_reject')} 不一致，确认理由")
        if seed is not None and any(seed.get(k) != expected.get(k) for k in ("important", "urgent", "decision")):
            pwarns.append(f"警告：{tag}（{fid}）的期望标签和种子库标注不一致（种子：重要{seed.get('important')} 紧急{seed.get('urgent')} "
                          f"{seed.get('decision')}），确认理由")
        if lists["matched_capabilities"]:
            pwarns.append(f"警告：{tag}（{fid}）的 matched_capabilities 不会写进黄金集（golden-set-feedback.csv 没有“相关能力”这一列），"
                          "这条样本不计入能力目录命中率")
        row = {"golden_id": "", "feedback_id": fid, "input": item, "expected": dict(expected),
               "acceptable_decisions": lists["acceptable_decisions"], "can_auto_reject": auto,
               "reject_points": lists["reject_points"], "reply_points": lists["reply_points"],
               "rationale": str(p.get("rationale") or ""), "dimensions": lists["dimensions"],
               "case_type": p.get("case_type") if isinstance(p.get("case_type"), str) else "",
               "matched_capabilities": lists["matched_capabilities"]}
        csv_row = G.to_feedback_rows([row])[0]
        csv_row.update({"golden_id": "", "操作": "新增", "备注": f"adlc-criteria：补 {p.get('criterion')}"})
        csv_rows.append(csv_row)
        if source == "extra":
            e = row["expected"]
            meta = {"category": category, "important": e.get("important"), "urgent": e.get("urgent"),
                    "decision": e.get("decision"), "level": e.get("level") or C.derive_level(e.get("important"), e.get("urgent")),
                    "acceptable_decisions": row["acceptable_decisions"] or [e.get("decision")],
                    "can_auto_reject": row["can_auto_reject"], "golden_id": None, "case_type": None, "dimensions": [],
                    "rationale": row["rationale"], "reject_points": row["reject_points"], "reply_points": row["reply_points"],
                    "matched_capabilities": row["matched_capabilities"]}
            extra.append(dict(item, _meta=meta))
        summary.append({"criterion": p.get("criterion"), "feedback_id": fid, "source": source,
                        "title": item.get("title"), "labels": _labels(row), "why": str(p.get("why") or "")})
    if errors:
        raise CriteriaError("黄金样本提议有问题：\n  " + "\n  ".join(errors))
    # dry run through the existing tool path: golden_tool.py apply-feedback uses exactly these two calls
    sim_pool = dict(pool)
    sim_pool.update({str(x["id"]): x for x in extra})
    new_rows, log = G.apply_feedback(golden, csv_rows, "v-preview", sim_pool)
    gerrs, gwarns = G.validate(new_rows, "binary", _catalog_ids())
    if gerrs:
        raise CriteriaError("按 golden_tool.py 的规则试运行没通过：\n  " + "\n  ".join(gerrs[:10]))
    before = set(G.validate(golden, "binary", _catalog_ids())[1])
    warns = pwarns + [w for w in log if w.startswith("警告")] + [w for w in gwarns if w not in before]
    added = sum(1 for w in log if w.startswith("新增 "))
    if added != len(csv_rows):
        raise CriteriaError(f"试运行只新增了 {added} 条（提议 {len(csv_rows)} 条）：\n  " + "\n  ".join(log))
    return summary, csv_rows, extra, warns


def applied_rows(draft: dict, crit_ids: set, golden: List[dict]) -> List[dict]:
    """draft['golden_applied']: proposals already written by golden_tool.py (moved there after verify passed)."""
    items = draft.get("golden_applied") or []
    if not isinstance(items, list):
        raise CriteriaError("golden_applied 要写成列表")
    by_fid = {str(r.get("feedback_id")): r for r in golden}
    out, errors = [], []
    for i, p in enumerate(items, 1):
        fid = str((p or {}).get("feedback_id") or "") if isinstance(p, dict) else ""
        g = by_fid.get(fid)
        if not g:
            errors.append(f"golden_applied 第 {i} 条：黄金集里没有 {fid!r}（还没写入就放回 golden_proposals）")
            continue
        if not isinstance(p.get("criterion"), str) or p.get("criterion") not in crit_ids:
            errors.append(f"golden_applied 第 {i} 条：criterion {p.get('criterion')!r} 不在判据表里")
        out.append({"criterion": p.get("criterion"), "feedback_id": fid, "golden_id": g.get("golden_id"),
                    "title": (g.get("input") or {}).get("title"), "labels": _labels(g), "version": g.get("version")})
    if errors:
        raise CriteriaError("已写入的黄金样本对不上：\n  " + "\n  ".join(errors))
    return out


def _yes_no(v) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return {"是": True, "否": False}.get(C.norm_yes_no(v) or "")
    return None


def _str_list(v, seps) -> Optional[List[str]]:
    """A list of strings, or one string split like the CSV columns; None = wrong type."""
    if v is None:
        return []
    if isinstance(v, str):
        return C.split_list(v, seps)
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return [x.strip() for x in v if x.strip()]
    return None


def _seed_index() -> dict:
    """Seed / extra-feedback labels (_meta) by id and by (title, content)."""
    seed = C.read_jsonl(C.POOL_PATH) if os.path.exists(C.POOL_PATH) else []
    extra_path = os.path.join(C.templates_dir(), "extra-feedback.jsonl")
    seed += C.read_jsonl(extra_path) if os.path.exists(extra_path) else []
    out: dict = {}
    for r in seed:
        m = r.get("_meta") if isinstance(r.get("_meta"), dict) else None
        if m:
            out.setdefault((r.get("title"), r.get("content")), m)
    return out


def _catalog_ids() -> Optional[set]:
    if not os.path.exists(C.CATALOG_PATH):
        return None
    doc = json.loads(Path(C.CATALOG_PATH).read_text(encoding="utf-8"))
    return {c.get("id") for c in (doc.get("capabilities") or [])}


# ---------------------------------------------------------------- build
def build(out_dir, catalog: Optional[Dict[str, dict]] = None, golden: Optional[List[dict]] = None,
          feedback: Optional[List[dict]] = None, now: Optional[datetime] = None, write: bool = True) -> dict:
    d = Path(out_dir)
    catalog = catalog if catalog is not None else load_catalog()
    golden = golden if golden is not None else golden_rows()
    feedback = feedback if feedback is not None else feedback_rows()
    fishbone = _read_json(d / "fishbone-data.json")
    check_fishbone(fishbone)
    draft = _read_json(d / DRAFT)
    if not isinstance(draft, dict) or not isinstance(draft.get("criteria"), list):
        raise CriteriaError(f"{DRAFT} 要有 criteria 列表（先运行 criteria.py skeleton）")
    obs = {o["obstacle_id"]: o for o in obstacles(fishbone)}
    golden_by_id = {str(r.get("golden_id")): r for r in golden}
    errors, warns, rows, used = [], [], [], Counter()
    seen_ids = set()
    for c in draft["criteria"]:
        if not isinstance(c, dict):
            errors.append("criteria 里每一项都要是对象")
            continue
        e, w = check_rules(c, catalog)  # types first: an unhashable id must not crash the checks below
        cid = c.get("id").strip() if isinstance(c.get("id"), str) else ""
        oid = c.get("obstacle_id") if isinstance(c.get("obstacle_id"), str) else None
        if not cid or cid in seen_ids:
            errors.append(f"判据编号为空或重复：{cid!r}")
        seen_ids.add(cid)
        if oid not in obs:
            errors.append(f"{cid}：obstacle_id {c.get('obstacle_id')!r} 不是鱼骨图里已确认的障碍（可选：{', '.join(obs) or '无'}）")
        elif str(c.get("sticky_text") or "").strip() not in ("", obs[oid]["sticky_text"]):
            errors.append(f"{cid}：{oid} 现在的便签是“{obs[oid]['sticky_text']}”，和草稿里的“{c.get('sticky_text')}”不一致："
                          "鱼骨图改过，核对 obstacle_id（或 skeleton --force 重来）")
        if oid is not None:
            used[oid] += 1
        errors += e
        warns += w
        if e:
            continue
        try:
            level, notes, fixable, w2 = coverage(c, catalog, golden_by_id)
        except CriteriaError as ex:
            errors.append(str(ex))
            continue
        warns += w2
        rows.append({"id": cid, "obstacle_id": oid, "sticky_text": obs.get(oid, {}).get("sticky_text", ""),
                     "fishbone_dimension": obs.get(oid, {}).get("fishbone_dimension", ""),
                     "text": str(c.get("text")).strip(), "dimension": c.get("dimension"), "tier": c.get("tier"),
                     "threshold": None if c.get("tier") == "红线" else str(c.get("threshold")).strip(),
                     "executor": c.get("executor"),
                     "checks": [{k: catalog[x].get(k) for k in ("id", "name", "evaluator", "field", "executor", "tier", "where")}
                                for x in c.get("checks") or []],
                     "coverage": level, "coverage_notes": notes, "golden_ids": [str(g) for g in c.get("golden_ids") or []],
                     "golden_fixable": fixable, "golden_gap": str(c.get("golden_gap") or "").strip(),
                     "note": str(c.get("note") or "")})
    for oid in obs:
        if used[oid] == 0:
            errors.append(f"障碍 {oid}（{obs[oid]['sticky_text']}）还没有判据：每条已确认的障碍写一条判据")
        elif used[oid] > 1:
            errors.append(f"障碍 {oid} 写了 {used[oid]} 条判据：每条障碍只写一条（拆不开就合成一条更具体的）")
    if errors:
        raise CriteriaError("判据草稿有问题，没有写入：\n  " + "\n  ".join(errors))
    applied = applied_rows(draft, {r["id"] for r in rows}, golden)
    scen = scenario_of(fishbone, draft)
    if draft.get("golden_proposals") and scen != "lab":
        why = ("鱼骨图的背景材料里没有实验角色卡" if scen == "unverified" else "这不是实验场景")
        raise CriteriaError(f"{why}：实验黄金集只收需求分诊 Agent（实验场景）的样本，这次不补黄金样本，把 golden_proposals 清空")
    summary, csv_rows, extra, pwarns = proposals(draft, {r["id"] for r in rows}, golden, feedback)
    warns += pwarns
    planned = Counter(p["criterion"] for p in summary)
    for r in rows:
        if planned.get(r["id"]):
            r["coverage_notes"].append(f"已提议补 {planned[r['id']]} 条黄金样本（写入后把新编号填进 golden_ids 再 build）")
    stamp = (now or datetime.now(timezone.utc)).astimezone(BEIJING).strftime("%Y-%m-%d %H:%M")
    meta = fishbone.get("metadata") if isinstance(fishbone.get("metadata"), dict) else {}
    doc = {"schema_version": "1.0", "generated_at": stamp + " 北京时间",
           "metadata": {k: meta.get(k) for k in ("customer", "date", "use_case", "service_target")},
           "source": {"fishbone_sha256": hashlib.sha256((d / "fishbone-data.json").read_bytes()).hexdigest()[:12],
                      "golden_count": len(golden)},
           "criteria": rows, "warnings": list(dict.fromkeys(warns)),
           "summary": {"tier": dict(Counter(r["tier"] for r in rows)), "coverage": dict(Counter(r["coverage"] for r in rows)),
                       "executor": dict(Counter(r["executor"] for r in rows)),
                       "golden_fixable": [r["id"] for r in rows if r["golden_fixable"]]},
           "scenario": scen, "golden_proposals": summary, "golden_applied": applied,
           "apply_steps": apply_steps(d, len(extra)) if summary else []}
    if write:
        (d / OUT_JSON).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (d / OUT_MD).write_text(render_md(doc), encoding="utf-8")
        for name in (PROPOSAL_CSV, PROPOSAL_EXTRA):
            if (d / name).exists():
                (d / name).unlink()  # a preview from an earlier build must not outlive the proposals it showed
        if csv_rows:
            C.write_csv(str(d / PROPOSAL_CSV), csv_rows, G.FEEDBACK_CSV_FIELDS)
        if extra:
            C.write_jsonl(str(d / PROPOSAL_EXTRA), extra)
    return doc


def _rel(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(REPO.resolve()))
    except ValueError:
        return str(p)


def apply_steps(d: Path, n_extra: int) -> List[Dict[str, str]]:
    """The existing tools, in order: [{cmd, note}] (cmd empty = a manual edit described by note)."""
    q = shlex.quote
    verify_cmd = f"python3 .claude/skills/adlc-criteria/scripts/criteria.py verify {q(_rel(d))}"
    steps = []
    if n_extra:
        steps += [{"cmd": "", "note": h} for h in mock_params_hint(n_extra)]
        steps.append({"cmd": "python3 data/tools/generate_mock_data.py", "note": "重新生成上个月的需求反馈（第 2.4 步的工具）"})
    steps += [{"cmd": verify_cmd + " --before-apply", "note": "确认需求编号都在需求反馈里"},
              {"cmd": f"python3 data/tools/golden_tool.py apply-feedback --feedback {q(_rel(d / PROPOSAL_CSV))} --dry-run",
               "note": "试运行，看分布和警告"},
              {"cmd": f"python3 data/tools/golden_tool.py apply-feedback --feedback {q(_rel(d / PROPOSAL_CSV))}",
               "note": "写入黄金集（自动备份）"},
              {"cmd": "python3 data/tools/golden_tool.py freeze", "note": "冻结并同步到 evaluators/*/golden_set.json"},
              {"cmd": verify_cmd, "note": "确认已写入、已冻结、已同步"},
              {"cmd": "./40-create-evaluators.sh", "note": "黄金集打包在评估器 Lambda 里，不重新部署就还是旧黄金集"}]
    if n_extra:
        steps.append({"cmd": "./30-deploy-runtime.sh", "note": "Agent 的相似需求工具读的是打包进 Runtime 的需求反馈"})
    return steps


def _md(v) -> str:
    return " ".join(str(v if v is not None else "").split()).replace("|", "\\|")


def render_md(doc: dict) -> str:
    m = doc.get("metadata") or {}
    s = doc.get("summary") or {}
    lines = [f"# 判据表 · {_md(m.get('use_case'))}", "",
             f"业务方：{_md(m.get('customer'))} · 日期：{_md(m.get('date'))} · 生成于 {doc.get('generated_at')}", "",
             "三档：" + "、".join(f"{k} {v}" for k, v in (s.get("tier") or {}).items())
             + " · 覆盖：" + "、".join(f"{k} {v}" for k, v in (s.get("coverage") or {}).items()), "",
             "| ID | 判据（主语是 Agent） | 维度 | 档位 | 阈值 | 执行方式 | 现有评估器覆盖 | 黄金样本 | 来源障碍 |",
             "|----|----|----|----|----|----|----|----|----|"]
    for r in doc.get("criteria") or []:
        who = "；".join(f"{c['id']}（{c['evaluator']}）" for c in r["checks"]) or "—"
        notes = "；".join(r.get("coverage_notes") or [])
        lines.append(f"| {_md(r['id'])} | {_md(r['text'])} | {_md(r['dimension'])} | {_md(r['tier'])} | "
                     f"{_md(r['threshold'] if r['threshold'] else '0 违例（不打分）')} | {_md(r['executor'])} | "
                     f"**{_md(r['coverage'])}**：{_md(who)}{('<br>' + _md(notes)) if notes else ''} | "
                     f"{_md('、'.join(r['golden_ids']) or '—')} | {_md(r['sticky_text'])} |")
    if doc.get("warnings"):
        lines += ["", "## 提醒", ""] + [f"- {_md(w)}" for w in doc["warnings"]]
    if doc.get("golden_proposals"):
        lines += ["", "## 提议补充的黄金样本（预览，业务方同意后才写入）", "",
                  "| 判据 | 需求编号 | 来源 | 标题 | 期望标签 | 为什么 |", "|----|----|----|----|----|----|"]
        for p in doc["golden_proposals"]:
            src = "新写（extra-feedback）" if p["source"] == "extra" else "上个月已有"
            lines.append(f"| {_md(p['criterion'])} | {_md(p['feedback_id'])} | {src} | {_md(p['title'])} | "
                         f"{_md(p['labels'])} | {_md(p['why'])} |")
        lines += ["", "写入步骤（业务方同意后；写完把这些提议从 golden_proposals 挪到 golden_applied）：", ""] + [
            f"{i}. " + (f"`{st['cmd']}` —— {st['note']}" if st.get("cmd") else st.get("note", ""))
            for i, st in enumerate(doc.get("apply_steps") or [], 1)]
    if doc.get("golden_applied"):
        lines += ["", "## 已写入黄金集的样本（记得重新运行 `./40-create-evaluators.sh`）", "",
                  "| 判据 | 黄金编号 | 需求编号 | 标题 | 期望标签 | 版本 |", "|----|----|----|----|----|----|"]
        for p in doc["golden_applied"]:
            lines.append(f"| {_md(p['criterion'])} | {_md(p['golden_id'])} | {_md(p['feedback_id'])} | {_md(p['title'])} | "
                         f"{_md(p['labels'])} | {_md(p['version'])} |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- verify (after applying)
def verify(out_dir, before_apply: bool = False) -> Tuple[bool, List[str]]:
    """before_apply: only the feedback items must exist (ok before golden_tool.py apply-feedback has run)."""
    d = Path(out_dir)
    doc = _read_json(d / OUT_JSON)
    props = doc.get("golden_proposals") or []
    if not props:
        return True, ["criteria.json 里没有黄金样本提议，不需要写入"]
    fb = {str(r.get("id")) for r in feedback_rows()}
    golden = golden_rows()
    by_fid = {str(r.get("feedback_id")): r for r in golden}
    lines, ok = [], True
    for p in props:
        fid = p["feedback_id"]
        in_fb = fid in fb
        g = by_fid.get(fid)
        ok = ok and in_fb and (before_apply or g is not None)
        lines.append(f"{'✅' if in_fb else '❌'} {fid} 在上个月的需求反馈里" + ("" if in_fb else "（新写的条目要先追加并运行 generate_mock_data.py）"))
        lines.append(f"{'✅' if g else '⏳'} {fid} 在黄金集里" + (f"：{g.get('golden_id')}（把它填进 {p['criterion']} 的 golden_ids 后重新 build）" if g else "（还没运行 apply-feedback）"))
    man = Path(C.GOLDEN_DIR) / "MANIFEST.json"
    frozen = False
    try:
        frozen = json.loads(man.read_text(encoding="utf-8")).get("sha256") == hashlib.sha256(Path(C.GOLDEN_PATH).read_bytes()).hexdigest()
    except (OSError, ValueError):
        pass
    ok = ok and (before_apply or frozen)
    lines.append(f"{'✅' if frozen else '⏳'} 黄金集已冻结（MANIFEST.json 与 golden_set.jsonl 一致）" + ("" if frozen else "：运行 golden_tool.py freeze"))
    synced = True
    for lam in ("adlc_label_accuracy", "adlc_reject_quality"):
        p = REPO / "evaluators" / lam / "golden_set.json"
        try:
            ids = set((json.loads(p.read_text(encoding="utf-8")).get("items") or {}).keys())
        except (OSError, ValueError, AttributeError):
            ids = set()
        synced = synced and all(x["feedback_id"] in ids for x in props)
    ok = ok and (before_apply or synced)
    lines.append(f"{'✅' if synced else '⏳'} 已同步到 evaluators/*/golden_set.json" + ("" if synced else "（freeze 会自动同步）"))
    lines.append("👉 最后一步（本脚本查不到）：./40-create-evaluators.sh 重新部署评估器，新黄金集才在评估里生效"
                 + ("；新增了需求反馈时还要 ./30-deploy-runtime.sh" if any(x["source"] == "extra" for x in props) else ""))
    return ok, lines


# ---------------------------------------------------------------- skeleton / listings
def skeleton(out_dir, force: bool = False) -> Path:
    d = Path(out_dir)
    fishbone = _read_json(d / "fishbone-data.json")
    check_fishbone(fishbone)
    obs = obstacles(fishbone)
    if not obs:
        raise CriteriaError("鱼骨图里没有已确认的障碍，没有可写的判据")
    path = d / DRAFT
    if path.exists() and not force:
        raise CriteriaError(f"{_rel(path)} 已存在，未覆盖（确实要重来就加 --force）")
    draft = {"criteria": [{"id": f"C-{i:02d}", "obstacle_id": o["obstacle_id"], "sticky_text": o["sticky_text"],
                           "evidence": o["evidence"], "text": "", "dimension": o["fishbone_dimension"], "tier": "",
                           "threshold": "", "executor": "", "checks": [], "golden_ids": [], "golden_gap": "", "note": ""}
                          for i, o in enumerate(obs, 1)],
             "scenario": "", "golden_proposals": []}
    path.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def catalog_lines(catalog: Dict[str, dict]) -> List[str]:
    out = []
    for c in catalog.values():
        cov = "已覆盖" if c.get("coverage") == "covered" else "部分覆盖"
        req = c.get("golden_requires")
        need = ("要黄金样本" + (f"（{json.dumps(req, ensure_ascii=False)}）" if req else "")) if c.get("needs_golden") else "不需要黄金样本"
        out.append(f"{c['id']:<28} {c['executor']}·{c['tier']}·{cov}·{need} | {c['name']} | {c['evaluator']}")
    return out


def golden_lines(golden: List[dict]) -> List[str]:
    out = []
    for r in golden:
        e, i = r.get("expected") or {}, r.get("input") or {}
        out.append(f"{r.get('golden_id')} {r.get('feedback_id')} | {i.get('title')} | {i.get('feedback_type')} | "
                   f"重要{e.get('important')} 紧急{e.get('urgent')} {e.get('decision')}{'/'.join([''] + [a for a in r.get('acceptable_decisions') or [] if a != e.get('decision')])}"
                   f"{' 不可自动拒绝' if r.get('can_auto_reject', True) is False else ''} | {r.get('case_type')} | {'/'.join(r.get('dimensions') or [])}")
    return out


def candidate_lines(feedback: List[dict], golden: List[dict]) -> List[str]:
    """Last month's feedback items that are not in the golden set, with the seed pool's labels when known."""
    seed = C.read_jsonl(C.POOL_PATH) if os.path.exists(C.POOL_PATH) else []
    extra_path = os.path.join(C.templates_dir(), "extra-feedback.jsonl")
    seed += C.read_jsonl(extra_path) if os.path.exists(extra_path) else []
    by_id = {str(r.get("id")): r for r in seed}
    by_text = {(r.get("title"), r.get("content")): r for r in seed}
    in_golden = {str(r.get("feedback_id")) for r in golden}
    out = []
    for r in feedback:
        fid = str(r.get("id"))
        if fid in in_golden:
            continue
        s = by_id.get(fid) if by_id.get(fid, {}).get("title") == r.get("title") else by_text.get((r.get("title"), r.get("content")))
        m = (s or {}).get("_meta") or {}
        lab = (f"种子标注：{m.get('category')} 重要{m.get('important')} 紧急{m.get('urgent')} {m.get('decision')}"
               f"{' 不可自动拒绝' if m.get('can_auto_reject') is False else ''} | {m.get('rationale') or ''}") if m else "种子库里没有（自己标注）"
        out.append(f"{fid} | {r.get('title')} | {r.get('feedback_type')} · {r.get('customer_tier')} · {r.get('customer')} | {lab}")
    return out


def hint_lines(path: Path = SCENARIO_MAP) -> List[str]:
    """The sample scenario's pain points with their catalog / feedback / golden ids (facilitator only)."""
    doc = _read_json(path)
    out = [f"场景对照（{doc.get('role_card')}；只给主持人和判据环节用，不要念给业务方）："]
    for p in doc.get("pain_points") or []:
        ids = " ".join(f"{k}={','.join(p.get(k) or []) or '—'}" for k in ("catalog", "feedback", "golden", "not_in_golden"))
        out.append(f"{p.get('no')} {p.get('title')}（{p.get('dimension_hint')}）| {ids} | {p.get('note')}")
    return out


def output_lines(d: Path, doc: dict) -> List[str]:
    """Absolute path + what it is, one line per file the build wrote."""
    roles = [(OUT_JSON, "判据表（结构化，以它为准）"), (OUT_MD, "判据表（Markdown）"),
             (PROPOSAL_CSV, "黄金样本提议（golden-set-feedback.csv 格式，预览，未写入）"),
             (PROPOSAL_EXTRA, "新写的需求反馈（extra-feedback.jsonl 格式，预览，未写入）"), (DRAFT, "判据草稿")]
    return [f"  {(d / name).resolve()}  —— {role}" for name, role in roles if (d / name).exists()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="障碍 → 判据 → 黄金集（adlc-criteria skill）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("skeleton")
    s.add_argument("out_dir")
    s.add_argument("--force", action="store_true")
    sub.add_parser("catalog")
    sub.add_parser("golden")
    sub.add_parser("candidates")
    sub.add_parser("hints")
    s = sub.add_parser("build")
    s.add_argument("out_dir")
    s = sub.add_parser("verify")
    s.add_argument("out_dir")
    s.add_argument("--before-apply", action="store_true", help="写入黄金集之前：只检查需求编号都在需求反馈里")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "catalog":
            print("\n".join(catalog_lines(load_catalog())))
        elif a.cmd == "golden":
            print("\n".join(golden_lines(golden_rows())))
        elif a.cmd == "hints":
            print("\n".join(hint_lines()))
        elif a.cmd == "candidates":
            print("\n".join(candidate_lines(feedback_rows(), golden_rows())))
        elif a.cmd == "skeleton":
            p = skeleton(a.out_dir, a.force)
            print(f"已写入 {_rel(p)}：每条已确认的障碍一条空判据。可选的 checks（criteria.py catalog）：")
            print("\n".join(catalog_lines(load_catalog())))
            print("\n当前黄金集（criteria.py golden）：")
            print("\n".join(golden_lines(golden_rows())))
        elif a.cmd == "build":
            doc = build(a.out_dir)
            d = Path(a.out_dir)
            print(f"✅ 判据表 {len(doc['criteria'])} 条 → {_rel(d / OUT_JSON)}、{_rel(d / OUT_MD)}")
            print("   三档：" + json.dumps(doc["summary"]["tier"], ensure_ascii=False)
                  + "  覆盖：" + json.dumps(doc["summary"]["coverage"], ensure_ascii=False))
            if doc["summary"]["golden_fixable"]:
                print("   可以靠补黄金样本补上缺口的判据：" + "、".join(doc["summary"]["golden_fixable"]))
            for w in doc["warnings"]:
                print("   ⚠️ " + w)
            if doc["golden_proposals"]:
                print(f"   黄金样本提议 {len(doc['golden_proposals'])} 条（预览，未写入 data/）")
            print(f"输出目录：{d.resolve()}")
            print("\n".join(output_lines(d, doc)))
        elif a.cmd == "verify":
            ok, lines = verify(a.out_dir, a.before_apply)
            print("\n".join(lines))
            return 0 if ok else 1
    except CriteriaError as e:
        print(f"❌ {e}")
        return 1
    except (TypeError, ValueError, AttributeError, KeyError) as e:  # a malformed draft must end in a message
        print(f"❌ 草稿或数据文件格式不对，没有写入（{type(e).__name__}: {e}）：按 SKILL.md 第 2 步的字段说明检查 {DRAFT}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
