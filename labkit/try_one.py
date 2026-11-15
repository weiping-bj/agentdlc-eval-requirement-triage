"""Chapters 4/5: send ONE requirement to the deployed Runtime and print the agent's structured output.

  python -m labkit.try_one FB-2610-009             # an item of the golden set or last month's pool (or a golden id: G06)
  python -m labkit.try_one --text "希望……"         # your own text (free text: not in the golden set)
  python -m labkit.try_one FB-2610-004 --eval      # also run the evaluators on this one session (waits for its trace)

Same invocation as labkit.batch (one session, the Runtime of .lab-state/runtime.json) and, with --eval, the same
per-session evaluation as labkit.evaluate (golden labels as the reference input when the item is in the golden set).
A tryout is not a run: nothing is written to .lab-state/runs.json or results/, so it never enters 43 / 45 / 51.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone

from . import aws, batch, items

MAX_TEXT = 4000
FIELDS = (("important", "是否重要"), ("urgent", "是否紧急"), ("decision", "处理建议"), ("level", "优先级"))
TEXTS = (("reasoning", "理由"), ("reject_reason", "拒绝理由"), ("customer_reply", "客户话术"))
ID_RE = re.compile(r"^(?:FB-[\w-]+|G\d{1,4})$", re.I)
WIDTH = 100  # wrap long text lines in the terminal


class UsageError(SystemExit):
    pass


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="./46-try-one.sh", description="给已部署的 Runtime 发一条需求，打印 Agent 的结构化输出")
    ap.add_argument("item", nargs="?", help="需求编号：黄金集或上月需求池里的 FB-…，或黄金集编号 G…")
    ap.add_argument("--text", help="自己写的一条需求（整段文字，用引号括起来）")
    ap.add_argument("--eval", action="store_true", help="再对这一个 session 跑一遍评估器（要等 trace 写入 CloudWatch，约 2–3 分钟）")
    ap.add_argument("--no-builtin", action="store_true", help="--eval 时不跑内置评估器（Builtin.*）")
    ap.add_argument("--raw", action="store_true", help="同时打印模型的原始输出")
    a = ap.parse_args(argv)
    if bool(a.item) == bool(a.text):
        ap.error("给一个需求编号，或者用 --text 写一条需求（二选一）")
    if a.text is not None:
        a.text = a.text.strip()
        if not a.text:
            ap.error("--text 不能是空的")
        if len(a.text) > MAX_TEXT:
            ap.error(f"--text 太长（{len(a.text)} 字，最多 {MAX_TEXT} 字）")
    if a.no_builtin and not a.eval:
        ap.error("--no-builtin 只和 --eval 一起用")
    return a


def find_item(arg: str, golden: list, pool: list) -> tuple:
    """(row, golden row or None) for a feedback id (FB-…) or a golden id (G…), case-insensitive."""
    key = arg.strip()
    if not ID_RE.match(key):
        raise UsageError(f"❌ {key!r} 不像需求编号（FB-2610-001 或 G01 这种）。要试自己写的需求：--text \"……\"")
    k = key.upper()
    for r in golden:
        if items.fid_of(r).upper() == k or str(r.get("golden_id") or "").upper() == k:
            return r, r
    for r in pool:
        if items.fid_of(r).upper() == k:
            return r, None
    ids = ", ".join(items.fid_of(r) for r in golden[:3])
    raise UsageError(f"❌ 黄金集和上月需求池里都没有 {key}（例如 {ids} …）。要试自己写的需求：--text \"……\"")


def free_text_row(text: str, now: datetime | None = None) -> dict:
    ts = (now or datetime.now(timezone.utc)).strftime("%Y%m%d%H%M%S")
    first = text.splitlines()[0].strip()
    return {"id": f"TRY-{ts}", "title": first[:40], "content": text}


def _wrap(s: str, indent: str) -> list:
    out = []
    for para in str(s).splitlines() or [""]:
        while len(para) > WIDTH:
            out.append(indent + para[:WIDTH])
            para = para[WIDTH:]
        out.append(indent + para)
    return out


def _val(v) -> str:
    if v is None or v == "":
        return "—"
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def output_lines(row: dict, golden: dict | None, result, raw: str | None, show_raw: bool) -> list:
    """The agent's structured output, each label next to the golden answer when there is one."""
    it = items.item_of(row)
    out = result if isinstance(result, dict) else {}
    L = [f"需求 {items.fid_of(row)}" + (f"（{golden.get('golden_id')}）" if golden and golden.get("golden_id") else "")
         + (f"：{it.get('title')}" if it.get("title") else "")]
    exp = items.expected_of(golden) if golden else {}
    acc = golden.get("acceptable_decisions") if golden else None
    for k, cn in FIELDS:
        if k == "level" and out.get(k) in (None, "", "none", "null") and k not in exp:
            continue
        got = _val(out.get(k))
        line = f"  {cn}  {got}"
        if golden:
            want = "/".join(map(str, acc)) if k == "decision" and isinstance(acc, list) and acc else exp.get(k)
            if want not in (None, ""):
                ok = got in str(want).split("/")
                line += f"    黄金集 {want} {'✓' if ok else '✗'}"
        L.append(line)
    for k, cn in TEXTS:
        v = out.get(k)
        if v in (None, ""):
            L.append(f"  {cn}  —")
        else:
            L.append(f"  {cn}：")
            L += _wrap(_val(v), "    ")
    for k in ("_parse_error", "_warnings"):
        if out.get(k):
            L.append(f"  ⚠️  {k}：{_val(out.get(k))}")
    if not out:
        L.append("  ⚠️  Agent 没有返回结构化结果（见下面的原始输出）" if raw else "  ⚠️  Agent 没有返回结构化结果")
    if golden:
        L.append("  （“黄金集 ✓/✗”是直接和黄金集比对；正式的分数以评估器为准：加 --eval）")
    if show_raw or (not out and raw):
        L.append("  原始输出：")
        L += _wrap(raw or "（空）", "    ")
    return L


