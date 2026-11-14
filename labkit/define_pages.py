"""Define ring previews: the fishbone (adlc-fishbone skill) and the criteria table (adlc-criteria skill) as
self-contained pages, published with the .drawio file to the participant's private lab bucket.

  ./tools/publish-define-pages.sh outputs/fishbone/<group>-<date>              # render, upload, print console links
  ./tools/publish-define-pages.sh outputs/fishbone/<group>-<date> --no-upload  # only write the pages (no AWS call)

Why: attendees reach the EC2 only through the SSM browser console (no port forwarding, no file download), so
the files the skills write under outputs/ cannot be opened there. This module re-runs the skill's own drawio
generator on fishbone-data.json (so the uploaded .drawio always matches the JSON and passed every check), renders
fishbone.html from the same JSON and, when criteria.json exists, criteria.html; each page is one file (inline
CSS, no script, no image, font or link, a CSP that forbids any network load; every value from the JSON is
HTML-escaped) and is checked with labkit.data_pages.external_refs before it is written. The upload goes through
labkit.publish (fail closed: nothing is uploaded unless the bucket is confirmed private and owned by this account)
to reports/define/<session>/ ; the S3 console link opens the page (Object -> Open), the .drawio is downloaded from
the console and opened in app.diagrams.net. Same keys every run, so a re-run overwrites the same objects.

The render path is standard library only (runs with the system python3); boto3 is imported only to upload.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from labkit import config as labcfg
from labkit.compare import load_css, topbar
from labkit.data_pages import BEIJING, CSP, E, _CSP_META, external_refs, shown as _shown, source_info

ROOT = Path(labcfg.ROOT)
GENERATOR = ROOT / ".claude" / "skills" / "adlc-fishbone" / "scripts" / "generate_fishbone.py"
DRAWIO_TYPE = "application/vnd.jgraph.mxfile"
# key, Chinese name, template subtitle, the template's question
DIMS = [("cognition", "认知", "业务知识与上下文", "它真的理解你的业务吗？"),
        ("quality", "质量", "正确、完整、一致、可引用", "结果是否可靠且可验证？"),
        ("responsibility", "责任", "权限、行为边界、合规、审计", "风险边界和责任是否清楚？"),
        ("cost", "成本", "单次任务与规模化成本", "规模化之后是否经济？"),
        ("performance", "性能", "延迟、吞吐、稳定性、可用性", "生产 SLA 是否达标？"),
        ("other", "其他 / 待归类", "系统集成、流程、组织", "还有什么会让它跑不下去？")]
COVERAGE_CN = {"confirmed": ("有确认的障碍", "g"), "explored_empty": ("问过，确认为空", "b"),
               "unresolved": ("没问清楚，不入图", "o")}
TIER_PILL = {"红线": "r", "门禁": "o", "观测": "b"}
COVER_PILL = {"已覆盖": "g", "部分覆盖": "o", "未覆盖": "r"}
PAGE_CSS = """
.layout { max-width: 1480px; margin: 0 auto; padding: 18px 22px 40px; }
.small { font-size: 12px; }
.muted { color: var(--muted); }
.fb { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)) 190px; grid-template-rows: auto 40px auto;
      column-gap: 14px; margin-top: 6px; }
