#!/usr/bin/env python3
"""Fill the five-dimension fishbone drawio template from fishbone-data.json (adlc-fishbone skill).

Adapted from the AgentDLC v-Team's agent-dlc-fishbone skill (generate_fishbone.py, same cell-ID map and the
same JSON schema 1.0). Changes for the workshop lab: a CLI, the template is found inside this skill
(../assets/fishbone-template.drawio, never by scanning the disk), every report check is computed rather than
printed as PASS, and text written into the template's html=1 cells is HTML-escaped (a note that says "<b>" or
"A&B" shows literally instead of being parsed as markup).

  python3 .claude/skills/adlc-fishbone/scripts/generate_fishbone.py outputs/fishbone/<group>-<date>
  python3 .claude/skills/adlc-fishbone/scripts/generate_fishbone.py --check-template
  python3 .claude/skills/adlc-fishbone/scripts/generate_fishbone.py --check-json outputs/fishbone/<dir>

Reads <dir>/fishbone-data.json, writes <dir>/fishbone.drawio and <dir>/validation-report.txt, prints the result
as JSON. Exit code 0 = every check passed, 1 = a check failed (nothing is guessed), 2 = usage error.
Standard library only; runs with the system python3 (3.9+).
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = SKILL_DIR / "assets" / "fishbone-template.drawio"

# Cell IDs of the template's 2400x1350 editable layer (unchanged from the v-Team skill).
META_CELLS = {
    "customer": "3QwRYIxs2djGdYka4jAl-34",
    "date": "3QwRYIxs2djGdYka4jAl-35",
    "use_case": "3QwRYIxs2djGdYka4jAl-36",
    "service_target": "3QwRYIxs2djGdYka4jAl-37",
}
DIMENSION_SLOTS = {
    "cognition": ["3QwRYIxs2djGdYka4jAl-38", "3QwRYIxs2djGdYka4jAl-39", "3QwRYIxs2djGdYka4jAl-40"],
    "quality": ["3QwRYIxs2djGdYka4jAl-41", "3QwRYIxs2djGdYka4jAl-42", "3QwRYIxs2djGdYka4jAl-43"],
    "responsibility": ["3QwRYIxs2djGdYka4jAl-44", "3QwRYIxs2djGdYka4jAl-45", "3QwRYIxs2djGdYka4jAl-46"],
    "cost": ["3QwRYIxs2djGdYka4jAl-47", "3QwRYIxs2djGdYka4jAl-48", "3QwRYIxs2djGdYka4jAl-49"],
    "performance": ["3QwRYIxs2djGdYka4jAl-50", "3QwRYIxs2djGdYka4jAl-51", "3QwRYIxs2djGdYka4jAl-52"],
    "other": ["3QwRYIxs2djGdYka4jAl-53", "3QwRYIxs2djGdYka4jAl-54", "3QwRYIxs2djGdYka4jAl-55"],
}
DIM_CN = {"cognition": "认知", "quality": "质量", "responsibility": "责任", "cost": "成本",
          "performance": "性能", "other": "其他"}
ALL_REQUIRED_IDS = list(META_CELLS.values()) + [cid for slots in DIMENSION_SLOTS.values() for cid in slots]
PLACEHOLDER_TEXTS = ("请填写", "请填写具体 Use Case", "YYYY-MM-DD", "双击填写障碍", "示例（可删除）", "对内 / To B / To C")
PLACEHOLDER_MARKS = ("双击填写障碍", "示例（可删除）")  # never part of real content; checked in every output cell
CURRENT_LAYER_PREFIX = "3QwRYIxs2djGdYka4jAl-"  # the 2400x1350 editable layer; the template also carries two older
# full copies (bB5O89TeUSKUgIe8cjWL-*) hidden behind it, still holding placeholders. They are dropped from the output.
KEEP_CELLS = ("0", "1")
COVERAGE_STATES = ("confirmed", "explored_empty", "unresolved")
# Performance = latency / throughput / stability / availability only (SKILL.md "怎么归类"). A performance note that talks
# about omissions and has none of the performance words is most likely completeness, i.e. quality.
OMISSION_WORDS = ("漏", "不完整", "不全", "没覆盖", "没考虑", "丢", "忽略", "只判了", "只处理", "没管")
PERFORMANCE_WORDS = ("秒", "分钟", "小时", "ms", "QPS", "超时", "卡住", "卡死", "慢", "延迟", "并发", "吞吐", "排队", "高峰",
                     "不可用", "可用性", "宕", "挂了", "崩", "稳定", "失败", "报错", "积压", "处理不完", "处理不过来",
                     "来不及", "跑完", "跑不完", "时间窗", "响应时间", "响应慢")
CLASSIFY_WARNING = ("performance: check the dimension of this note - an omission / completeness problem belongs to "
                    "quality, only not finishing in time / unstable / unavailable is performance (SKILL.md 怎么归类):")
STICKY_SOFT_LIMIT = 50  # display width (CJK = 1, ASCII = 0.5); wider notes may overflow the 340x82 sticky

CHECKS = [  # (key, text) in report order
    ("json", "JSON parses and has schema_version 1.0"),
    ("status", "Status is approved_for_render"),
    ("metadata", "All four metadata fields are non-empty"),
    ("coverage", "All six coverage fields exist, use a known state and agree with the barriers"),
    ("confirmed", "Every selected barrier is confirmed and has sticky text"),
    ("max3", "No dimension has more than 3 selected barriers"),
    ("template", "Every required Cell ID exists exactly once in the template"),
    ("filled", "All selected sticky texts appear in their target cells"),
    ("empty", "All unused target cells are empty"),
    ("placeholder", "No placeholder text remains in any target cell"),
    ("layers", "Stale template layers are removed; no cell anywhere still holds a placeholder"),
    ("xml", "Generated drawio reparses as valid XML"),
    ("nonempty", "Output file is non-empty"),
]


def display_width(text: str) -> float:
    """Width in CJK characters: full-width / wide characters count 1, everything else (digits, spaces) 0.5."""
    import unicodedata
    return sum(1.0 if unicodedata.east_asian_width(c) in "WF" else 0.5 for c in text)


def cell_text(text) -> str:
    """Plain text -> value of an html=1 drawio cell: escaped, line breaks as <br>."""
    s = html.escape(str(text if text is not None else "").strip(), quote=True)
    return s.replace("\r\n", "\n").replace("\n", "<br>")


def _cells(root, cell_id: str) -> list:
    return [c for c in root.iter() if c.tag in ("mxCell", "UserObject", "object") and c.get("id") == cell_id]


def validate_template(root) -> Tuple[bool, List[str]]:
    errors = []
    for cid in ALL_REQUIRED_IDS:
        n = len(_cells(root, cid))
        if n == 0:
            errors.append(f"MISSING: {cid}")
        elif n > 1:
            errors.append(f"DUPLICATE: {cid} (found {n})")
    return not errors, errors


def _items(data, dim: str) -> list:
    barriers = data.get("barriers") if isinstance(data, dict) and isinstance(data.get("barriers"), dict) else {}
    items = barriers.get(dim)
    return [b for b in items if isinstance(b, dict)] if isinstance(items, list) else []


def selected_of(data: dict, dim: str) -> list:
    return [b for b in _items(data, dim) if b.get("selected") is True]


def validate_json(data) -> Tuple[Dict[str, List[str]], List[str]]:
    """Errors per check key (empty list = pass) and warnings. Never raises on a malformed document."""
    err: Dict[str, List[str]] = {k: [] for k, _ in CHECKS}
    warns: List[str] = []
    if not isinstance(data, dict):
        err["json"].append("top level must be a JSON object")
        return err, warns
    if data.get("schema_version") != "1.0":
        err["json"].append("schema_version must be '1.0'")
    if data.get("status") != "approved_for_render":
        err["status"].append(f"status is {data.get('status')!r}, expected 'approved_for_render'")
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    for field in META_CELLS:
        if not str(meta.get(field) or "").strip():
            err["metadata"].append(f"metadata.{field} is empty")
    coverage, barriers = data.get("coverage"), data.get("barriers")
    for name, obj in (("coverage", coverage), ("barriers", barriers)):
        if not isinstance(obj, dict):
            err["json"].append(f"{name} must be an object keyed by dimension")
        else:
            unknown = sorted(set(obj) - set(DIMENSION_SLOTS))
            if unknown:  # a confirmed obstacle under an unknown key would silently vanish from every output
                err["json"].append(f"{name} has unknown dimension(s) {unknown}; use {', '.join(DIMENSION_SLOTS)}")
    coverage = coverage if isinstance(coverage, dict) else {}
    barriers = barriers if isinstance(barriers, dict) else {}
    for dim in DIMENSION_SLOTS:
        items = barriers.get(dim, [])
        if not isinstance(items, list):
            err["json"].append(f"barriers.{dim} must be a list")
            items = []
        for i, b in enumerate(items):
            if not isinstance(b, dict):
                err["json"].append(f"barriers.{dim}[{i}] must be an object")
                continue
            for flag in ("confirmed", "selected"):
                if flag in b and not isinstance(b[flag], bool):
                    err["json"].append(f"barriers.{dim}[{i}].{flag} must be true or false, got {b[flag]!r}")
        items = [b for b in items if isinstance(b, dict)]
        confirmed = [b for b in items if b.get("confirmed") is True]
        state = coverage.get(dim)
        if state is None:
            err["coverage"].append(f"coverage.{dim} missing")
        elif state not in COVERAGE_STATES:
            err["coverage"].append(f"coverage.{dim} is {state!r}, expected one of {', '.join(COVERAGE_STATES)}")
        elif state == "confirmed" and not confirmed:
            err["coverage"].append(f"coverage.{dim} is 'confirmed' but no barrier is confirmed")
        elif state != "confirmed" and confirmed:
            err["coverage"].append(f"coverage.{dim} is {state!r} but {len(confirmed)} barrier(s) are confirmed")
        sel = [b for b in items if b.get("selected") is True]
        if len(sel) > 3:
            err["max3"].append(f"{dim}: {len(sel)} selected barriers (max 3)")
        if confirmed and not sel:
            warns.append(f"{dim}: {len(confirmed)} confirmed barrier(s) but none selected for the diagram")
        if dim == "performance":  # every confirmed note, backups too: the criteria step uses all of them
            for b in confirmed:
                text = str(b.get("sticky_text") or "").strip()
                if any(w in text for w in OMISSION_WORDS) and not any(w in text for w in PERFORMANCE_WORDS):
                    warns.append(f"{CLASSIFY_WARNING} {text}")
        for b in sel:
            text = str(b.get("sticky_text") or "").strip()
            if b.get("confirmed") is not True:
                err["confirmed"].append(f"{dim}: selected barrier not confirmed: {text or '?'}")
            if not text:
                err["confirmed"].append(f"{dim}: selected barrier has empty sticky_text")
            elif display_width(text) > STICKY_SOFT_LIMIT:
                warns.append(f"{dim}: sticky text is {display_width(text):g} CJK characters wide (>{STICKY_SOFT_LIMIT}), "
                             f"may overflow; split it into two notes: {text}")

    return err, warns


def _report_lines(result: dict) -> List[str]:
    lines = ["=" * 60, "FISHBONE DIAGRAM VALIDATION REPORT", "=" * 60, "",
             f"Overall: {'PASS' if result['success'] else 'FAIL'}",
             f"Output:  {result.get('output_path') or 'N/A'}", ""]
    if result["validation_errors"]:
        lines.append("ERRORS:")
        lines += [f"  ✗ {e}" for e in result["validation_errors"]]
    else:
        lines.append("All checks passed.")
    if result.get("warnings"):
        lines += ["", "WARNINGS:"] + [f"  ! {w}" for w in result["warnings"]]
    lines += ["", "Checks performed:"]
    for key, text in CHECKS:
        lines.append(f"  [{result['checks'].get(key, 'SKIP')}] {text}")
    return lines


def write_validation_report(result: dict, report_path) -> str:
    Path(report_path).write_text("\n".join(_report_lines(result)) + "\n", encoding="utf-8")
    return str(report_path)


def _finish(err: Dict[str, List[str]], warns: List[str], output_path: Optional[str], ran: set) -> dict:
    errors = [f"{k.upper()}: {e}" for k, _ in CHECKS for e in err.get(k, [])]
    checks = {k: ("FAIL" if err.get(k) else "PASS") if k in ran else "SKIP" for k, _ in CHECKS}
    return {"success": not errors and all(v == "PASS" for v in checks.values()), "output_path": output_path,
            "validation_errors": errors, "warnings": warns, "checks": checks}


def generate_fishbone(json_path, template_path=TEMPLATE_PATH, output_path=None) -> dict:
    """Fill the template; returns {success, output_path, validation_errors, warnings, checks}."""
    output_path = str(output_path or Path(json_path).with_name("fishbone.drawio"))
    err: Dict[str, List[str]] = {k: [] for k, _ in CHECKS}
    json_keys = {"json", "status", "metadata", "coverage", "confirmed", "max3"}
    try:
        data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        err["json"].append(f"cannot read {json_path}: {e}")
        return _finish(err, [], None, {"json"})
    jerr, warns = validate_json(data)
    for k, v in jerr.items():
        err[k] += v
    try:
        tree = ET.parse(str(template_path))
    except (OSError, ET.ParseError) as e:
        err["template"].append(f"cannot read template {template_path}: {e}")
        return _finish(err, warns, None, json_keys | {"template"})
    root = tree.getroot()
    _, terr = validate_template(root)
    err["template"] += terr
    if any(err[k] for k in json_keys | {"template"}):
        return _finish(err, warns, None, json_keys | {"template"})

    original = {cid: _cells(root, cid)[0].get("value", "") for cid in ALL_REQUIRED_IDS}
    removed = drop_stale_layers(root)
    meta = data["metadata"]
    for field, cid in META_CELLS.items():
        _cells(root, cid)[0].set("value", cell_text(meta.get(field)))
    expected: Dict[str, str] = {}
    for dim, slots in DIMENSION_SLOTS.items():
        sel = selected_of(data, dim)
        for i, cid in enumerate(slots):
            value = cell_text(sel[i]["sticky_text"]) if i < len(sel) else ""
            _cells(root, cid)[0].set("value", value)
            expected[cid] = value
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    tree.write(output_path, encoding="unicode", xml_declaration=True)

    ran = set(k for k, _ in CHECKS)
    if os.path.getsize(output_path) == 0:
        err["nonempty"].append(f"{output_path} is empty")
    try:
        out_root = ET.parse(output_path).getroot()
    except ET.ParseError as e:
        err["xml"].append(f"output XML invalid: {e}")
        return _finish(err, warns, output_path, ran)
    for cid in ALL_REQUIRED_IDS:  # unchanged from the template, or exactly a placeholder (a real note may say 请填写)
        val = _cells(out_root, cid)[0].get("value", "")
        if val and (val == original[cid] or html.unescape(val) in PLACEHOLDER_TEXTS):
            err["placeholder"].append(f"placeholder remains in {cid}: {val!r}")
    stale = [c.get("id") for c in out_root.iter("mxCell") if c.get("id") not in KEEP_CELLS
             and not str(c.get("id") or "").startswith(CURRENT_LAYER_PREFIX)]
    if stale:
        err["layers"].append(f"{len(stale)} cell(s) of older template layers remain, e.g. {stale[:3]}")
    target = set(ALL_REQUIRED_IDS)
    for c in out_root.iter("mxCell"):
        val = c.get("value", "")
        if c.get("id") not in target and any(m in val for m in PLACEHOLDER_MARKS):
            err["layers"].append(f"placeholder text in {c.get('id')}: {val!r}")
    if not removed and stale:
        err["layers"].append("the stale layers were not found where expected; check the template")
    for field, cid in META_CELLS.items():
        if _cells(out_root, cid)[0].get("value", "") != cell_text(meta.get(field)):
            err["filled"].append(f"metadata.{field} not written to {cid}")
    for dim, slots in DIMENSION_SLOTS.items():
        for i, cid in enumerate(slots):
            got, want = _cells(out_root, cid)[0].get("value", ""), expected[cid]
            if want and got != want:
                err["filled"].append(f"{dim}[{i}]: expected {want!r}, got {got!r}")
            if not want and got:
                err["empty"].append(f"{dim} slot {i + 1} ({cid}) should be empty, has {got!r}")
    return _finish(err, warns, output_path, ran)


def drop_stale_layers(root) -> int:
    """Remove every cell that is not in the current editable layer (ids 0 / 1 stay); returns how many were removed."""
    n = 0
    for parent in list(root.iter()):
        for child in list(parent):
            if child.tag == "mxCell" and child.get("id") not in KEEP_CELLS \
                    and not str(child.get("id") or "").startswith(CURRENT_LAYER_PREFIX):
                parent.remove(child)
                n += 1
    return n


def _out_dir(arg: str) -> Path:
    p = Path(arg)
    return p.parent if p.name == "fishbone-data.json" else p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fill the AgentDLC fishbone drawio template from fishbone-data.json")
    ap.add_argument("out_dir", nargs="?", help="session directory holding fishbone-data.json (outputs/fishbone/<group>-<date>)")
    ap.add_argument("--template", default=str(TEMPLATE_PATH), help=argparse.SUPPRESS)
    ap.add_argument("--check-template", action="store_true", help="only verify the template's cell IDs")
    ap.add_argument("--check-json", action="store_true", help="only validate fishbone-data.json (writes nothing)")
    a = ap.parse_args(argv)
    if a.check_template:
        try:
            ok, errors = validate_template(ET.parse(a.template).getroot())
        except (OSError, ET.ParseError) as e:
            ok, errors = False, [f"cannot read template {a.template}: {e}"]
        print(json.dumps({"success": ok, "template": a.template, "validation_errors": errors}, ensure_ascii=False, indent=2))
        return 0 if ok else 1
    if not a.out_dir:
        ap.print_usage(sys.stderr)
        return 2
    d = _out_dir(a.out_dir)
    if a.check_json:
        try:
            data = json.loads((d / "fishbone-data.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(json.dumps({"success": False, "validation_errors": [f"JSON: {e}"]}, ensure_ascii=False, indent=2))
            return 1
        err, warns = validate_json(data)
        errors = [f"{k.upper()}: {e}" for k, v in err.items() for e in v]
        print(json.dumps({"success": not errors, "validation_errors": errors, "warnings": warns}, ensure_ascii=False, indent=2))
        return 0 if not errors else 1
    result = generate_fishbone(d / "fishbone-data.json", a.template, d / "fishbone.drawio")
    write_validation_report(result, d / "validation-report.txt")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
