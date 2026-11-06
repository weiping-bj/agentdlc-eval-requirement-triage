"""Shared helpers for data/tools scripts. Standard library only (python3 >= 3.9)."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

TOOLS = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.dirname(TOOLS)
REPO = os.path.dirname(DATA)
GOLDEN_DIR = os.path.join(DATA, "golden")
GOLDEN_PATH = os.path.join(GOLDEN_DIR, "golden_set.jsonl")
FEEDBACK_PATH = os.path.join(DATA, "feedback_last_month.jsonl")
CATALOG_PATH = os.path.join(DATA, "capability_catalog.json")
POOL_PATH = os.path.join(TOOLS, "seed", "pool.jsonl")

INPUT_FIELDS = ["id", "title", "content", "customer", "industry", "customer_tier", "channel", "module",
                "submitted_at", "feedback_type", "contract_commitment"]
REQUIRED_FIELDS = ["id", "title", "content"]
RECOMMENDED_FIELDS = ["customer", "customer_tier", "module", "submitted_at", "feedback_type"]
OPTIONAL_FIELDS = ["industry", "channel", "contract_commitment"]
YES_NO = ("是", "否")
DECISIONS = ("迭代", "拒绝", "待评估")
LEVELS = ("P0", "P1", "P2")
DIMENSIONS = ("认知", "质量", "责任", "成本", "性能")
CASE_TYPES = ("简单", "模糊", "对抗", "应拒绝")
TIERS = ("战略", "KA", "腰部", "长尾")
FEEDBACK_TYPES = ("新需求", "功能缺陷", "体验优化", "咨询")


def templates_dir() -> str:
    """data/templates (default) or repo-root templates/ if the lead moved them there."""
    for d in (os.path.join(DATA, "templates"), os.path.join(REPO, "templates")):
        if os.path.exists(os.path.join(d, "discussion-feedback.md")):
            return d
    return os.path.join(DATA, "templates")


# ---------------------------------------------------------------- file IO
def read_jsonl(path: str) -> List[Dict[str, Any]]:
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as e:
                raise ValueError(f"{path}:{i}: 不是合法 JSON：{e}") from e
    return rows


def write_jsonl(path: str, rows: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _unescape_cell(v: str) -> str:
    return v[1:] if len(v) > 1 and v[0] == "'" and v[1] in _FORMULA_START else v


def _escape_cell(v: Any) -> Any:
    """Prevent Excel from evaluating customer text as a formula (CSV injection / '#NAME?' mangling)."""
    if isinstance(v, str) and v[:1] in _FORMULA_START:
        return "'" + v
    return v


def read_text_any(path: str) -> str:
    """UTF-8 (with/without BOM) first, then GB18030 (Excel "CSV" on Chinese Windows)."""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{path}: 无法识别文件编码，请在 Excel 中“另存为 → CSV UTF-8（逗号分隔）”后重试")


def read_csv(path: str) -> List[Dict[str, str]]:
    text = read_text_any(path)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t;")
        # Excel always writes "" for a quote inside a quoted cell; a quote-free 4 KB sample sniffs as
        # doublequote=False and later cells with quotes would split into the next columns.
        dialect.doublequote = True
    except csv.Error:
        dialect = csv.excel
    return [{(k or "").strip(): _unescape_cell((v or "").strip()) for k, v in row.items() if k is not None}
            for row in csv.DictReader(io.StringIO(text), dialect=dialect)]


def write_csv(path: str, rows: List[Dict[str, Any]], fields: List[str]) -> None:
    """UTF-8 with BOM so Excel opens Chinese correctly."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else _escape_cell(r.get(k))) for k in fields})


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_RNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref or "A").group(0)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def read_xlsx(path: str, sheet: Optional[str] = None) -> List[Dict[str, str]]:
    """Minimal .xlsx reader (no openpyxl): first sheet (or by name), first row = header."""
    z = zipfile.ZipFile(path)
    names = z.namelist()
    shared: List[str] = []
    if "xl/sharedStrings.xml" in names:
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", _NS):
            shared.append("".join(t.text or "" for t in si.iter("{%s}t" % _NS["m"])))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rid_target = {r.get("Id"): r.get("Target") for r in rels}
    sheets = [(s.get("name"), s.get("{%s}id" % _RNS)) for s in wb.find("m:sheets", _NS)]
    if not sheets:
        return []
    chosen = sheets[0]
    if sheet:
        match = [s for s in sheets if s[0] == sheet]
        if not match:  # never fall back to the first sheet silently: that reads the wrong table
            raise ValueError(f"找不到工作表“{sheet}”。文件里的工作表：{'、'.join(str(s[0]) for s in sheets)}")
        chosen = match[0]
    target = rid_target.get(chosen[1], "worksheets/sheet1.xml").lstrip("/")
    target = target if target.startswith("xl/") else "xl/" + target
    root = ET.fromstring(z.read(target))
    grid: List[List[str]] = []
    for r in root.find("m:sheetData", _NS):
        row: Dict[int, str] = {}
        nxt = 0
        for c in r:
            ref = c.get("r")
            idx = _col_index(ref) if ref else nxt  # the cell reference is optional in OOXML
            nxt = idx + 1
            t = c.get("t")
            v = c.find("m:v", _NS)
            if t == "inlineStr":
                isv = c.find("m:is", _NS)
                val = "".join(x.text or "" for x in isv.iter("{%s}t" % _NS["m"])) if isv is not None else ""
            elif v is None:
                val = ""
            elif t == "s":
                val = shared[int(v.text)]
            else:
                val = v.text or ""
            row[idx] = val
        if row:
            width = max(row) + 1
            grid.append([row.get(i, "") for i in range(width)])
    if not grid:
        return []
    header = [h.strip() for h in grid[0]]
    out = []
    for g in grid[1:]:
        if not any(x.strip() for x in g):
            continue
        out.append({header[i]: (g[i].strip() if i < len(g) else "") for i in range(len(header)) if header[i]})
    return out


