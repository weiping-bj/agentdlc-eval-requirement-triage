#!/usr/bin/env python3
"""本地批量打分：三项标签准确率 vs 70% 目标，并输出“自动拒绝列表 + 重要/紧急分布”。

    # 直接用 Agent 的输出（JSONL，每行一个输出对象，或 {"result": {...}} / {"session_id":..,"result":..}）
    python3 evaluators/s12_label_accuracy/batch_score.py --results results/v0.jsonl --report-md results/v0-score.md
    # 用从 CloudWatch 导出的 spans（JSON 数组 / JSONL / {"sessionSpans": [...]}），按 trace 拆分
    python3 evaluators/s12_label_accuracy/batch_score.py --spans results/v0-spans.json
    # 只看分布（不打分，例如跑完 60 条全量输入后）
    python3 evaluators/s12_label_accuracy/batch_score.py --results results/all.jsonl --distribution-only

目标线：--target > config/lab.env 的 TARGET_ACCURACY > 0.70；三项各自 >= 目标才算达标。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import label_scoring as ls  # noqa: E402
import span_adapter as sa  # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))


def _cfg_target() -> float:
    vals = {}
    for name in ("lab.env", "local.env"):
        p = os.path.join(REPO, "config", name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        vals[k.strip()] = v.strip()
    try:
        return float(os.environ.get("TARGET_ACCURACY") or vals.get("TARGET_ACCURACY") or 0.70)
    except ValueError:
        return 0.70


def _read_any_json(path: str):
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    try:
        return json.loads(text)
    except ValueError:
        return [json.loads(l) for l in text.splitlines() if l.strip()]


def preds_from_results(path: str) -> list:
    data = _read_any_json(path)
    rows = data if isinstance(data, list) else (data.get("results") if isinstance(data, dict) and isinstance(data.get("results"), list) else [data])
    preds = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        obj = r.get("result") if isinstance(r.get("result"), dict) else r
        objs = sa.triage_objects(obj)
        for k in ("agent_output", "output", "response", "raw"):  # e.g. telemetry/LOG_FORMAT.md lines
            if objs:
                break
            v = r.get(k)
            objs = sa.triage_objects(v) if isinstance(v, (dict, list)) else (sa.triage_from_text(v) if isinstance(v, str) else [])
        fid_hint = r.get("feedback_id") or (obj.get("feedback_id") if isinstance(obj, dict) else None)
        if not objs:
            preds.append({"feedback_id": fid_hint, "_unparsed": True})
            continue
        for o in objs:
            n = sa.normalize_output(o)
            n["feedback_id"] = n.get("feedback_id") or fid_hint
            preds.append(n)
    return preds


def preds_from_spans(path: str) -> list:
    data = _read_any_json(path)
    if isinstance(data, dict):
        data = data.get("sessionSpans") or data.get("spans") or data.get("results") or []
    preds = []
    for tid, spans in sa.group_by_trace(data).items():
        ex = sa.extract_records(spans, None)
        for rec in ex["records"]:
            preds.append(rec["output"])
        if not ex["records"] and ex["has_assistant_text"]:
            preds.append({"feedback_id": ex.get("feedback_id_hint"), "_unparsed": True, "_trace": tid})
    return preds


def score(preds: list, golden: dict, target: float, scheme: str, tol: int) -> dict:
    latest = {}
    unparsed = []
    for p in preds:
        if p.get("_unparsed"):
            if p.get("feedback_id"):
                unparsed.append(p["feedback_id"])
            continue
        if p.get("feedback_id"):
            latest[p["feedback_id"]] = p
    scored, missing = [], []
    for fid, gold in golden.items():
        p = latest.get(fid)
        if p is None:
            missing.append(fid)
            continue
        scored.append(ls.score_record(p, gold, scheme, tol))
    agg = ls.aggregate(scored, target, scheme, missing)
    agg["unparsed_outputs"] = unparsed
    agg["not_in_golden"] = sorted(set(latest) - set(golden))
    agg["failures"] = [{"feedback_id": s["feedback_id"], "golden_id": s["golden_id"], "label": s["label"],
                        "dimensions": golden[s["feedback_id"]].get("dimensions"), "case_type": golden[s["feedback_id"]].get("case_type"),
                        "fields": {f: v for f, v in s["fields"].items() if not v["correct"]}}
                       for s in scored if s["label"] != "Pass"]
    by_dim = {}
    for s in scored:
        for d in golden[s["feedback_id"]].get("dimensions") or []:
            t = by_dim.setdefault(d, [0, 0])
            t[0] += s["n_correct"]
            t[1] += s["n_fields"]
    agg["accuracy_by_dimension"] = {d: round(a / b, 4) for d, (a, b) in by_dim.items() if b}
    agg["distribution"] = ls.distribution(latest.values())
    return agg


def _pct(x) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def report_md(res: dict, title: str) -> str:
    L = [f"# {title}", ""]
    if "accuracy" in res:
        verdict = "✅ 达标" if res["passed"] else "❌ 未达标"
        L += [f"**结论：{verdict}**（目标：三项各自 ≥ {_pct(res['target'])}；黄金集 {res['n_items']} 条，"
              f"有输出 {res['n_scored']} 条，无输出 {res['n_missing_output']} 条）", "",
              "| 标签 | 准确率 | 目标 | 是否达标 |", "|---|---|---|---|"]
        for f, v in res["accuracy"].items():
            L.append(f"| {ls.FIELD_CN.get(f, f)} | {_pct(v)} | {_pct(res['target'])} | {'是' if v >= res['target'] else '否'} |")
        L += ["", f"- 三项平均：{_pct(res['overall_accuracy'])}；三项全对的比例：{_pct(res['all_three_correct_rate'])}",
              f"- 严重错误（误拒不可自动拒绝的需求）：{len(res['critical_misrejects'])} 条 {', '.join(res['critical_misrejects'])}"]
        rec = res.get("per_class_recall") or {}
        if rec:
            L += ["", "**各类别召回率**（例如“紧急=是”的召回率低，说明 Agent 漏判紧急）", ""]
            for f, d in rec.items():
                L.append(f"- {ls.FIELD_CN.get(f, f)}：" + "，".join(f"{k} {_pct(v)}" for k, v in sorted(d.items())))
        if res.get("accuracy_by_dimension"):
            L += ["", "**按五维统计的标签准确率**：" + "，".join(f"{k} {_pct(v)}" for k, v in sorted(res["accuracy_by_dimension"].items()))]
        if res.get("failures"):
            L += ["", "## 判错的需求（用于 7.4 归因）", "", "| 黄金集 | 需求编号 | 类型 | 五维 | 判错项（期望 → 实际） |", "|---|---|---|---|---|"]
            for x in res["failures"]:
                errs = "；".join(f"{ls.FIELD_CN.get(f, f)} {'/'.join(v.get('acceptable') or [str(v.get('expected'))])} → {v.get('predicted')}"
                                for f, v in x["fields"].items()) or "-"
                tag = "（严重）" if x["label"] == "CriticalFail" else ""
                L.append(f"| {x['golden_id']}{tag} | {x['feedback_id']} | {x.get('case_type') or ''} | {'/'.join(x.get('dimensions') or [])} | {errs} |")
        if res.get("unparsed_outputs"):
            L += ["", f"输出无法解析为 JSON：{', '.join(res['unparsed_outputs'])}"]
    dist = res.get("distribution") or {}
    if dist:
        L += ["", "## Agent 输出分布（场景交付物：自动拒绝 + 重要/紧急分布）", "",
              f"- 共 {dist['n']} 条；处理建议：" + "，".join(f"{k} {v}" for k, v in sorted(dist["decision"].items())),
              "- 重要/紧急四象限：" + "，".join(f"{k} {v}" for k, v in sorted(dist["quadrant"].items()))]
        if dist.get("rejected"):
            L += ["", "| 自动拒绝的需求 | 拒绝理由 |", "|---|---|"]
            for r in dist["rejected"][:60]:
                L.append(f"| {r['feedback_id']} | {(r.get('reject_reason') or '').replace('|', '｜')[:120]} |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--results", help="Agent 输出 JSONL/JSON")
    src.add_argument("--spans", help="导出的 span 文档 JSON/JSONL")
    ap.add_argument("--golden", default=os.path.join(REPO, "data", "golden", "golden_set.jsonl"))
    ap.add_argument("--target", type=float, default=None)
    ap.add_argument("--scheme", choices=["binary", "level"], default=os.environ.get("S12_LABEL_SCHEME", "binary"))
    ap.add_argument("--level-tolerance", type=int, default=int(os.environ.get("S12_LEVEL_TOLERANCE", "0")))
    ap.add_argument("--distribution-only", action="store_true")
    ap.add_argument("--out-json")
    ap.add_argument("--report-md")
    ap.add_argument("--title", default="场景 12 · 标签准确率评估报告")
    ap.add_argument("--fail-under", action="store_true", help="未达标时退出码 1")
    a = ap.parse_args(argv)
    preds = preds_from_results(a.results) if a.results else preds_from_spans(a.spans)
    target = a.target if a.target is not None else _cfg_target()
    if a.distribution_only:
        res = {"distribution": ls.distribution([p for p in preds if not p.get("_unparsed")])}
    else:
        res = score(preds, ls.load_golden(a.golden), target, a.scheme, a.level_tolerance)
    md = report_md(res, a.title)
    print(md)
    if a.out_json:
        os.makedirs(os.path.dirname(os.path.abspath(a.out_json)), exist_ok=True)
        with open(a.out_json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    if a.report_md:
        os.makedirs(os.path.dirname(os.path.abspath(a.report_md)), exist_ok=True)
        with open(a.report_md, "w", encoding="utf-8") as f:
            f.write(md)
    return 1 if (a.fail_under and not res.get("passed", True)) else 0


if __name__ == "__main__":
    sys.exit(main())
