"""Scenario 12 business output: static HTML triage report of one batch run over last month's feedback.

  python -m labkit.report                                    # latest run with source=feedback in .lab-state/runs.json
  python -m labkit.report --run results/run-B-v1-....jsonl   # a run JSONL (or a run_id registered in runs.json)
  python -m labkit.report --run <...> --feedback data/feedback_last_month.jsonl --out page.html

No golden set needed: the page shows what the agent decided for each feedback item (counts of
迭代/拒绝/待评估, a 重要×紧急 matrix, distribution by module, the auto-rejection list with the
internal reason and the customer reply, the 待评估 list and failed items). Feedback details
(title, customer, module, tier) are joined by id from data/feedback_last_month.jsonl when present.
Self-contained page; prints the output path on stdout. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from labkit.compare import (ALIASES, as_dict, clip, esc, feedback_index, first_present, fmt_time,
                            load_state_list, num, page, parse_obj, path_of, pill, root, safe_name, shown_path, text,
                            topbar, write_page)

# decision -> (filter code, pill class, chart color); order = display order (validated categorical trio)
DECISIONS = {"迭代": ("iter", "b", "#2f6fdc"), "待评估": ("eval", "o", "#d9822b"), "拒绝": ("rej", "p", "#7b5bd6")}
QUADS = (("y", "y", "重要且紧急"), ("y", "n", "重要不紧急"), ("n", "y", "紧急不重要"), ("n", "n", "不重要不紧急"))
FOOTER = "数据为合成数据；分诊结果由 Agent 自动生成，拒绝话术对外发送前请产品经理复核"

EXTRA_CSS = """
button.kpi > span, button.cell > span { display: block; }
table.quad { table-layout: fixed; }
table.quad th, table.quad td { border: 0; padding: 4px; }
table.quad th { background: none; text-align: center; }
table.quad tbody th { text-align: right; width: 84px; }
button.cell { width: 100%; min-height: 96px; border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px;
  text-align: left; font: inherit; color: inherit; cursor: pointer; }