def read_any(path: str, sheet: Optional[str] = None) -> List[Dict[str, Any]]:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        return read_xlsx(path, sheet)
    if ext == ".xls":
        raise ValueError("不支持旧版 .xls，请在 Excel 中“另存为 .xlsx 或 .csv”后再试。")
    if ext in (".csv", ".tsv", ".txt"):
        return read_csv(path)
    if ext == ".jsonl":
        return read_jsonl(path)
    if ext == ".json":
        with open(path, encoding="utf-8-sig") as f:
            doc = json.load(f)
        if isinstance(doc, dict):
            for k in ("items", "data", "records", "feedback", "rows"):
                if isinstance(doc.get(k), list):
                    return doc[k]
            return [doc]
        return list(doc)
    raise ValueError(f"不支持的文件类型：{ext}（支持 .csv/.xlsx/.json/.jsonl）")


# ---------------------------------------------------------------- tiny YAML (subset) for our templates
def _scalar(v: str) -> Any:
    v = v.strip()
    if v == "" or v in ("~", "null", "None"):
        return None
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        return v[1:-1]
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [_scalar(x) for x in inner.split(",")] if inner else []
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def _strip_comment(line: str) -> str:
    out, q = [], None
    for ch in line:
        if q:
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
        elif ch == "#":
            break
        out.append(ch)
    return "".join(out).rstrip()


