"""Draw the AgentDLC six-ring flywheel SVGs (and the chapter-4 evaluator map) used by the Workshop Studio handbook
(maintainer tool).

  python3 tools/ws_flywheel.py            # (re)write workshop-studio/static/images/flywheel-*.svg, eval-golden-map.svg
  python3 tools/ws_flywheel.py --check    # exit 1 if a committed SVG differs from what this script draws

flywheel-overview.svg   chapter 0: the six rings around the hub 黄金标准, each ring's relation to the hub, and the two
                        loops (修 Agent = prompt / tools; 修评估体系 = golden set / capability catalog)
flywheel-<ring>.svg     the small "你在这里" marker of one chapter: the ring(s) of that chapter highlighted
eval-golden-map.svg     chapter 4.1: which golden-set fields each evaluator reads, how the answers reach it (bundled
                        into the Lambdas / reference input on demand), on-demand vs online, and which pillar
                        (红线 / 门禁 / 观测) its result feeds. Mirrors labkit/evaluate.py, labkit/online.py and
                        evaluators/shared/pillars.py: change it with them.
Self-contained SVG (no external font, image or link), deterministic output. Standard library only.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parent.parent / "workshop-studio" / "static" / "images"
FONT = "'PingFang SC','Microsoft YaHei','Noto Sans CJK SC','Noto Sans SC',sans-serif"
RINGS = [("define", "① 定义", "产出圆心"), ("build", "② 构建", "遵循圆心"), ("evaluate", "③ 评估", "取用圆心"),
         ("release", "④ 发布", "判定用圆心"), ("observe", "⑤ 观测", "复用圆心"), ("feedback", "⑥ 回流", "回灌圆心")]
MARKERS = {  # file stem -> highlighted rings
    "define": ("define",), "build": ("build",), "evaluate": ("evaluate",), "optimize": ("build", "evaluate"),
    "release-observe": ("release", "observe"), "feedback": ("feedback",), "all": tuple(r for r, _l, _n in RINGS),
}
HI, HI_TEXT, LO, LO_TEXT, LINE, HUB = "#e8770e", "#ffffff", "#f1f3f6", "#3b4654", "#9aa7b6", "#1f3350"


def _pos(i: int, cx: float, cy: float, r: float) -> tuple:
    a = -math.pi / 2 + i * 2 * math.pi / len(RINGS)
    return cx + r * math.cos(a), cy + r * math.sin(a)


def _arc_arrows(cx, cy, r, node_r) -> str:
    out = []
    for i in range(len(RINGS)):
        a1 = -math.pi / 2 + i * 2 * math.pi / 6 + node_r / r * 1.15
        a2 = -math.pi / 2 + (i + 1) * 2 * math.pi / 6 - node_r / r * 1.25
        x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
        x2, y2 = cx + r * math.cos(a2), cy + r * math.sin(a2)
        out.append(f'<path d="M{x1:.1f},{y1:.1f} A{r:.1f},{r:.1f} 0 0 1 {x2:.1f},{y2:.1f}" fill="none" '
                   f'stroke="{LINE}" stroke-width="2" marker-end="url(#ah)"/>')
    return "".join(out)


def marker_svg(highlight: tuple, title: str) -> str:
    w = h = 300
    cx = cy = 150
    r, nr = 104, 34
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" '
             f'aria-label="{escape(title)}"><title>{escape(title)}</title>',
             f'<defs><marker id="ah" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" '
             f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{LINE}"/></marker></defs>',
             f'<rect width="{w}" height="{h}" fill="#ffffff"/>', _arc_arrows(cx, cy, r, nr),
             f'<circle cx="{cx}" cy="{cy}" r="44" fill="{HUB}"/>',
             f'<text x="{cx}" y="{cy - 4}" text-anchor="middle" font-family="{FONT}" font-size="15" font-weight="700" '
             f'fill="#ffffff">黄金标准</text>',
             f'<text x="{cx}" y="{cy + 15}" text-anchor="middle" font-family="{FONT}" font-size="10.5" '
             f'fill="#c9d4e3">黄金集 + 五维判据</text>']
    for i, (key, label, _rel) in enumerate(RINGS):
        x, y = _pos(i, cx, cy, r)
        on = key in highlight
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{nr}" fill="{HI if on else LO}" '
                     f'stroke="{HI if on else LINE}" stroke-width="{3 if on else 1.5}"/>')
        parts.append(f'<text x="{x:.1f}" y="{y + 5:.1f}" text-anchor="middle" font-family="{FONT}" font-size="15" '
                     f'font-weight="{700 if on else 400}" fill="{HI_TEXT if on else LO_TEXT}">{escape(label)}</text>')
    parts.append("</svg>\n")
    return "".join(parts)


def overview_svg() -> str:
    w, h = 900, 560
    cx, cy = 300, 285
    r, nr = 190, 52
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" '
             'aria-label="AgentDLC 六环飞轮与两条回路"><title>AgentDLC 六环飞轮与两条回路</title>',
             f'<defs><marker id="ah" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" '
             f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{LINE}"/></marker>'
             f'<marker id="ag" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" '
             f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#2f6fdc"/></marker>'
             f'<marker id="ap" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" '
             f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#7b5bd6"/></marker></defs>',
             f'<rect width="{w}" height="{h}" fill="#ffffff"/>', _arc_arrows(cx, cy, r, nr),
             f'<circle cx="{cx}" cy="{cy}" r="78" fill="{HUB}"/>',
             f'<text x="{cx}" y="{cy - 10}" text-anchor="middle" font-family="{FONT}" font-size="24" font-weight="700" '
             f'fill="#ffffff">黄金标准</text>',
             f'<text x="{cx}" y="{cy + 16}" text-anchor="middle" font-family="{FONT}" font-size="14" fill="#c9d4e3">'
             '黄金集 + 五维判据</text>',
             f'<text x="{cx}" y="{cy + 36}" text-anchor="middle" font-family="{FONT}" font-size="12" fill="#c9d4e3">'
             '（圆心）</text>']
    for i, (_key, label, rel) in enumerate(RINGS):
        x, y = _pos(i, cx, cy, r)
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{nr}" fill="{LO}" stroke="{LINE}" stroke-width="1.5"/>')
        parts.append(f'<text x="{x:.1f}" y="{y - 2:.1f}" text-anchor="middle" font-family="{FONT}" font-size="19" '
                     f'font-weight="700" fill="{LO_TEXT}">{escape(label)}</text>')
        parts.append(f'<text x="{x:.1f}" y="{y + 20:.1f}" text-anchor="middle" font-family="{FONT}" font-size="13" '
                     f'fill="#6b7785">{escape(rel)}</text>')
    # the two loops, drawn inside the wheel: 评估 -> 构建 (修 Agent), 评估 -> 定义 (修评估体系)
    (bx, by), (ex, ey), (dx, dy) = _pos(1, cx, cy, r), _pos(2, cx, cy, r), _pos(0, cx, cy, r)
    parts.append(f'<path d="M{ex - 30:.1f},{ey - 34:.1f} Q{cx + 120:.1f},{cy:.1f} {bx - 26:.1f},{by + 40:.1f}" '
                 'fill="none" stroke="#2f6fdc" stroke-width="2.5" stroke-dasharray="7 5" marker-end="url(#ag)"/>')
    parts.append(f'<path d="M{ex - 50:.1f},{ey - 10:.1f} Q{cx + 30:.1f},{cy - 40:.1f} {dx + 16:.1f},{dy + 50:.1f}" '
                 'fill="none" stroke="#7b5bd6" stroke-width="2.5" stroke-dasharray="7 5" marker-end="url(#ap)"/>')
    legend = [("#2f6fdc", "修 Agent", "改提示词、工具、模型 → 重新部署 → 用同一把尺子再评估"),
              ("#7b5bd6", "修评估体系", "改黄金集、能力目录、判据 → 冻结 → 重新部署评估器；分数要重新算基线"),
              (LINE, "主循环", "定义 → 构建 → 评估 → 发布 → 观测 → 回流，每一环都对齐圆心")]
    y0 = 140
    parts.append(f'<text x="560" y="{y0 - 34}" font-family="{FONT}" font-size="18" font-weight="700" fill="{LO_TEXT}">'
                 '两条回路</text>')
    for k, (color, name, desc) in enumerate(legend):
        yy = y0 + k * 92
        dash = "" if color == LINE else 'stroke-dasharray="7 5" '
        parts.append(f'<line x1="560" y1="{yy}" x2="600" y2="{yy}" stroke="{color}" stroke-width="3" {dash}/>')
        parts.append(f'<text x="612" y="{yy + 6}" font-family="{FONT}" font-size="17" font-weight="700" '
                     f'fill="{color if color != LINE else LO_TEXT}">{escape(name)}</text>')
        words, line, lines = desc, "", []
        for ch in words:  # wrap at ~20 CJK characters
            line += ch
            if len(line) >= 20 and ch in "、，；→ ":
                lines.append(line)
                line = ""
        lines.append(line)
        for j, ln in enumerate(lines):
            parts.append(f'<text x="560" y="{yy + 30 + j * 20}" font-family="{FONT}" font-size="14" fill="#3b4654">'
                         f'{escape(ln.strip())}</text>')
    parts.append(f'<text x="560" y="470" font-family="{FONT}" font-size="13" fill="#6b7785">每一环结束时问一句：</text>')
    parts.append(f'<text x="560" y="492" font-family="{FONT}" font-size="14" font-weight="700" fill="{LO_TEXT}">'
                 '“这一环有没有对齐黄金标准？”</text>')
    parts.append("</svg>\n")
    return "".join(parts)


MONO = "Menlo,Consolas,'DejaVu Sans Mono',monospace"
# golden-set field chips: key -> (label, text colour, fill)
FIELDS = {"exp": ("期望标签", "#2f6fdc", "#e8f0fd"), "acc": ("可接受答案", "#138a8a", "#e3f4f4"),
          "car": ("可否自动拒绝", "#b0306a", "#fbe9f1"), "pts": ("拒绝 / 话术要点", "#7b5bd6", "#f0ecfb"),
          "cat": ("能力目录", "#8a6d1f", "#f7f1e1"), "ref": ("期望标签（参考输入）", "#2f6fdc", "#ffffff"),
          "none": ("不读黄金集", "#6b7785", "#f1f3f6")}
PILLARS = {"red": ("#c62828", "#fdecea"), "gate": ("#c45f00", "#fff1e3"), "obs": ("#6b7785", "#f1f3f6")}
# (evaluator, type, golden fields read, on demand, online, [(pillar, chip text)]): labkit/evaluate.py (BUILTINS,
# expected_text), labkit/online.py (ONLINE_*), labkit/compare_story.py (ROLES: pillars), evaluators/*/ (fields read)
EVAL_ROWS = [
    ("label_important · label_urgent", "代码型（Lambda）", ("exp", "car"), True, True,
     [("gate", "门禁 · 准确率"), ("red", "红线 · 误拒")]),
    ("label_decision", "代码型（Lambda）", ("exp", "acc", "car"), True, True,
     [("gate", "门禁 · 准确率"), ("red", "红线 · 误拒")]),
    ("label_accuracy", "代码型（Lambda）", ("exp", "acc", "car"), True, True,
     [("red", "红线 · 误拒"), ("obs", "观测 · 整条通过率")]),
    ("reject_quality", "代码型（Lambda 内调用评审模型）", ("pts", "car", "cat"), True, True,
     [("red", "红线 · 话术"), ("gate", "门禁 · 话术通过率"), ("obs", "观测")]),
    ("reject_quality_llm", "LLM 评审（AgentCore 原生）", ("none",), True, False, [("obs", "观测")]),
    ("Builtin.Correctness", "内置", ("ref",), True, False, [("obs", "观测")]),
    ("Builtin.Helpfulness", "内置", ("none",), True, True, [("obs", "观测")]),
]


def _tw(s: str, size: float) -> float:
    """Approximate rendered width: CJK / full-width characters are one em, the rest about 0.6 em."""
    return sum(size if ord(c) > 0x2E7F else size * 0.6 for c in s)


def _chip(x: float, y: float, label: str, color: str, fill: str, size: float = 12, dashed: bool = False) -> tuple:
    w = _tw(label, size) + 14
    dash = ' stroke-dasharray="4 3"' if dashed else ""
    svg = (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{size + 10:.0f}" rx="9" fill="{fill}" '
           f'stroke="{color}" stroke-width="1.2"{dash}/>'
           f'<text x="{x + 7:.1f}" y="{y + size + 3:.1f}" font-family="{FONT}" font-size="{size}" fill="{color}">'
           f'{escape(label)}</text>')
    return svg, w


def _t(x, y, s: str, size: float = 13, color: str = LO_TEXT, weight: int = 400, family: str = FONT) -> str:
    return (f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" font-weight="{weight}" '
            f'fill="{color}" xml:space="preserve">{escape(s)}</text>')


def evalmap_svg() -> str:
    w, h = 960, 740
    title = "评估器怎么用黄金集：读哪些字段、按需还是在线、进结论的哪一支柱"
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" '
         f'aria-label="{escape(title)}"><title>{escape(title)}</title>',
         f'<rect width="{w}" height="{h}" fill="#ffffff"/>', _t(20, 30, "评估器怎么用黄金集", 19, LO_TEXT, 700)]
    # top band: one golden item, the capability catalog, how the answers reach the evaluators
    p.append(f'<rect x="20" y="46" width="440" height="146" rx="8" fill="#fbfcfd" stroke="{LINE}"/>')
    p.append(_t(34, 70, "黄金集的一条样本（例：G29 · FB-2610-041）", 14, LO_TEXT, 700))
    sample = [("exp", "重要 否 · 紧急 否 · 建议 拒绝"), ("acc", "拒绝（模糊样例可以有多个）"),
              ("car", "是（为“否”的需求判拒绝 = 误拒）"), ("pts", "租户间数据隔离（CAP-027）· 替代方案")]
    for i, (k, val) in enumerate(sample):
        chip, _w = _chip(34, 82 + i * 26, *FIELDS[k])
        p.append(chip)
        p.append(_t(146, 82 + i * 26 + 16, val, 12.5, "#3b4654"))
    p.append(f'<rect x="474" y="46" width="200" height="146" rx="8" fill="#fbfcfd" stroke="{LINE}"/>')
    chip, _w = _chip(488, 58, *FIELDS["cat"])
    p.append(chip)
    for j, ln in enumerate(["不在黄金集里，随", "reject_quality 打包；", "评审时摘录相关能力的", "状态、限制、替代方案和",
                            "菜单路径，用来核实话术"]):
        p.append(_t(488, 104 + j * 18, ln, 12.5, "#3b4654"))
    p.append(f'<rect x="688" y="46" width="252" height="146" rx="8" fill="#fbfcfd" stroke="{LINE}"/>')
    p.append(_t(702, 70, "答案怎么到评估器", 14, LO_TEXT, 700))
    for j, ln in enumerate(["① 冻结时打包进 Lambda", "   （golden_set.json）：按需、", "   在线都按需求编号查",
                            "② 按需评估另把期望标签当", "   参考输入传给每个评估器；", "   在线评估没有这一项"]):
        p.append(_t(702, 94 + j * 17, ln, 12.5, "#3b4654"))
    # the table: one row per evaluator
    p.append(_t(20, 222, "每一行：左边同色标签 = 这个评估器读的黄金集字段；右边 = 它的结果进结论的哪一支柱", 12.5, "#6b7785"))
    top, rh = 256, 44
    for x, s in ((26, "读黄金集的哪些字段"), (330, "评估器 · 类型"), (575, "按需 / 在线"), (682, "进结论的哪一支柱")):
        p.append(_t(x, 246, s, 13, LO_TEXT, 700))
    p.append(f'<line x1="20" y1="{top - 2}" x2="940" y2="{top - 2}" stroke="{LINE}" stroke-width="1.2"/>')
    for i, (name, typ, fields, ondemand, online, pills) in enumerate(EVAL_ROWS):
        y = top + i * rh
        if i % 2 == 0:
            p.append(f'<rect x="20" y="{y}" width="920" height="{rh}" fill="#f7f9fb"/>')
        x = 26.0
        for k in fields:
            chip, cw = _chip(x, y + 11, *FIELDS[k], dashed=(k == "ref"))
            p.append(chip)
            x += cw + 6
        p.append(_t(330, y + 19, name, 13, LO_TEXT, 700, MONO))
        p.append(_t(330, y + 36, typ, 11.5, "#6b7785"))
        for j, (on, label) in enumerate(((ondemand, "按需"), (online, "在线"))):
            px = 575 + j * 52
            if on:
                p.append(f'<rect x="{px}" y="{y + 11}" width="44" height="22" rx="11" fill="{HUB}"/>'
                         + _t(px + 10, y + 26, label, 12, "#ffffff"))
            else:
                p.append(f'<rect x="{px}" y="{y + 11}" width="44" height="22" rx="11" fill="#ffffff" stroke="#c3cad3" '
                         'stroke-dasharray="3 3"/>' + _t(px + 10, y + 26, label, 12, "#b3bcc7"))
        x = 682.0
        for kind, label in pills:
            chip, cw = _chip(x, y + 11, label, *PILLARS[kind])
            p.append(chip)
            x += cw + 6
    bottom = top + len(EVAL_ROWS) * rh
    p.append(f'<line x1="20" y1="{bottom}" x2="940" y2="{bottom}" stroke="{LINE}" stroke-width="1.2"/>')
    # the three pillars (evaluators/shared/pillars.py)
    boxes = [("red", "红线 · 一票否决（要求 0 条）", ["误拒：判了拒绝，黄金集却标“不可自动拒绝”", "（标签评估器返回 CriticalFail）",
                                                  "话术红线：reject_quality 解释里的“红线：…”"]),
             ("gate", "门禁 · 各 ≥ 70%", ["是否重要 / 是否紧急 / 处理建议准确率", "拒绝话术通过率（判拒绝的条目中 Pass 占比）",
                                        "没有拒绝条目时话术门禁“不适用”"]),
             ("obs", "观测 · 只看趋势", ["整条通过率、五维准确率、话术分项和均分、", "reject_quality_llm、Builtin.*：", "不影响结论，用来归因和对比"])]
    by = bottom + 18
    for i, (kind, head, lines) in enumerate(boxes):
        color, fill = PILLARS[kind]
        bx = 20 + i * 310
        p.append(f'<rect x="{bx}" y="{by}" width="300" height="96" rx="8" fill="{fill}" stroke="{color}" '
                 'stroke-width="1.5"/>')
        p.append(_t(bx + 12, by + 22, head, 13.5, color, 700))
        for j, ln in enumerate(lines):
            p.append(_t(bx + 12, by + 44 + j * 19, ln, 12, "#3b4654"))
    p.append(_t(20, by + 122, "结论 = 没有红线，且每个门禁都达标。一条需求出现红线，它在每个门禁和整条通过率里都按错计。",
                13, LO_TEXT, 700))
    p.append(_t(20, by + 142, "规则在 evaluators/shared/pillars.py；评估器的角色和第 5 章对比页 ① 一致（labkit/compare_story.py）",
                12, "#6b7785"))
    p.append("</svg>\n")
    return "".join(p)


def drawings() -> dict:
    names = {"define": "你在这里：① 定义", "build": "你在这里：② 构建", "evaluate": "你在这里：③ 评估",
             "optimize": "你在这里：② 构建 + ③ 评估（修 Agent）", "release-observe": "你在这里：④ 发布 + ⑤ 观测",
             "feedback": "你在这里：⑥ 回流", "all": "六环全景"}
    out = {f"flywheel-{k}.svg": marker_svg(v, names[k]) for k, v in MARKERS.items()}
    out["flywheel-overview.svg"] = overview_svg()
    out["eval-golden-map.svg"] = evalmap_svg()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    bad = []
    for name, svg in drawings().items():
        p = OUT / name
        if a.check:
            if not p.is_file() or p.read_text(encoding="utf-8") != svg:
                bad.append(name)
        else:
            OUT.mkdir(parents=True, exist_ok=True)
            p.write_text(svg, encoding="utf-8")
    if bad:
        print("out of date (run python3 tools/ws_flywheel.py): " + ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