.fb .bone { position: relative; padding: 8px 10px 12px; min-height: 220px; }
.fb .bone.top { grid-row: 1; background: linear-gradient(to top right, transparent calc(50% - 1.5px), #9aa7b6 calc(50% - 1.5px),
                #9aa7b6 calc(50% + 1.5px), transparent calc(50% + 1.5px)); }
.fb .bone.bottom { grid-row: 3; background: linear-gradient(to bottom right, transparent calc(50% - 1.5px), #9aa7b6 calc(50% - 1.5px),
                   #9aa7b6 calc(50% + 1.5px), transparent calc(50% + 1.5px)); }
.fb .spine { grid-column: 1 / 4; grid-row: 2; align-self: center; height: 6px; background: var(--navy); border-radius: 3px;
             position: relative; }
.fb .spine::after { content: ""; position: absolute; right: -14px; top: -9px; border-left: 18px solid var(--navy);
                    border-top: 12px solid transparent; border-bottom: 12px solid transparent; }
.fb .head { grid-column: 4; grid-row: 1 / 4; align-self: center; background: var(--navy); color: #fff; border-radius: 12px;
            padding: 24px 14px; text-align: center; font-weight: 700; font-size: 18px; line-height: 1.5; }
.fb .dimhead { position: relative; display: inline-block; background: #fff; border: 1px solid var(--line); border-radius: 8px;
               padding: 4px 10px; line-height: 1.4; }
.fb .dimhead b { font-size: 15px; }
.fb .sticky { position: relative; background: #FFF1B8; border: 1px solid #C78C00; border-radius: 6px; padding: 7px 10px;
              margin: 8px 0; font-size: 13.5px; line-height: 1.5; max-width: 94%; box-shadow: 0 1px 2px rgba(16,24,40,.08);
              white-space: pre-wrap; overflow-wrap: anywhere; }
.fb .none { position: relative; display: block; width: fit-content; margin: 8px 0; padding: 4px 10px; border: 1px dashed #b8c2cc;
            border-radius: 6px; background: #fff; color: var(--muted); font-size: 12.5px; }
table.t { width: 100%; border-collapse: collapse; font-size: 13px; }
table.t th, table.t td { border: 1px solid var(--line); padding: 6px 8px; vertical-align: top; text-align: left; }
table.t th, table.t td:first-child { white-space: nowrap; }
table.t th { background: #f3f5f8; }
td.pre, .pre { white-space: pre-wrap; overflow-wrap: anywhere; }
.kv { display: grid; grid-template-columns: max-content 1fr; gap: 2px 14px; font-size: 13px; }
"""
_NOTE = ("只在你的私有 S3 桶里，不公开。用的是你们自己的 Agent 场景时，页面可能含内部信息：只在本账号内查看，"
         "不要把对象改成公开。")


class DefineError(RuntimeError):
    """The session directory cannot be rendered (missing / invalid file, generator check failed)."""


class StaleCriteria(DefineError):
    """criteria.json was built from another fishbone-data.json; the fishbone files are still publishable."""

    def __init__(self, msg: str, files: list):
        super().__init__(msg)
        self.files = files


# ---------------------------------------------------------------- helpers
def _now(now: Optional[datetime]) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(BEIJING).strftime("%Y-%m-%d %H:%M")


def _dict(v) -> dict:
    return v if isinstance(v, dict) else {}


def _list(v) -> list:
    return v if isinstance(v, list) else []


def _pill(text, cls: str) -> str:
    return f'<span class="pill {cls}">{E(text)}</span>'


def sha12(path) -> str:
    return source_info(path)["sha"][:12]


def session_slug(out_dir) -> str:
    """The session directory name as one safe S3 key segment (letters incl. CJK, digits, '.', '_', '-').
    A name that had to be changed gets a short hash of the original, so two sessions never share a key."""
    name = Path(out_dir).resolve().name
    s = re.sub(r"[^\w.-]+", "-", name).strip("-.")
    if s != name or not s:
        s = f"{s or 'session'}-{hashlib.sha256(name.encode('utf-8')).hexdigest()[:6]}"
    return s


def finish(page: str, label: str) -> str:
    """Fill the CSP and refuse a page that would load or link anything outside the file."""
    page = page.replace("__ADLC_CSP__", CSP.format(scripts="'none'"), 1)
    bad = external_refs(page)
    if bad or "<script" in page.lower():
        raise DefineError(f"{label}：页面不是自包含的，未写入：{sorted(set(bad)) or '含脚本'}")
    return page


def _page(title: str, bar: str, body: str) -> str:
    return (f'<!doctype html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n{_CSP_META}\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>{E(title)}</title>\n'
            f"<style>\n{load_css()}\n{PAGE_CSS}</style>\n</head>\n<body>\n{bar}\n<div class=\"layout\"><main>\n{body}\n"
            f"</main></div>\n<footer>AgentDLC 动手实验 · 定义环 · {E(title)}</footer>\n</body>\n</html>\n")


def _meta_rows(meta: dict, keys) -> str:
    return "".join(f"<div class=\"muted\">{E(cn)}</div><div>{E(meta.get(k)) or '—'}</div>" for k, cn in keys)


# ---------------------------------------------------------------- fishbone page
def _selected(data: dict, dim: str) -> list:
    return [b for b in _list(_dict(data.get("barriers")).get(dim)) if isinstance(b, dict) and b.get("selected")]


def _bone(data: dict, dim: Tuple[str, str, str, str], row: str) -> str:
    key, cn, sub, question = dim
    state = _dict(data.get("coverage")).get(key)
    sel = _selected(data, key)
    notes = "".join(f'<div class="sticky">{E(b.get("sticky_text"))}</div>' for b in sel[:3])
    if not sel:
        label = COVERAGE_CN.get(state, ("没有入图的障碍", "n"))[0]
        notes = f'<div class="none">{E(label)}</div>'
    return (f'<div class="bone {row}"><div class="dimhead"><b>{E(cn)}</b> <span class="small muted">{E(sub)}</span>'
            f'<div class="small muted">{E(question)}</div></div>{notes}</div>')


def render_fishbone(data: dict, src: dict, now: Optional[datetime] = None) -> str:
    meta = _dict(data.get("metadata"))
    stamp = _now(now)
    bones = "".join(_bone(data, d, "top") for d in DIMS[:3]) + "".join(_bone(data, d, "bottom") for d in DIMS[3:])
    diagram = (f'<div class="fb">{bones}<div class="spine"></div>'
               f'<div class="head">Agent 无法<br>稳定上线</div></div>')
    rows = []
    for key, cn, _, _ in DIMS:
        state = _dict(data.get("coverage")).get(key)
        label, cls = COVERAGE_CN.get(state, (state or "—", "n"))
        items = [b for b in _list(_dict(data.get("barriers")).get(key)) if isinstance(b, dict)]
        if not items:
            rows.append(f"<tr><td><b>{E(cn)}</b><br>{_pill(label, cls)}</td><td colspan=\"4\" class=\"muted\">—</td></tr>")
            continue
        for i, b in enumerate(items):
            flag = (_pill("入图", "g") if b.get("selected") else _pill("备选（未入图）", "n")) if b.get("confirmed") \
                else _pill("未确认", "o")
            head = f"<td rowspan=\"{len(items)}\"><b>{E(cn)}</b><br>{_pill(label, cls)}</td>" if i == 0 else ""
            rows.append(f"<tr>{head}<td class=\"pre\"><b>{E(b.get('sticky_text'))}</b></td>"
                        f"<td class=\"pre small\">{E(b.get('customer_quote')) or '—'}</td>"
                        f"<td class=\"pre small\">{E(b.get('evidence')) or '—'}</td><td>{flag}</td></tr>")
    park = [p for p in _list(data.get("parking_lot")) if isinstance(p, dict)]
    park_html = ("<table class=\"t\"><tr><th>业务方提出的方案（先不讨论）</th><th>转成了哪条障碍</th></tr>"
                 + "".join(f"<tr><td class=\"pre\">{E(p.get('original'))}</td><td>{E(p.get('converted_to')) or '—'}</td></tr>"
                           for p in park) + "</table>") if park else '<p class="muted">没有。</p>'
    n_sel = sum(len(_selected(data, k)) for k, *_ in DIMS)
    body = (f'<section class="card"><div class="warn"><b>五维鱼骨图预览</b>（{E(meta.get("use_case"))}，入图便签 {n_sel} 张，'
            f"生成于 {E(stamp)} 北京时间）。可编辑的原图是同目录的 <code>fishbone.drawio</code>：在 S3 控制台下载后，"
            f"用 app.diagrams.net 打开。{_NOTE}<br>来源：<code>{E(src['path'])}</code> · sha256 <code>{E(src['sha'])}</code>"
            f"</div><div class=\"kv\">"
            + _meta_rows(meta, [("customer", "业务方"), ("use_case", "Agent 场景"), ("service_target", "服务对象"),
                                ("date", "日期"), ("participants", "参与人"), ("mode", "模式")])
            + f"</div></section>\n<section class=\"card\"><h2>鱼骨图</h2>{diagram}"
            "<p class=\"small muted\">每张便签只写一个具体障碍，写事实不写方案；都由业务方逐条确认过。</p></section>\n"
            "<section class=\"card\"><h2>全部障碍（含备选与原话）</h2><table class=\"t\"><tr><th>维度</th><th>便签</th>"
            f"<th>原话（已脱敏）</th><th>证据 / 数字</th><th>状态</th></tr>{''.join(rows)}</table></section>\n"
            f"<section class=\"card\"><h2>Parking lot</h2>{park_html}</section>")
    bar = topbar("五维鱼骨图 · AgentDLC 定义环", "adlc-fishbone skill 生成 · 只在你的私有 S3 桶里",
                 [("生成于", f"{stamp} 北京时间"), ("sha256", src["sha"])])
    return finish(_page(f"五维鱼骨图 · {meta.get('use_case') or ''}", bar, body), "鱼骨图")


# ---------------------------------------------------------------- criteria page
def _count(rows: List[dict], key: str, order) -> str:
    from collections import Counter
    c = Counter(str(r.get(key) or "—") for r in rows)
    keys = [k for k in order if k in c] + sorted(k for k in c if k not in order)
    return " ".join(f"{E(k)} <b>{c[k]}</b>" for k in keys) or "—"


def render_criteria(doc: dict, src: dict, now: Optional[datetime] = None) -> str:
    meta = _dict(doc.get("metadata"))
    crit = [c for c in _list(doc.get("criteria")) if isinstance(c, dict)]
    stamp = _now(now)
    rows = []
    for c in crit:
        checks = [x for x in _list(c.get("checks")) if isinstance(x, dict)]
        cov = str(c.get("coverage") or "—")
        who = "<br>".join(f"<code>{E(x.get('id'))}</code> {E(x.get('evaluator'))}<div class=\"small muted\">{E(x.get('field'))}</div>"
                          for x in checks) or '<span class="muted">现有评估器都不判这一条</span>'
        notes = "".join(f"<li>{E(n)}</li>" for n in _list(c.get("coverage_notes")))
        gold = " ".join(f"<code>{E(g)}</code>" for g in _list(c.get("golden_ids"))) or "—"
        rows.append(
            f"<tr><td><b>{E(c.get('id'))}</b></td><td class=\"pre\">{E(c.get('text'))}"
            f"<div class=\"small muted pre\">来自便签：{E(c.get('sticky_text'))}</div></td>"
            f"<td>{E(c.get('dimension'))}</td><td>{_pill(c.get('tier') or '—', TIER_PILL.get(str(c.get('tier')), 'n'))}</td>"
            f"<td class=\"pre\">{E(c.get('threshold')) if c.get('threshold') not in (None, '') else '0 违例（不打分）'}</td>"
            f"<td>{E(c.get('executor'))}</td><td>{_pill(cov, COVER_PILL.get(cov, 'n'))}<div class=\"small\">{who}</div>"
            f"{'<ul class=small>' + notes + '</ul>' if notes else ''}</td><td>{gold}</td></tr>")
    props = [p for p in _list(doc.get("golden_proposals")) if isinstance(p, dict)]
    prop_html = ""
    if props:
        prow = "".join(
            f"<tr><td>{E(p.get('criterion'))}</td><td><code>{E(p.get('feedback_id'))}</code><div class=\"small muted\">"
            f"{'新写的需求反馈（extra-feedback）' if p.get('source') == 'extra' else '上个月已有的需求反馈'}</div></td>"
            f"<td class=\"pre\"><b>{E(p.get('title'))}</b></td><td class=\"small\">{E(p.get('labels'))}</td>"
            f"<td class=\"pre small\">{E(p.get('why'))}</td></tr>" for p in props)
        prop_html = ("<section class=\"card\"><h2>提议补充的黄金样本</h2><div class=\"warn\">这是提议：要业务方同意后才写入黄金集。"
                     "写入并冻结后要重新运行 <code>./40-create-evaluators.sh</code>（黄金集打包在评估器 Lambda 里）；"
                     "新增了需求反馈时，还要重新运行 <code>./30-deploy-runtime.sh</code>（相似需求工具读的是打包进 Runtime 的需求反馈）。</div>"
                     "<table class=\"t\"><tr><th>判据</th><th>需求编号</th><th>标题</th><th>期望标签</th><th>为什么补这条</th></tr>"
                     f"{prow}</table></section>\n")
    done = [p for p in _list(doc.get("golden_applied")) if isinstance(p, dict)]
    if done:
        drow = "".join(f"<tr><td>{E(p.get('criterion'))}</td><td><code>{E(p.get('golden_id'))}</code></td>"
                       f"<td><code>{E(p.get('feedback_id'))}</code></td><td class=\"pre\">{E(p.get('title'))}</td>"
                       f"<td class=\"small\">{E(p.get('labels'))}</td></tr>" for p in done)
        prop_html += ("<section class=\"card\"><h2>已写入黄金集的样本</h2><div class=\"note\">记得重新运行 "
                      "<code>./40-create-evaluators.sh</code>，评估器才会用上新黄金集。</div><table class=\"t\"><tr><th>判据</th>"
                      f"<th>黄金编号</th><th>需求编号</th><th>标题</th><th>期望标签</th></tr>{drow}</table></section>\n")
    warns = "".join(f"<li>{E(w)}</li>" for w in _list(doc.get("warnings")))
    body = (f'<section class="card"><div class="warn"><b>判据表预览</b>（{len(crit)} 条，生成于 {E(stamp)} 北京时间）。'
            f"每条判据的主语是 Agent、谓语可观测；“现有评估器覆盖”由 adlc-criteria skill 的对照表算出。{_NOTE}"
            f"<br>来源：<code>{E(src['path'])}</code> · sha256 <code>{E(src['sha'])}</code></div><div class=\"kv\">"
            + _meta_rows(meta, [("customer", "业务方"), ("use_case", "Agent 场景"), ("date", "日期")])
            + f"<div class=\"muted\">三档</div><div>{_count(crit, 'tier', ['红线', '门禁', '观测'])}</div>"
            f"<div class=\"muted\">覆盖</div><div>{_count(crit, 'coverage', ['已覆盖', '部分覆盖', '未覆盖'])}</div>"
            f"<div class=\"muted\">执行方式</div><div>{_count(crit, 'executor', ['代码断言', '裁判', '内置评估器', '指标读数'])}</div>"
            f"</div>{'<div class=note><b>提醒</b><ul class=small>' + warns + '</ul></div>' if warns else ''}</section>\n"
            "<section class=\"card\"><h2>判据表</h2><div style=\"overflow-x:auto\"><table class=\"t\" style=\"min-width:1100px\">"
            "<tr><th>ID</th><th>判据（主语是 Agent）</th><th>维度</th><th>档位</th><th>阈值</th><th>执行方式</th>"
            f"<th>现有评估器覆盖</th><th>黄金样本</th></tr>{''.join(rows)}</table></div></section>\n{prop_html}")
    bar = topbar("判据表 · AgentDLC 定义环", "adlc-criteria skill 生成 · 只在你的私有 S3 桶里",
                 [("生成于", f"{stamp} 北京时间"), ("sha256", src["sha"])])
    return finish(_page(f"判据表 · {meta.get('use_case') or ''}", bar, body), "判据表")


# ---------------------------------------------------------------- session directory
def _generator():
    spec = importlib.util.spec_from_file_location("adlc_generate_fishbone", GENERATOR)
    if spec is None or spec.loader is None:
        raise DefineError(f"找不到鱼骨图生成脚本：{GENERATOR}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read_json(path: Path, label: str) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise DefineError(f"{label}：读不了 {_shown(path)}（{type(e).__name__}: {e}）") from e
    if not isinstance(doc, dict):
        raise DefineError(f"{label}：{_shown(path)} 顶层不是 JSON 对象")
    return doc


def build(out_dir, now: Optional[datetime] = None) -> List[Tuple[str, Path, str]]:
    """Regenerate fishbone.drawio, write fishbone.html (+ criteria.html). Returns [(label, path, content type)]."""
    d = Path(out_dir)
    data_path = d / "fishbone-data.json"
    if not data_path.is_file():
        raise DefineError(f"{_shown(d)} 里没有 fishbone-data.json：先用 adlc-fishbone skill 完成鱼骨图")
    data = _read_json(data_path, "鱼骨图")
    gen = _generator()
    try:
        result = gen.generate_fishbone(data_path, gen.TEMPLATE_PATH, d / "fishbone.drawio")
        gen.write_validation_report(result, d / "validation-report.txt")
    except Exception as e:  # noqa: BLE001 - a malformed JSON must end in a message, never a traceback
        raise DefineError(f"鱼骨图生成脚本出错（{type(e).__name__}: {e}），什么都没有上传；先运行 "
                          f"generate_fishbone.py {_shown(d)} --check-json 查看") from e
    if not result["success"]:
        raise DefineError("鱼骨图校验没通过，什么都没有上传：\n   " + "\n   ".join(result["validation_errors"][:8])
                          + f"\n   详见 {_shown(d / 'validation-report.txt')}")
    out = []
    page = render_fishbone(data, {"path": _shown(data_path), "sha": sha12(data_path)}, now)
    (d / "fishbone.html").write_text(page, encoding="utf-8")
    out += [("鱼骨图预览", d / "fishbone.html", "text/html; charset=utf-8"),
            ("鱼骨图 drawio", d / "fishbone.drawio", DRAWIO_TYPE)]
    crit_path = d / "criteria.json"
    if crit_path.is_file():
        doc = _read_json(crit_path, "判据表")
        built_from = str(_dict(doc.get("source")).get("fishbone_sha256") or "")
        if built_from != sha12(data_path):
            stale = d / "criteria.html"
            if stale.exists():
                stale.unlink()  # never publish a criteria page built from an older fishbone
            raise StaleCriteria(f"criteria.json 是按旧的鱼骨图生成的（{built_from or '?'} ≠ {sha12(data_path)}），判据表没有发布："
                                f"重新运行 criteria.py build {_shown(d)} 后再发布", out)
        page = render_criteria(doc, {"path": _shown(crit_path), "sha": sha12(crit_path)}, now)
        (d / "criteria.html").write_text(page, encoding="utf-8")
        out.append(("判据表预览", d / "criteria.html", "text/html; charset=utf-8"))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="./tools/publish-define-pages.sh", allow_abbrev=False,
                                 description="把鱼骨图 / 判据表生成自包含网页，连同 drawio 上传到你的私有 S3 桶")
    ap.add_argument("out_dir", help="会话目录，例如 outputs/fishbone/<组名>-<日期>")
    ap.add_argument("--no-upload", action="store_true", help="只生成本地网页，不上传（不调用任何 AWS 接口）")
    a = ap.parse_args(argv)
    rc = 0
    try:
        files = build(a.out_dir)
    except StaleCriteria as e:
        print(f"⚠️ {e}")
        files, rc = e.files, 1
    except DefineError as e:
        print(f"❌ {e}")
        return 1
    for label, path, _ in files:
        print(f"  ✅ {label} → {_shown(path)}")
    if a.no_upload:
        print("--no-upload：只写了本地文件，没有上传。EC2 上打不开这些文件；去掉 --no-upload 重跑即可上传到你的私有桶。")
        return rc
    from labkit import publish as P  # boto3 only on the upload path
    prefix = f"reports/define/{session_slug(a.out_dir)}"
    links = []
    for label, path, ctype in files:
        try:
            links.append((label, P.publish(str(path), key=f"{prefix}/{path.name}", content_type=ctype)))
        except P.NotPrivateError as e:  # same bucket for every file: stop here
            print(f"  ❌ {e}")
            return 1
        except Exception as e:  # noqa: BLE001 - report every failure, keep the local file
            rc = 1
            print(f"  ❌ {label}：上传失败（本地文件保留在 {_shown(path)}）：{type(e).__name__}: {e}")
    if links:
        w = max(len(label) for label, _ in links)
        print("\n在笔记本浏览器打开（需已登录这个实验账号的控制台）：网页点右上角「打开」；drawio 点「下载」，"
              "再用 app.diagrams.net 打开（桶是私有的，链接不能给组外的人用）：")
        for label, url in links:
            print(f"  {label.ljust(w)}  {url}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