def load_yaml(path: str) -> Dict[str, Any]:
    """PyYAML if installed, else a subset parser (nested maps by indentation, '- ' lists, scalars)."""
    try:
        import yaml  # type: ignore
        with open(path, encoding="utf-8-sig") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        pass
    with open(path, encoding="utf-8-sig") as f:
        lines = [(len(l) - len(l.lstrip(" ")), _strip_comment(l).strip()) for l in f.read().splitlines()]
    lines = [(i, t) for i, t in lines if t]
    pos = 0

    def parse(indent: int):
        nonlocal pos
        if pos < len(lines) and lines[pos][1].startswith("- "):
            arr = []
            while pos < len(lines) and lines[pos][0] == indent and lines[pos][1].startswith("- "):
                arr.append(_scalar(lines[pos][1][2:]))
                pos += 1
            return arr
        obj: Dict[str, Any] = {}
        while pos < len(lines) and lines[pos][0] == indent:
            text = lines[pos][1]
            if ":" not in text:
                raise ValueError(f"{path}: 无法解析的行：{text}")
            k, v = text.split(":", 1)
            pos += 1
            if v.strip() == "":
                if pos < len(lines) and (lines[pos][0] > indent or
                                         (lines[pos][0] == indent and lines[pos][1].startswith("- "))):
                    obj[k.strip()] = parse(lines[pos][0])
                else:
                    obj[k.strip()] = None
            else:
                obj[k.strip()] = _scalar(v)
        return obj

    return parse(lines[0][0]) if lines else {}


# ---------------------------------------------------------------- value normalisation
def norm_yes_no(v: Any) -> Optional[str]:
    if isinstance(v, bool):
        return "是" if v else "否"
    s = str(v if v is not None else "").strip().lower()
    if s in ("是", "y", "yes", "true", "1", "重要", "紧急", "有", "√", "✓", "可", "可以", "允许"):
        return "是"
    if s in ("否", "n", "no", "false", "0", "不重要", "不紧急", "无", "×", "x", "不", "不可", "不能", "不可以", "不允许"):
        return "否"
    return None


_NEGATION = re.compile(r"(不|暂不|不予|无需|无须|无法|没有|won'?t|not\b|^un|n't)")


def norm_decision(v: Any) -> Optional[str]:
    """迭代 / 拒绝 / 待评估, or None when unknown or negated (e.g. 不建议迭代, 暂不开发 -> None, reported by validation)."""
    s = str(v if v is not None else "").strip().lower()
    if not s:
        return None
    table = [("待评估", ("待评估", "需评估", "评估", "待定", "pending", "evaluate", "澄清")),
             ("拒绝", ("拒绝", "reject", "rejected", "不做", "驳回", "不予采纳")),
             ("迭代", ("迭代", "iterate", "accept", "纳入", "排期", "开发", "修复", "接受"))]
    for canon, words in table:
        if s == canon or s in words:
            return canon
    if _NEGATION.search(s):
        return None
    for canon, words in table:
        if any(w in s for w in words):
            return canon
    return None


def norm_level(v: Any) -> Optional[str]:
    s = str(v if v is not None else "").strip().upper()
    if s in LEVELS:
        return s
    if s in ("0", "1", "2"):
        return "P" + s
    m = {"高": "P0", "中": "P1", "低": "P2", "HIGH": "P0", "MEDIUM": "P1", "LOW": "P2"}
    return m.get(s)


def norm_date(v: Any) -> Optional[str]:
    if v in (None, ""):
        return None
    s = str(v).strip()
    if re.fullmatch(r"\d{5}(\.\d+)?", s):  # Excel serial
        return (date(1899, 12, 30) + timedelta(days=int(float(s)))).isoformat()
    s2 = s.replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-").replace(".", "-")
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s2)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    for fmt in ("%Y%m%d", "%d-%m-%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(s2[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def derive_level(important: Optional[str], urgent: Optional[str]) -> str:
    if important == "是" and urgent == "是":
        return "P0"
    if important == "是":
        return "P1"
    return "P2"


LIST_SEPS = r"[；;|\n]+"          # for free-text points (may contain "/")
TAG_SEPS = r"[/／、,，；;|\s]+"     # for short tags (dimensions, acceptable decisions)


def split_list(v: Any, seps: str = LIST_SEPS) -> List[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v or "").strip()
    if not s:
        return []
    return [p.strip() for p in re.split(seps, s) if p.strip()]
