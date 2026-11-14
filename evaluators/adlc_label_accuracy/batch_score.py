#!/usr/bin/env python3
"""本地批量打分：按三大支柱（红线 / 门禁 / 观测）给出结论，并输出“自动拒绝列表 + 重要/紧急分布”。

    # 直接用 Agent 的输出（JSONL，每行一个输出对象，或 {"result": {...}} / {"session_id":..,"result":..}）
    python3 evaluators/adlc_label_accuracy/batch_score.py --results results/v0.jsonl --report-md results/v0-score.md
    # 用从 CloudWatch 导出的 spans（JSON 数组 / JSONL / {"sessionSpans": [...]}），按 trace 拆分
    python3 evaluators/adlc_label_accuracy/batch_score.py --spans results/v0-spans.json
    # 带上 43-run-eval.sh 已有的话术评审结果（不重新调用评审模型），三大支柱才完整
    python3 evaluators/adlc_label_accuracy/batch_score.py --results results/run-A-v1-....jsonl --reject-eval results/eval-A-v1-....json
    # 只看分布（不打分，例如跑完 60 条全量输入后）
    python3 evaluators/adlc_label_accuracy/batch_score.py --results results/all.jsonl --distribution-only

结论 = 没有红线 且 门禁全部达标（规则见 pillars.py）：
  红线（一票否决）：误拒不可自动拒绝的需求；话术评审的 red_flags（需 --reject-eval）。有 1 条就不通过，该条整条判错。
  门禁：是否重要 / 是否紧急 / 处理建议（分级模式再加优先级）准确率各自 >= 目标，红线条目和无输出条目按错计；
        拒绝话术通过率 >= 目标。
        目标线：--target > config/lab.env 的 TARGET_ACCURACY > 0.70。没有话术评审结果时该门禁“未评估”，结论不能判通过。
  观测：五维、用例类型、召回率、话术分项、格式错误率、能力目录命中率、输出分布，只看趋势。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import label_scoring as ls  # noqa: E402
import pillars  # noqa: E402
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
        if not objs and r.get("error") and r.get("result") is None:
            preds.append({"feedback_id": fid_hint, "_agent_error": True})  # no output at all (not a format error)
            continue
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


def reject_results(path: str) -> tuple:
    """({feedback_id: reject_quality entry}, run file name) from a labkit eval JSON (results/eval-<run_id>.json)."""
    data = _read_any_json(path)
    sessions = data.get("sessions") if isinstance(data, dict) else data
    run = (data.get("run") or {}) if isinstance(data, dict) else {}
    out = {}
    for s in sessions or []:
        if isinstance(s, dict) and s.get("feedback_id") and isinstance(s.get("scores"), dict) \
                and "reject_quality" in s["scores"]:
            out[str(s["feedback_id"])] = s["scores"]["reject_quality"]
    return out, os.path.basename(str(run.get("file") or "")) if isinstance(run, dict) else ""


def _cap_hit(pred: dict, caps: list):
    if not caps:
        return None
    blob = " ".join(str(pred.get(k) or "") for k in ("reasoning", "reject_reason", "customer_reply"))
    return any(c in blob for c in caps)


def pillar_items(scored: list, missing: list, golden: dict, latest: dict, rejects: dict | None, scheme: str = "binary",
                 tol: int = 0, unparsed=()) -> list:
    """Per-item records for pillars.build (missing / unparsable outputs count as wrong in every label).

    Gate fields are pillars.gate_fields(scheme): in the graded scheme important / urgent are scored here too.
    A reject_quality entry is only used for an item this run actually rejected (a stale eval cannot charge
    a red flag to an item the agent did not reject)."""
    unparsed = set(unparsed)
    out = []
    for s in scored:
        fid = s["feedback_id"]
        gold, pred = golden[fid], latest.get(fid) or {}
        raw = {f: v.get("raw_correct", v["correct"]) for f, v in s["fields"].items()}
        if scheme == "level":
            raw.update({f: v["raw_correct"] for f, v in ls.score_record(pred, gold, "binary", tol)["fields"].items()
                        if f not in raw})
        rejected = pred.get("decision") == "拒绝"
        rej = pillars.reject_entry(rejects.get(fid)) if (rejects is not None and rejected) else None
        out.append(pillars.new_item(
            fid, golden_id=s.get("golden_id"), fields=raw, misreject=s["critical"], format_error=False, rejected=rejected,
            decision=pred.get("decision"), reject=rej, dimensions=gold.get("dimensions") or [], case_type=gold.get("case_type"),
            capabilities=gold.get("matched_capabilities") or [], cap_hit=_cap_hit(pred, gold.get("matched_capabilities") or [])))
    for fid in missing:
        gold = golden[fid]
        out.append(pillars.new_item(fid, golden_id=gold.get("golden_id"), no_output=True, format_error=fid in unparsed,
                                    fields={f: False for f in pillars.gate_fields(scheme)},
                                    dimensions=gold.get("dimensions") or [], case_type=gold.get("case_type")))
    return out


def score(preds: list, golden: dict, target: float, scheme: str, tol: int, rejects: dict | None = None) -> dict:
    latest = {}
    unparsed = []
    for p in preds:
        if p.get("_unparsed"):
            if p.get("feedback_id"):
                unparsed.append(p["feedback_id"])
            continue
        if p.get("_agent_error"):
            continue  # counted as missing output (wrong in every label)
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
                        "fields": {f: v for f, v in s["fields"].items() if not v.get("raw_correct", v["correct"])}}
                       for s in scored if s["label"] != "Pass"]
    agg["distribution"] = ls.distribution(latest.values())
    p = pillars.build(pillar_items(scored, missing, golden, latest, rejects, scheme, tol, unparsed),
                      pillars.gate_fields(scheme), target)
    agg["pillars"] = p
    # one set of numbers per report: gate accuracies (every red-line item and every missing output counts as wrong)
    gates = [g for g in p["gates"] if g["key"] != "reject_quality"]
    agg["accuracy"] = {g["key"]: g["actual"] for g in gates}
    agg["overall_accuracy"] = round(sum(v or 0 for v in agg["accuracy"].values()) / len(gates), 4) if gates else 0.0
    agg["label_gates_passed"] = all(g["status"] == "met" for g in gates)
    agg["all_three_correct_rate"] = p["observations"]["all_correct_rate"]
    agg["accuracy_by_dimension"] = p["observations"]["by_dimension"]
    agg["verdict"] = p["verdict"]
    agg["passed"] = p["passed"]
    agg["reject_quality_source"] = "eval" if rejects is not None else None
    return agg


def _pct(x) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def _cell(v, n: int = 160) -> str:
    """Markdown table cell from untrusted text: one line, no table / link / image / HTML syntax."""
    s = pillars.plain(v, n)
    for ch, rep in (("|", "｜"), ("[", "［"), ("]", "］"), ("<", "＜"), (">", "＞"), ("`", "｀"), ("!", "！")):
        s = s.replace(ch, rep)
    return s


def _gate_cell(g: dict) -> str:
    if g["actual"] is None:
        return "—"
    extra = f"（{g['ok']}/{g['n']}）" if g["key"] == "reject_quality" else ""
    return _pct(g["actual"]) + extra


def pillars_md(p: dict, reject_hint: str = "") -> list:
    """Markdown lines for the three-pillar section (red line / gate / observation)."""
    rl, obs = p["red_line"], p["observations"]
    L = [f"**结论：{pillars.verdict_line(p)}**", "",
         "> 三大支柱：**红线**一票否决（出现 1 条即不通过，该条整条判错）→ **门禁**全部达标 → **观测**只看趋势、不影响结论。", "",
         f"## 1. 红线（一票否决）：{rl['count']} 条 {pillars.red_line_mark(p)}", "",
         "| 红线 | 条数 | 要求 |", "|---|---|---|"]
    rq = next(g for g in p["gates"] if g["key"] == "reject_quality")
    for k, cn in pillars.KIND_CN.items():
        n = rl["by_kind"].get(k, 0)
        shown = str(n)
        if k == "reject_red_flag" and rq["status"] == "not_evaluated":
            shown = "未评估"
        L.append(f"| {cn} | {shown} | 0 |")
    L.append(f"| **合计（按条）** | **{rl['count']}** | **0** |")
    if not rl["complete"]:
        L += ["", f"有 {len(rq['unjudged'])} 条拒绝没有话术评审结果，话术红线可能没有统计全。{reject_hint}"]
    if rl["items"]:
        L += ["", "| 黄金集 | 需求编号 | 红线 | 说明 |", "|---|---|---|---|"]
        for x in rl["items"]:
            for k in x["kinds"]:
                L.append(f"| {_cell(x.get('golden_id') or '-')} | {_cell(x['feedback_id'])} | {pillars.KIND_CN[k['kind']]} | "
                         f"{_cell(k['detail'])} |")
    L += ["", "## 2. 门禁（全部达标才通过）", "", "| 门禁 | 实际 | 门槛 | 是否达标 |", "|---|---|---|---|"]
    for g in p["gates"]:
        st = pillars.GATE_STATUS_CN[g["status"]]
        if g["status"] == "not_evaluated" and g["key"] == "reject_quality" and reject_hint:
            st += f"（{reject_hint}）"
        L.append(f"| {g['name']} | {_gate_cell(g)} | ≥ {_pct(g['threshold'])} | {st} |")
    L += ["", f"- 红线条目（误拒或话术红线）整条判错：标签门禁里各项都按“错”计；无输出的条目也按错计。拒绝话术通过率：{rq['rule']}。"]
    L += ["", "## 3. 观测（只看趋势，不影响结论）", "",
          f"- 整条通过率（各项全对且无红线）：{_pct(obs['all_correct_rate'])}；格式错误（FormatError）：{obs['format_error']['n']} 条"
          f"（{_pct(obs['format_error']['rate'])}）",
          "- 处理建议分布：" + "，".join(f"{k} {v}" for k, v in sorted(obs["decisions"].items()))]
    if obs["by_dimension"]:
        L.append("- 按五维的标签准确率：" + "，".join(f"{k} {_pct(v)}" for k, v in obs["by_dimension"].items()))
    if obs["by_case_type"]:
        L.append("- 按用例类型的整条答对率：" + "，".join(f"{k} {_pct(v)}" for k, v in obs["by_case_type"].items()))
    cap = obs["capability_hit"]
    if cap["n"]:
        L.append(f"- 能力目录命中率（输出引用了黄金集标注的能力编号）：{_pct(cap['rate'])}（{cap['hit']}/{cap['n']}）")
    r = obs["reject"]
    if r["n"]:
        names = dict(pillars.JUDGE_DIMS)
        sub = "，".join(f"{names.get(k, k)} {v:g}" for k, v in r["scores"].items())
        L.append(f"- 拒绝话术：评审 {r['n']} 条，均分 {r['mean']:.2f}" + (f"；分项均分（1~5）：{sub}" if sub else "")
                 + (f"；未覆盖要点 {r['missing_points']} 条，例如：{_cell('；'.join(r['top_missing'][:3]), 120)}"
                    if r["missing_points"] else ""))
    return L


def report_md(res: dict, title: str) -> str:
    L = [f"# {title}", ""]
    if "accuracy" in res:
        hint = "" if res.get("reject_quality_source") else "本地打分不调用评审模型：用 --reject-eval 传入 43-run-eval.sh 的评估结果"
        L += pillars_md(res["pillars"], hint)
        L += ["", f"黄金集 {res['n_items']} 条，有输出 {res['n_scored']} 条，无输出 {res['n_missing_output']} 条（无输出按三项全错计）。"]
        L += [f"- 标签门禁平均：{_pct(res['overall_accuracy'])}；严重错误（误拒不可自动拒绝的需求）："
              f"{len(res['critical_misrejects'])} 条 {', '.join(res['critical_misrejects'])}"]
        rec = res.get("per_class_recall") or {}
        if rec:
            L += ["", "**各类别召回率**（观测；按 Agent 原始输出统计，不含红线否决。例如“紧急=是”的召回率低，说明 Agent 漏判紧急）", ""]
            for f, d in rec.items():
                L.append(f"- {ls.FIELD_CN.get(f, f)}：" + "，".join(f"{k} {_pct(v)}" for k, v in sorted(d.items())))
        if res.get("failures"):
            L += ["", "## 判错的需求（用于失败归因）", "", "| 黄金集 | 需求编号 | 类型 | 五维 | 判错项（期望 → 实际） |", "|---|---|---|---|---|"]
            for x in res["failures"]:
                errs = "；".join(f"{ls.FIELD_CN.get(f, f)} {'/'.join(v.get('acceptable') or [str(v.get('expected'))])} → {v.get('predicted')}"
                                for f, v in x["fields"].items()) or "-"
                tag = "（红线·整条判错）" if x["label"] == "CriticalFail" else ""
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
                L.append(f"| {_cell(r['feedback_id'])} | {_cell(r.get('reject_reason'), 120)} |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--results", help="Agent 输出 JSONL/JSON")
    src.add_argument("--spans", help="导出的 span 文档 JSON/JSONL")
    ap.add_argument("--golden", default=os.path.join(REPO, "data", "golden", "golden_set.jsonl"))
    ap.add_argument("--target", type=float, default=None)
    ap.add_argument("--scheme", choices=["binary", "level"], default=os.environ.get("ADLC_LABEL_SCHEME", "binary"))
    ap.add_argument("--level-tolerance", type=int, default=int(os.environ.get("ADLC_LEVEL_TOLERANCE", "0")))
    ap.add_argument("--reject-eval", help="43-run-eval.sh 的 results/eval-<run_id>.json：读取其中已有的 reject_quality 结果"
                                          "（不重新调用评审模型），补全话术红线和话术门禁")
    ap.add_argument("--distribution-only", action="store_true")
    ap.add_argument("--out-json")
    ap.add_argument("--report-md")
    ap.add_argument("--title", default="AgentDLC 动手实验 · 标签准确率评估报告")
    ap.add_argument("--fail-under", action="store_true", help="结论不是“通过”（有红线、门禁未达标或未评估）时退出码 1")
    a = ap.parse_args(argv)
    preds = preds_from_results(a.results) if a.results else preds_from_spans(a.spans)
    target = a.target if a.target is not None else _cfg_target()
    if a.distribution_only:
        res = {"distribution": ls.distribution([p for p in preds if not p.get("_unparsed")])}
    else:
        rejects, ran = reject_results(a.reject_eval) if a.reject_eval else (None, "")
        if ran and a.results and ran != os.path.basename(a.results):
            print(f"⚠️ --reject-eval 来自另一次运行（{ran}），与 --results {os.path.basename(a.results)} 不一致："
                  "话术评审结果只用于本次运行判为“拒绝”的条目，结论仅供参考", file=sys.stderr)
        res = score(preds, ls.load_golden(a.golden), target, a.scheme, a.level_tolerance, rejects)
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