def score_lines(sess: dict, golden: dict | None) -> list:
    """One line per evaluator (label, value, explanation) and the red lines of this one item."""
    from . import compare
    if sess.get("error"):
        return [f"  ⚠️  评估没完成：{sess['error']}"]
    sc = sess.get("scores") or {}
    names = [n for n in compare.EVAL_ORDER if n in sc] + sorted(set(sc) - set(compare.EVAL_ORDER))
    L = []
    for n in names:
        e = sc[n] or {}
        if e.get("errorCode") or e.get("errorMessage"):
            L.append(f"  {n:<22} 出错 {e.get('errorCode') or ''} {compare.clip(e.get('errorMessage'), 160)}")
            continue
        v = compare.num(e.get("value"))
        L.append(f"  {n:<22} {str(e.get('label') or '—'):<12} {'—' if v is None else f'{v:.2f}'}")
        if e.get("explanation"):
            L += _wrap(compare.clip(e.get("explanation"), 600), "      ")
    fb = {sess.get("feedback_id"): compare.feedback_index().get(sess.get("feedback_id"), {})} if golden else {}
    ev = compare.EvalRun({"sessions": [sess]}, "try-one.json")
    it = compare.pillar_items(ev, fb, compare.label_specs(ev))[0]
    red = compare.pillars.red_line_kinds(it)
    if red:
        L += [f"  ⛔ 红线（一票否决）：{compare.pillars.KIND_CN[k['kind']]}：{k['detail']}" for k in red]
    elif golden:
        L.append("  红线：无")
    if not golden:
        L.append("  （这条不在黄金集里：标签评估器没有标准答案，会返回 Skipped）")
    return L


def evaluators_of(no_builtin: bool) -> list:
    from . import evaluate
    st = aws.load_state("evaluators") or {}
    if not st:
        raise SystemExit("❌ 还没有注册评估器：先运行 ./40-create-evaluators.sh（第 4.2 步），或去掉 --eval")
    evs = [(k, v["evaluatorId"]) for k, v in st.items() if not k.startswith("_")]
    return evs if no_builtin else evs + [(b, b) for b in evaluate.BUILTINS]


def run_eval(rec: dict, golden: dict | None, rt: dict, evaluators: list, started: float) -> dict:
    from . import evaluate
    gold = {rec["feedback_id"]: golden} if golden else {}
    return evaluate.evaluate_session(rec, gold, evaluators, rt.get("logGroup"), int(started) - 600)


def main(argv=None) -> int:
    a = parse_args(argv)
    rt = aws.load_state("runtime") or {}
    if not rt.get("agentRuntimeArn"):
        raise SystemExit("❌ 找不到 Runtime（.lab-state/runtime.json）。请先运行 ./30-deploy-runtime.sh（第 3 章）")
    if a.text:
        row, golden = free_text_row(a.text), None
    else:
        gold_rows = items.load("golden")
        try:
            pool = items.load("feedback")
        except FileNotFoundError:
            pool = []
        row, golden = find_item(a.item, gold_rows, pool)
    evaluators = evaluators_of(a.no_builtin) if a.eval else []
    c = aws.cfg()
    version, model = rt.get("agentVersion") or "?", rt.get("modelId") or c.get("AGENT_MODEL_ID")
    fid = items.fid_of(row)
    sid = batch._session_id(f"{c['LAB_NAME']}-try", version, fid or "x")
    print(f"▶ 试一条：{fid} · AgentCore Runtime · 版本 {version} · 模型 {model}")
    t0 = time.time()
    try:
        res = batch._runtime_one(row, rt["agentRuntimeArn"], sid)
    except Exception as e:  # noqa: BLE001 - one item: report and stop
        print(f"❌ 调用失败：{type(e).__name__}: {_short(e)}")
        return 1
    secs = time.time() - t0
    for line in output_lines(row, golden, res.get("result"), res.get("raw"), a.raw):
        print(line)
    print(f"✅ 用时 {secs:.1f}s · session = {sid}")
    if not a.eval:
        print("   想看评估器怎么打分：加 --eval 再试一次（会再调用一次 Agent）")
        return 0
    print(f"▶ 评估这个 session：{len(evaluators)} 个评估器（trace 写入 CloudWatch 要 1–3 分钟，脚本会等）")
    rec = {"feedback_id": fid, "session_id": sid, "result": res.get("result"), "latency_ms": int(secs * 1000)}
    sess = run_eval(rec, golden, rt, evaluators, t0)
    for line in score_lines(sess, golden):
        print(line)
    return 0


def _short(e) -> str:
    s = str(e)
    return s if len(s) <= 300 else s[:299] + "…"


if __name__ == "__main__":
    sys.exit(main())
