"""Step 7.5: list the failed golden items of one evaluation with their five-dimension labels.

  python -m labkit.failures                              # latest results/eval-*.json
  python -m labkit.failures results/eval-A-v0-....json   # a specific evaluation
  python -m labkit.failures <eval.json> --golden data/golden/golden_set.jsonl

For every session whose label_accuracy is Fail / CriticalFail it prints the wrong fields (expected ->
actual), the golden row's `dimensions` (认知/质量/责任/成本/性能), `case_type` and a clipped `rationale`,
joined by feedback_id. The wrong fields come from the label_accuracy evaluator's own explanation
("需求 FB-…（G…）：… 是否重要 错（期望 否，实际 是）…", the block of this session's feedback id), so they match
the score exactly; only when that is missing are the agent output and the golden labels compared here
(an approximation of the evaluator's normalisation). Sessions that were not scored right/wrong (FormatError,
Skipped, evaluator or agent errors) are listed separately and not counted. Ends with a per-dimension
tally (an item with several dimensions counts once in each) and a per-field tally. Standard library only.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

from . import items
from .compare import parse_obj
from .config import ROOT

DIMS = ("认知", "质量", "责任", "成本", "性能")
# (golden key, per-field evaluator, short name, name in the evaluator explanation)
FIELDS = (("important", "label_important", "重要", "是否重要"), ("urgent", "label_urgent", "紧急", "是否紧急"),
          ("decision", "label_decision", "建议", "处理建议"))
LEVEL = ("level", None, "优先级", "优先级")  # only scored when the label scheme is "level"
WRONG = ("Fail", "CriticalFail")
_EXPLAINED = re.compile(r"(是否重要|是否紧急|处理建议|优先级) (对|错)（期望 (.*?)，实际 (.*?)）")
_BLOCK = re.compile(r"需求 (\S+?)（")
_NEGATION = re.compile(r"(不|暂不|不予|无需|无须|无法|没有|won'?t|not\b|^un|n't)")

# The evaluator's tolerated variants (evaluators/s12_label_accuracy/span_adapter.py), for the fallback only.
_KEYS = {"important": ("important", "is_important", "importance", "是否重要", "重要"),
         "urgent": ("urgent", "is_urgent", "urgency", "是否紧急", "紧急"),
         "decision": ("decision", "action", "suggestion", "recommendation", "处理建议", "建议")}
_YES = {"是", "yes", "y", "true", "1", "重要", "紧急", "高"}
_NO = {"否", "no", "n", "false", "0", "不重要", "不紧急", "低"}
_DECISIONS = (("待评估", ("待评估", "需评估", "评估", "pending", "evaluate", "needs review", "待定", "澄清", "补充信息")),
              ("拒绝", ("拒绝", "reject", "rejected", "decline", "不做", "驳回")),
              ("迭代", ("迭代", "iterate", "accept", "纳入", "开发", "修复", "fix")))


def width(s: str) -> int:
    """Terminal columns: CJK / full-width characters take two, ambiguous ones (… → · ─) are counted as two too."""
    return sum(2 if unicodedata.east_asian_width(c) in "WFA" else 1 for c in s)


def clip(s, cols: int) -> str:
    """Collapse whitespace and cut to at most `cols` terminal columns (with a trailing …)."""
    s = " ".join(str(s or "").split())
    if width(s) <= cols:
        return s
    out, w = [], 0
    for c in s:
        cw = width(c)
        if w + cw > cols - 2:
            break
        out.append(c)
        w += cw
    return "".join(out) + "…"


def latest_eval() -> Path:
    files = glob.glob(str(ROOT / "results" / "eval-*.json"))
    if not files:
        raise SystemExit("❌ results/ 里还没有 eval-*.json：先完成 7.2（./31-run-eval.sh）")
    return Path(max(files, key=os.path.getmtime))


def shown(p: Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(p)


def _d(v) -> dict:
    return v if isinstance(v, dict) else {}


def _norm(key: str, v):
    if v is None:
        return None
    if key == "decision":
        s = str(v).strip().lower()
        exact = next((canon for canon, words in _DECISIONS if s == canon or s in words), None)
        if exact or not s or _NEGATION.search(s):
            return exact or (str(v).strip() or None)
        return next((canon for canon, words in _DECISIONS if any(w in s for w in words)), str(v).strip())
    if isinstance(v, bool):
        return "是" if v else "否"
    s = str(v).strip().lower()
    return "是" if s in _YES else ("否" if s in _NO else (str(v).strip() or None))


def _acceptable(row: dict, key: str) -> list:
    """Golden answers that count as right (same rule as the label evaluators: decision may list several)."""
    if key == "decision" and isinstance(row.get("acceptable_decisions"), list) and row["acceptable_decisions"]:
        return [str(x) for x in row["acceptable_decisions"]]
    v = items.expected_of(row).get(key)
    return [str(v)] if v not in (None, "") else []


def explained(text, fid=None) -> list | None:
    """[(name, correct, expected, actual)] for every field the evaluator scored, or None if it lists none.

    A batch output is explained as several "需求 <id>（…）：…" blocks: only the block of `fid` is used
    (a single block is used as is)."""
    text = str(text or "")
    starts = [(m.start(), m.group(1)) for m in _BLOCK.finditer(text)]
    if len(starts) > 1:
        seg = [text[a:(starts[i + 1][0] if i + 1 < len(starts) else len(text))]
               for i, (a, f) in enumerate(starts) if f == fid]
        if not seg:
            return None
        text = seg[0]
    parts = _EXPLAINED.findall(text)
    if not parts:
        return None
    short = {f[3]: f[2] for f in (*FIELDS, LEVEL)}
    return [(short[n], mark == "对", exp, "—" if act in ("None", "") else act) for n, mark, exp, act in parts]


def from_explanation(text, fid=None) -> list | None:
    """[(name, expected, actual)] of the fields the evaluator marked 错, or None if it lists no fields."""
    got = explained(text, fid)
    return None if got is None else [(n, e, a) for n, ok, e, a in got if not ok]


def wrong_fields(session: dict, row: dict) -> list:
    """[(field name, expected, actual)] for the wrong fields of one session.

    1. the label_accuracy evaluator's explanation (what was actually scored);
    2. else, with a golden row: the agent output (keys / values normalised like the evaluator does)
       against the golden labels; decision is right when it is one of `acceptable_decisions`;
    3. else: the per-field evaluator labels (label_important / urgent / decision)."""
    scores = _d(session.get("scores"))
    got = from_explanation(_d(scores.get("label_accuracy")).get("explanation"), str(session.get("feedback_id") or ""))
    if got is not None:
        return got
    out = parse_obj(session.get("agent_output"))
    res = []
    for key, ev, name, _cn in FIELDS:
        raw = next((out[k] for k in _KEYS[key] if out.get(k) not in (None, "")), None)
        act = _norm(key, raw)
        if row:
            ok = _acceptable(row, key)
            if act not in ok:
                res.append((name, "/".join(ok) or "—", act or "—"))
        elif _d(scores.get(ev)).get("label") in WRONG:
            res.append((name, "?", act or "—"))
    return res


def other_reason(session: dict) -> str:
    sc = _d(_d(session.get("scores")).get("label_accuracy"))
    if session.get("agent_error"):
        return "Agent 报错：" + str(session["agent_error"])
    if session.get("error"):
        return "评估出错：" + str(session["error"])
    if sc.get("errorCode"):
        return f"评估器出错：{sc.get('errorCode')} {sc.get('errorMessage') or ''}".strip()
    if not sc:
        return "没有 label_accuracy 分数"
    return f"{sc.get('label')}：{sc.get('explanation') or ''}"


def _dims(row: dict) -> list:
    v = row.get("dimensions")
    if v is None:
        return []
    if isinstance(v, str):
        v = re.split(r"[、,，/;；\s]+", v)
    elif not isinstance(v, (list, tuple)):
        v = [v]
    return [d for d in dict.fromkeys(str(x).strip() for x in v) if d]


def analyze(ev: dict, golden: dict) -> dict:
    wrong, other = [], []
    sessions = [s for s in (ev.get("sessions") or []) if isinstance(s, dict)]
    for s in sessions:
        sc = _d(_d(s.get("scores")).get("label_accuracy"))
        label = sc.get("label")
        if label == "Pass":
            continue
        fid = str(s.get("feedback_id") or "（无编号）")
        row = _d(golden.get(fid))
        if label in WRONG:
            wrong.append({"fid": fid, "gid": row.get("golden_id"), "label": label, "in_golden": bool(row),
                          "dims": _dims(row), "case_type": row.get("case_type"), "rationale": row.get("rationale"),
                          "fields": wrong_fields(s, row), "explanation": sc.get("explanation"),
                          "scored": [x[0] for x in (explained(sc.get("explanation"), fid) or [])]})
        else:
            other.append({"fid": fid, "label": label or "—", "reason": other_reason(s)})
    dim_count = {d: 0 for d in DIMS}
    for w in wrong:
        for d in w["dims"]:
            dim_count[d] = dim_count.get(d, 0) + 1
    # tally the fields the evaluator scored (label scheme "level" scores 优先级 + 建议); default: the three labels
    seen = {n for w in wrong for n in w["scored"]}
    names = [n for n in (f[2] for f in (*FIELDS, LEVEL)) if n in seen] or [f[2] for f in FIELDS]
    names += [n for n in dict.fromkeys(f[0] for w in wrong for f in w["fields"]) if n not in names]
    field_count = {n: sum(1 for w in wrong for f in w["fields"] if f[0] == n) for n in names}
    return {"wrong": wrong, "other": other, "dims": dim_count, "fields": field_count, "total": len(sessions)}


def render(ev: dict, res: dict, eval_path: Path, golden_path: Path) -> str:
    run = _d(ev.get("run"))
    wrong, other = res["wrong"], res["other"]
    lines = [f"▶ 失败用例归因：{shown(eval_path)}",
             f"  黄金集：{shown(golden_path)}",
             f"  run {run.get('run_id', '?')} · 共 {res['total']} 条 · 判错 {len(wrong)} 条 · 其他未通过 {len(other)} 条",
             ""]
    lines.append(f"── 判错的需求（{len(wrong)} 条）──")
    for w in wrong:
        gid = f"（{w['gid']}）" if w["gid"] else ""
        sev = "  ⚠ 严重：误拒了不可自动拒绝的需求" if w["label"] == "CriticalFail" else ""
        if w["in_golden"]:
            dims = "、".join(w["dims"]) or "—"
            lines.append(f"{w['fid']}{gid}  五维：{dims}  类型：{w['case_type'] or '—'}{sev}")
        else:
            lines.append(f"{w['fid']}  （黄金集里没有这条，无五维标签）{sev}")
        if w["fields"]:
            lines.append("  错（期望→实际）：" + "；".join(f"{n} {e}→{a}" for n, e, a in w["fields"]))
        else:
            lines.append("  错：" + clip(w["explanation"] or "（评估器没有给出逐项结果）", 74))
        if w["rationale"]:
            lines.append("  理由：" + clip(w["rationale"], 74))
    if not wrong:
        lines.append("  （没有判错的需求）")
    if other:
        lines += ["", f"── 其他未通过（{len(other)} 条，不计入下面的统计）──"]
        lines += [f"{o['fid']}  {clip(o['reason'], 72)}" for o in other]
    dims = " · ".join(f"{d} {n} 条" for d, n in res["dims"].items())
    lines += ["", f"── 五维统计（判错 {len(wrong)} 条；一条有多个维度时每个维度各计一次）──", "  " + dims,
              "── 错在哪一项 ──", "  " + " · ".join(f"{k} {n} 条" for k, n in res["fields"].items())]
    return "\n".join(lines)


def load_golden(path: Path) -> dict:
    idx = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise SystemExit(f"❌ 无法读取黄金集 {path}：{e}")
    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            print(f"⚠️  黄金集第 {i} 行不是合法 JSON，已跳过", file=sys.stderr)
            continue
        if isinstance(row, dict) and items.fid_of(row):
            idx[items.fid_of(row)] = row
    return idx


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m labkit.failures", description="列出判错的黄金集用例并按五维统计（7.5）")
    ap.add_argument("eval", nargs="?", help="评估结果 JSON（默认：results/ 里最新的 eval-*.json）")
    ap.add_argument("--golden", help="黄金集 JSONL（默认：data/golden/golden_set.jsonl）")
    a = ap.parse_args(argv)
    eval_path = Path(a.eval) if a.eval else latest_eval()
    if not eval_path.exists() and (ROOT / eval_path).exists():
        eval_path = ROOT / eval_path
    try:
        ev = json.loads(eval_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"❌ 无法读取评估结果 {eval_path}：{e}")
    if not isinstance(ev, dict):
        raise SystemExit(f"❌ {eval_path} 不是评估结果文件（应为 31-run-eval.sh 生成的 eval-*.json）")
    if a.golden:
        golden_path = Path(a.golden)
    else:
        try:
            golden_path = items.golden_path()
        except FileNotFoundError:
            raise SystemExit("❌ 找不到黄金集 data/golden/golden_set.jsonl（第 2 章生成），或用 --golden 指定") from None
    print(render(ev, analyze(ev, load_golden(golden_path)), eval_path, golden_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
