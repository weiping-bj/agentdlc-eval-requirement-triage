"""Draw the AgentDLC six-ring flywheel SVGs used by the Workshop Studio handbook (maintainer tool).

  python3 tools/ws_flywheel.py            # (re)write workshop-studio/static/images/flywheel-*.svg
  python3 tools/ws_flywheel.py --check    # exit 1 if a committed SVG differs from what this script draws

flywheel-overview.svg   chapter 0: the six rings around the hub 黄金标准, each ring's relation to the hub, and the two
                        loops (修 Agent = prompt / tools; 修评估体系 = golden set / capability catalog)
flywheel-<ring>.svg     the small "你在这里" marker of one chapter: the ring(s) of that chapter highlighted
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


def drawings() -> dict:
    names = {"define": "你在这里：① 定义", "build": "你在这里：② 构建", "evaluate": "你在这里：③ 评估",
             "optimize": "你在这里：② 构建 + ③ 评估（修 Agent）", "release-observe": "你在这里：④ 发布 + ⑤ 观测",
             "feedback": "你在这里：⑥ 回流", "all": "六环全景"}
    out = {f"flywheel-{k}.svg": marker_svg(v, names[k]) for k, v in MARKERS.items()}
    out["flywheel-overview.svg"] = overview_svg()
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