button.cell:hover { border-color: var(--blue); }
.cell .qn { font-size: 12.5px; color: #46525e; font-weight: 600; }
.cell .cnt { font-size: 26px; font-weight: 700; line-height: 1.3; }
.cell .qd { font-size: 11.5px; color: var(--muted); }
.track { height: 10px; }
.seg { display: flex; gap: 2px; height: 10px; }
.seg span { display: block; height: 100%; border-radius: 2px; min-width: 2px; }
.linkbtn { border: 0; background: none; padding: 0; color: var(--blue); font: inherit; cursor: pointer; text-align: left; }
.linkbtn:hover { text-decoration: underline; }
td.reply { background: #fbfaff; }
.card > .runbox { max-width: 760px; }
"""


def yn(v):
    if isinstance(v, bool):
        return "是" if v else "否"
    s = text(v).strip()
    low = s.lower()
    if low in ("true", "yes", "y"):
        return "是"
    if low in ("false", "no", "n"):
        return "否"
    return s or None


def load_records(path: Path) -> list:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise SystemExit(f"❌ 无法读取运行结果 {path}：{e}")
    recs = []
    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            recs.append({"feedback_id": f"（第 {i} 行）", "error": "该行不是合法 JSON"})
            continue
        recs.append(r if isinstance(r, dict) else {"feedback_id": f"（第 {i} 行）", "error": "该行不是 JSON 对象"})
    return recs


def normalize(rec: dict, fb: dict) -> dict:
    res = rec.get("result")
    out = parse_obj(res)
    fid = text(rec.get("feedback_id") or out.get("feedback_id") or out.get("id") or out.get("需求编号")).strip() or "（无编号）"
    imp, urg = yn(first_present(out, ALIASES["important"])), yn(first_present(out, ALIASES["urgent"]))
    dec = text(first_present(out, ALIASES["decision"])).strip() or None
    if rec.get("error"):
        err = text(rec.get("error"))
    elif res is None:
        err = "Agent 没有返回结果"
    elif not out:
        err = "结果不是 JSON 对象"
    elif out.get("_parse_error"):
        err = "输出解析失败：" + text(out.get("_parse_error"))
    elif dec not in DECISIONS:
        err = f"处理建议取值无效：{dec or '（空）'}"
    else:
        err = None
    return {"fid": fid, "out": out, "imp": imp, "urg": urg, "dec": dec, "err": err, "raw": rec.get("raw"),
            "detail": as_dict(fb.get(fid)), "latency": num(rec.get("latency_ms")), "rec": rec}


def _code(v) -> str:
    return {"是": "y", "否": "n"}.get(v or "", "u")


def _yn_pill(label: str, v) -> str:
    if v == "是":
        return pill(f"{label}", "r" if label == "紧急" else "b")
    if v == "否":
        return pill(f"不{label}", "n")
    return pill(f"{label}？{v or '—'}", "n")


def _dec_pill(it: dict) -> str:
    if it["err"]:
        return pill("失败", "r", it["err"])
    _c, cls, _color = DECISIONS[it["dec"]]
    lvl = text(it["out"].get("level")).strip()
    return pill(it["dec"], cls) + (f' <span class="small muted">{esc(lvl)}</span>' if lvl and lvl.lower() != "none" else "")


def _who(it: dict) -> str:
    d = it["detail"]
    title = f'<div>{esc(d.get("title"))}</div>' if d.get("title") else ""
    sub = " · ".join(text(x) for x in (d.get("customer"), d.get("customer_tier"), d.get("module")) if x)
    return f'<span class="mono">{esc(it["fid"])}</span>{title}' + (f'<div class="small muted">{esc(sub)}</div>' if sub else "")


def _must_not_reject(it: dict):
    """Why a rejected item needs human review (defects / contract commitments must not be auto-rejected)."""
    d = it["detail"]
    if text(d.get("feedback_type")) == "功能缺陷":
        return "功能缺陷"
    if text(d.get("contract_commitment")) == "是":
        return "合同承诺"
    return None


def _review_flags(it: dict) -> str:
    out = it["out"]
    flags = []
    if not text(out.get("reject_reason")).strip():
        flags.append(pill("缺少拒绝理由", "r"))
    if not text(out.get("customer_reply")).strip():
        flags.append(pill("缺少客户话术", "r"))
    why = _must_not_reject(it)
    if why:
        flags.append(pill(f"⚠ 需人工复核：{why}类不应自动拒绝", "o"))
    return ("<div>" + " ".join(flags) + "</div>") if flags else ""


def pick_run(arg) -> tuple:
    runs = load_state_list("runs")
    if arg:
        p = Path(arg).expanduser()
        for c in (p, root() / p):
            if c.exists():
                c = c.resolve()
                entry = next((r for r in reversed(runs) if r.get("file") and (root() / str(r["file"])).resolve() == c), {})
                return c, entry
        for r in reversed(runs):
            if str(r.get("run_id")) == arg and r.get("file"):
                return root() / str(r["file"]), r
        raise SystemExit(f"❌ 找不到运行结果文件或 run_id：{arg}")
    fb_runs = [r for r in runs if str(r.get("source")) == "feedback" and r.get("file")]
    if not fb_runs:
        raise SystemExit("❌ .lab-state/runs.json 里没有 source=feedback 的运行：先运行 ./34-triage-report.sh"
                         "（或 python -m labkit.batch --source feedback），或用 --run 指定运行结果 JSONL")
    return root() / str(fb_runs[-1]["file"]), fb_runs[-1]


def build_report(recs: list, entry: dict, fb: dict, run_file: Path) -> str:
    its = [normalize(r, fb) for r in recs]
    ok = [i for i in its if not i["err"]]
    errs = [i for i in its if i["err"]]
    total = len(its)
    first = next((r for r in recs if r.get("run_id")), recs[0] if recs else {})
    run_id = text(entry.get("run_id") or first.get("run_id") or Path(run_file).stem.removeprefix("run-"))
    version = text(entry.get("version") or first.get("version")).strip()
    vlabel = version.upper() if version else "?"
    model = text(entry.get("model") or first.get("model")) or "—"
    letter, plabel = path_of(entry.get("mode") or first.get("mode"))
    starts = sorted(text(r.get("started_at")) for r in recs if r.get("started_at"))
    when = fmt_time(starts[0]) if starts else fmt_time(entry.get("created"))
    lats = [i["latency"] for i in its if i["latency"] is not None]
    joined = sum(1 for i in its if i["detail"])

    by_dec = {d: [i for i in ok if i["dec"] == d] for d in DECISIONS}
    n_review = sum(1 for i in by_dec["拒绝"] if _must_not_reject(i))

    # ---- summary + KPI tiles (click = filter the full list) ----
    sentence = (f"本次共分诊 {total} 条需求反馈：建议迭代 {len(by_dec['迭代'])} 条、自动拒绝 {len(by_dec['拒绝'])} 条"
                f"（已附客户沟通话术）、待人工评估 {len(by_dec['待评估'])} 条")
    sentence += f"；{len(errs)} 条处理失败，需要重跑。" if errs else "。"
    tiles = []

    def tile(f, label, n, sub):
        share = f"{n / total * 100:.0f}%" if total else "—"
        active = " active" if f == "all" else ""
        tiles.append(f'<button type="button" class="kpi{active}" data-target="#all" data-f="{esc(f)}" data-label="{esc(label)}" '
                     f'data-scroll><span class="k">{esc(label)}</span><span class="v">{n}</span>'
                     f'<span class="d">占 {esc(share)} · {esc(sub)}</span></button>')

    tile("all", "全部需求", total, "点击卡片筛选下方清单")
    tile("dec-iter", "迭代", len(by_dec["迭代"]), "进入产品迭代排期")
    tile("dec-rej", "拒绝", len(by_dec["拒绝"]), "自动拒绝并附话术")
    tile("dec-eval", "待评估", len(by_dec["待评估"]), "需产品/技术进一步评估")
    tile("err", "处理失败", len(errs), "需要重跑")
    notes = f'<div class="note">{esc(sentence)}</div>'
    if n_review:
        notes += (f'<div class="warn">⚠️ 有 {n_review} 条被判“拒绝”的需求属于功能缺陷或合同承诺类（按规则不可自动拒绝），'
                  '请在“自动拒绝清单”中人工复核。</div>')
    if fb and joined < total:
        notes += f'<div class="note">有 {total - joined} 条需求在需求明细文件中找不到，只显示编号。</div>'
    if not fb:
        notes += '<div class="note">未找到需求明细文件（data/feedback_last_month.jsonl），清单只显示需求编号。</div>'
    head = (f'<div class="card"><h2>{esc(vlabel)} 分诊结果总览</h2>{notes}<div class="kpis">{"".join(tiles)}</div></div>')

    info_rows = [("run_id", f'<span class="mono">{esc(run_id)}</span>'), ("路线", esc(plabel)),
                 ("版本", esc(vlabel)), ("模型", f'<span class="mono">{esc(model)}</span>'), ("运行时间", esc(when)),
                 ("条数", esc(f"{total} 条（成功 {len(ok)}，失败 {len(errs)}）")),
                 ("平均耗时", esc(f"{sum(lats) / len(lats) / 1000:.1f} 秒/条" if lats else "—")),
                 ("需求明细", esc(f"已关联 {joined}/{total} 条")),
                 ("结果文件", f'<span class="mono">{esc(shown_path(run_file))}</span>')]
    info = ('<div class="card"><h2>本次运行</h2><div class="runbox"><table>'
            + "".join(f"<tr><td>{esc(k)}</td><td>{v}</td></tr>" for k, v in info_rows) + "</table></div></div>")

    # ---- 重要 × 紧急 matrix ----
    qcount = {(i_, u_): [i for i in ok if _code(i["imp"]) == i_ and _code(i["urg"]) == u_] for i_, u_, _ in QUADS}
    qmax = max([len(v) for v in qcount.values()] + [1])

    def qcell(i_, u_, name):
        lst = qcount[(i_, u_)]
        dist = " · ".join(f"{d} {sum(1 for x in lst if x['dec'] == d)}" for d in DECISIONS)
        alpha = 0.04 + 0.22 * len(lst) / qmax
        return (f'<td><button type="button" class="cell" style="background:rgba(47,111,220,{alpha:.2f})" data-target="#all" '
                f'data-f="q-{i_}{u_}" data-label="{esc(name)}" data-scroll><span class="qn">{esc(name)}</span>'
                f'<span class="cnt">{len(lst)}</span><span class="qd">{esc(dist)}</span></button></td>')

    other = sum(1 for i in ok if "u" in (_code(i["imp"]), _code(i["urg"])))
    matrix = ('<div class="card"><h2>重要 × 紧急 矩阵</h2>'
              '<p class="small muted">点击格子筛选下方“全部需求清单”。格子里是条数及其中迭代 / 待评估 / 拒绝的分布。</p>'
              '<table class="quad"><thead><tr><th></th><th>紧急：是</th><th>紧急：否</th></tr></thead><tbody>'
              f'<tr><th>重要：是</th>{qcell("y", "y", "重要且紧急")}{qcell("y", "n", "重要不紧急")}</tr>'
              f'<tr><th>重要：否</th>{qcell("n", "y", "紧急不重要")}{qcell("n", "n", "不重要不紧急")}</tr>'
              '</tbody></table>'
              + (f'<p class="small muted">另有 {other} 条“是否重要 / 是否紧急”取值无效，未计入矩阵。</p>' if other else "")
              + "</div>")

    # ---- distribution by module ----
    mods: dict = {}
    for i in ok:
        mods.setdefault(text(i["detail"].get("module")) or "未知模块", []).append(i)
    mod_order = sorted(mods, key=lambda m: (-len(mods[m]), m))
    mod_idx = {m: k for k, m in enumerate(mod_order)}
    mmax = max([len(v) for v in mods.values()] + [1])
    legend = "".join(f'<span><span class="sw" style="background:{c}"></span>{esc(d)}</span>' for d, (_c, _p, c) in DECISIONS.items())
    mrows = []
    for m in mod_order:
        lst = mods[m]
        cnt = {d: sum(1 for x in lst if x["dec"] == d) for d in DECISIONS}
        segs = "".join(f'<span style="flex:{cnt[d]} 1 0;background:{c}" title="{esc(f"{m} · {d} {cnt[d]} 条")}"></span>'
                       for d, (_c, _p, c) in DECISIONS.items() if cnt[d])
        width = len(lst) / mmax * 100
        mrows.append(f'<tr><td><button type="button" class="linkbtn" data-target="#all" data-f="mod-{mod_idx[m]}" '
                     f'data-label="{esc("模块：" + m)}" data-scroll>{esc(m)}</button></td>'
                     f'<td style="width:42%"><div class="track" style="width:{width:.1f}%"><div class="seg">{segs}</div></div></td>'
                     + "".join(f'<td class="num">{cnt[d]}</td>' for d in DECISIONS) + f'<td class="num"><b>{len(lst)}</b></td></tr>')
    modules = ('<div class="card"><h2>按模块分布</h2>'
               f'<div class="legend">{legend}</div>'
               '<table><thead><tr><th>模块</th><th>分布</th>' + "".join(f"<th>{esc(d)}</th>" for d in DECISIONS)
               + '<th>合计</th></tr></thead><tbody>' + "".join(mrows) + "</tbody></table>"
               + ('<p class="small muted">点击模块名筛选下方清单。</p>' if mrows else '<p class="muted">没有成功分诊的条目。</p>')
               + "</div>")

    # ---- full list (filter target) ----
    arows = []
    for i in its:
        tags = ["err"] if i["err"] else [f"dec-{DECISIONS[i['dec']][0]}", f"q-{_code(i['imp'])}{_code(i['urg'])}",
                                         f"mod-{mod_idx.get(text(i['detail'].get('module')) or '未知模块', 'x')}"]
        reason = i["err"] if i["err"] else text(i["out"].get("reasoning"))
        arows.append(f'<tr data-tags="{esc(" ".join(tags))}"><td>{_who(i)}</td>'
                     f'<td>{"" if i["err"] else _yn_pill("重要", i["imp"]) + _yn_pill("紧急", i["urg"])}</td>'
                     f'<td>{_dec_pill(i)}</td><td class="small" title="{esc(clip(reason, 600))}">{esc(clip(reason, 90))}</td></tr>')
    full = ('<div class="card"><h2>全部需求清单</h2><div class="filters">'
            '<button type="button" class="fbtn active" data-target="#all" data-f="all" data-label="全部">清除筛选</button>'
            f'<span class="small muted" data-status-for="#all">全部：{total} 条</span></div>'
            '<table id="all"><thead><tr><th style="width:30%">需求</th><th>重要 / 紧急</th><th>处理建议</th><th>理由（悬停看全文）</th>'
            '</tr></thead><tbody>' + "".join(arows) + "</tbody></table></div>")

    # ---- auto-rejection list (full text) ----
    rrows = []
    for i in by_dec["拒绝"]:
        out = i["out"]
        rrows.append(f'<tr><td>{_who(i)}{_review_flags(i)}</td>'
                     f'<td class="prewrap">{esc(text(out.get("reject_reason")).strip() or "—")}</td>'
                     f'<td class="prewrap reply">{esc(text(out.get("customer_reply")).strip() or "—")}</td></tr>')
    rejects = ('<div class="card"><h2>自动拒绝清单（附客户沟通话术）</h2>'
               '<p class="small muted">“拒绝理由”是内部专业理由；“客户沟通话术”可复制给客户成功经理使用，发送前请人工复核。</p>'
               + ('<table id="rej"><thead><tr><th style="width:26%">需求</th><th style="width:30%">拒绝理由（内部）</th>'
                  '<th>客户沟通话术</th></tr></thead><tbody>' + "".join(rrows) + "</tbody></table>"
                  if rrows else '<p class="muted">本次没有被判为“拒绝”的需求。</p>') + "</div>")

    # ---- 待评估 list ----
    erows = []
    for i in by_dec["待评估"]:
        lvl = text(i["out"].get("level")).strip()
        erows.append(f'<tr><td>{_who(i)}</td><td>{_yn_pill("重要", i["imp"])}{_yn_pill("紧急", i["urg"])}'
                     f'{"<div class=small>优先级 " + esc(lvl) + "</div>" if lvl and lvl.lower() != "none" else ""}</td>'
                     f'<td class="prewrap small">{esc(text(i["out"].get("reasoning")).strip() or "—")}</td></tr>')
    evals = ('<div class="card"><h2>待评估清单</h2>'
             '<p class="small muted">Agent 认为需要产品 / 技术进一步评估（价值高但难度大、信息不足或一条反馈含多个诉求等）。</p>'
             + ('<table id="pending"><thead><tr><th style="width:30%">需求</th><th>重要 / 紧急</th><th>Agent 理由</th></tr></thead><tbody>'
                + "".join(erows) + "</tbody></table>" if erows else '<p class="muted">本次没有“待评估”的需求。</p>') + "</div>")

    # ---- errors ----
    xrows = []
    for i in errs:
        raw = i["raw"] if i["raw"] not in (None, "") else i["rec"].get("result")
        lat = f"{i['latency'] / 1000:.1f}s" if i["latency"] is not None else "—"
        xrows.append(f'<tr><td>{_who(i)}</td><td class="prewrap small">{esc(i["err"])}</td>'
                     f'<td class="prewrap small mono">{esc(clip(raw, 300)) or "—"}</td><td class="num">{esc(lat)}</td></tr>')
    errors = ('<div class="card"><h2>处理失败（需要重跑）</h2>'
              + ('<table id="errors"><thead><tr><th style="width:26%">需求</th><th>错误</th><th>原始输出（前 300 字）</th>'
                 '<th>耗时</th></tr></thead><tbody>' + "".join(xrows) + "</tbody></table>"
                 if xrows else '<p class="muted">没有失败的条目。</p>') + "</div>")

    bar = topbar(f"上月需求反馈分诊报告 · {vlabel}", "场景 12 · 需求反馈分诊 Agent · 业务输出（不需要黄金集）",
                 [("run_id", run_id), ("模型", model), ("路线", f"{letter} 版")])
    body = (head + info + f'<div class="grid2">{matrix}{modules}</div>' + full + rejects + evals + errors)
    return page(f"需求分诊报告 {vlabel} · {run_id}", bar, body, FOOTER, EXTRA_CSS)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m labkit.report", description="生成上月需求反馈分诊报告（自包含静态 HTML）")
    ap.add_argument("--run", help="运行结果 JSONL 路径或 run_id（默认：runs.json 中最新的 source=feedback 运行）")
    ap.add_argument("--feedback", help="需求明细 JSONL（默认：data/feedback_last_month.jsonl）")
    ap.add_argument("--out", help="输出 HTML（默认：results/triage-report-<run_id>.html）")
    a = ap.parse_args(argv)
    run_file, entry = pick_run(a.run)
    if not Path(run_file).exists():
        raise SystemExit(f"❌ 找不到运行结果文件：{run_file}")
    recs = load_records(run_file)
    if a.feedback and not (Path(a.feedback).exists() or (root() / a.feedback).exists()):
        print(f"⚠️ 找不到需求明细文件 {a.feedback}，改用默认数据文件", file=sys.stderr)
    fb = feedback_index([a.feedback] if a.feedback else [])
    first = next((r for r in recs if r.get("run_id")), {})
    run_id = text(entry.get("run_id") or first.get("run_id") or Path(run_file).stem.removeprefix("run-"))
    print(f"▶ 分诊报告：{run_id} · {len(recs)} 条", file=sys.stderr)
    out = Path(a.out) if a.out else root() / "results" / f"triage-report-{safe_name(run_id)}.html"
    path = write_page(out, build_report(recs, entry, fb, Path(run_file)))
    print("✅ 分诊报告已生成（本地文件，可直接用浏览器打开）：", file=sys.stderr)
    print(shown_path(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
