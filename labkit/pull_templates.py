"""Chapter 2.0: copy the morning's filled-in templates from the lab S3 bucket into data/templates/.

At 11:00 the EC2 does not exist yet and it has no inbound ports, so the group's recorder edits
the two template files on a laptop and, in the afternoon, drags them into the participant's
private s12lab bucket in the S3 console. This module brings them onto the EC2.

  python -m labkit.pull_templates --prepare     # create the private bucket if missing, print name + console link
  python -m labkit.pull_templates               # copy the two files into data/templates/
  python -m labkit.pull_templates --bucket s12lab-zhang3-<account>-us-west-2   # a group-mate's bucket
  python -m labkit.pull_templates --dry-run     # show what would happen, write nothing
  python -m labkit.pull_templates --force       # also replace an EC2-edited file with an unedited upload

Safety: only the two allowlisted file names are ever written, always into data/templates/ (the
object key in the bucket never becomes a path). An existing file is backed up to
data/templates/.backup/<timestamp>/ before it is replaced. Files are stored as UTF-8 (the CSV
with a BOM and LF line ends so Excel and the data tools both read it), whatever encoding Excel /
Notepad saved them in. Only buckets owned by this account are read (ExpectedBucketOwner).
Uses the EC2 instance role (or AWS_PROFILE off EC2); no keys.
"""
from __future__ import annotations

import argparse
import codecs
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ("discussion-feedback.md", "golden-set-feedback.csv")
CSV_REQUIRED = ("golden_id", "操作")
MAX_BYTES = 2_000_000
MAX_CANDIDATES = 30
RECORD = "last-pulled.json"  # in data/templates/.backup/: what this tool wrote last time, per file
BUCKET_RE = re.compile(r"^s12lab-[a-z0-9][a-z0-9-]{1,54}[a-z0-9]$")
BEIJING = timezone(timedelta(hours=8))  # attendees' wall clock; the EC2 itself runs in UTC


class PullError(RuntimeError):
    pass


# ----------------------------------------------------------------------------- pure helpers
def normalize_bucket(arg: str) -> str:
    b = (arg or "").strip()
    if b.startswith("s3://"):
        b = b[len("s3://"):]
    b = b.rstrip("/")
    if not BUCKET_RE.match(b):
        raise PullError(f"桶名 {arg!r} 不对：只能是实验桶（以 s12lab- 开头，例如 s12lab-zhang3-123456789012-us-west-2）")
    return b


def console_url(bucket: str, region: str) -> str:
    return f"https://{region}.console.aws.amazon.com/s3/buckets/{bucket}?region={region}&tab=objects"


def decode(raw: bytes):
    """Return (text, encoding label). UTF-16 (BOM) / UTF-8 (with or without BOM) / GB18030 (GBK)."""
    if raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
        return raw.decode("utf-16"), "UTF-16"
    try:
        return raw.decode("utf-8-sig"), "UTF-8"
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("gb18030"), "GBK"
    except UnicodeDecodeError as e:
        raise PullError("无法识别文件编码；请在 Excel 中“另存为 → CSV UTF-8（逗号分隔）”后重新上传") from e


def normalize_text(name: str, text: str) -> str:
    """CSV: one line-end style (LF). Classic Mac 'CSV' ends lines with CR only, which Python's csv
    module (and data/tools/common.read_csv) rejects with 'new-line character seen in unquoted field'."""
    text = text.lstrip("\ufeff")
    if name.endswith(".csv"):
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def to_utf8(name: str, text: str) -> bytes:
    body = text.encode("utf-8")
    return (codecs.BOM_UTF8 + body) if name.endswith(".csv") else body


def _csv_rows(text: str):
    text = normalize_text(".csv", text)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t;")
        dialect.doublequote = True  # Excel writes ""; a quote-free sample would sniff False (same fix as common.read_csv)
    except csv.Error:
        dialect = csv.excel
    try:
        return list(csv.reader(io.StringIO(text), dialect=dialect))
    except csv.Error as e:
        raise PullError(f"CSV 格式读不出来（{e}）：请在 Excel 里“另存为 → CSV UTF-8（逗号分隔）”后重新上传") from e


def comparable(name: str, text: str):
    """Content view that ignores what Excel / editors change on a plain Save (encoding, CRLF,
    quoting, trailing blanks), so 'opened and saved without edits' still counts as unchanged."""
    text = text.lstrip("\ufeff")
    if name.endswith(".csv"):
        out = []
        for row in _csv_rows(text):
            cells = [c.strip() for c in row]
            while cells and not cells[-1]:
                cells.pop()
            if cells:
                out.append(tuple(cells))
        return out
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()


def csv_check(text: str):
    """Return Counter of the 操作 column; raise PullError when this is not the feedback table."""
    rows = _csv_rows(text)
    header = [h.strip() for h in rows[0]] if rows else []
    missing = [c for c in CSV_REQUIRED if c not in header]
    if missing:
        if "golden_id" in header and any("?" in h or "\ufffd" in h for h in header):
            raise PullError("表头里的中文丢了（保存时没有用 UTF-8 / GBK，中文变成了问号或乱码）："
                            "请在 Excel 里“另存为 → CSV UTF-8（逗号分隔）”后重新上传")
        if "golden_id" in header:
            raise PullError("表头被改了（第一行找不到“操作”列）：请保持第一行表头和下载时一样，再重新上传")
        raise PullError(f"表头缺少 {'、'.join(missing)} 列，不像黄金集反馈表（请上传从仓库下载、填好的那份 CSV）")
    i = header.index("操作")
    ops: Counter = Counter()
    for r in rows[1:]:
        if any(c.strip() for c in r):
            ops[(r[i].strip() if i < len(r) else "") or "（空）"] += 1
    return ops


def match_objects(objects, name: str):
    """Split bucket objects into (exact, near, other) for one template name, newest first.
    near  = right type, renamed copy: 'golden-set-feedback (1).csv', '… - 副本.csv', '第3组-….csv',
            '….csv.txt' (Notepad / browsers add .txt)
    other = contains the template name but is another file type: '.xlsx', '.docx', …"""
    stem, ext = os.path.splitext(name)
    exact, near, other = [], [], []
    for o in objects:
        key = o["Key"]
        if key.endswith("/"):
            continue
        base = key.rsplit("/", 1)[-1]
        low = base.lower()
        if base == name:
            exact.append(o)
        elif stem.lower() in low and (low.endswith(ext) or low.endswith(ext + ".txt")):
            near.append(o)
        elif stem.lower() in low:
            other.append(o)
    newest = lambda lst: sorted(lst, key=lambda o: (o["LastModified"], o["Key"]), reverse=True)  # noqa: E731
    return newest(exact), newest(near), newest(other)


def safe_dest(templates_dir: Path, root: Path, name: str) -> Path:
    if name not in TEMPLATES:
        raise PullError(f"拒绝写入 {name!r}：只允许 {', '.join(TEMPLATES)}")
    tdir = templates_dir.resolve()
    if tdir.parent.name != "data" or tdir.name != "templates" or root.resolve() not in tdir.parents:
        raise PullError(f"拒绝写入：{templates_dir} 不是本仓库的 data/templates/")
    dest = templates_dir / name
    if dest.is_symlink() or (dest.exists() and dest.resolve().parent != tdir):
        raise PullError(f"拒绝写入：{dest} 是链接或指向 data/templates/ 之外")
    return dest


PLACEHOLDER = "【填写】"
PLACEHOLDER_RX = re.compile(r"【填写[^】]*】")  # also "【填写，例如……】"
# answers that mean "no change wanted"; compared after dropping spaces/punctuation
NO_CHANGE = {"基本符合", "符合", "符合实际", "基本符合实际", "符合没意见", "基本符合没意见", "差不多", "基本一致", "一致",
             "无", "没有", "暂无", "无意见", "没意见", "没有意见", "无补充", "不用改", "不需要", "不需要改", "同意", "可以",
             "ok", "-", "—", "/"}


def _no_change(value: str) -> bool:
    v = re.sub(r"[\s。.，,！!；;：:、]", "", PLACEHOLDER_RX.sub("", value)).lower()
    return v == "" or v in NO_CHANGE


def md_section(text: str, n: int):
    """Body of '## <n>.' (title line excluded) up to the next '## ' heading, or None."""
    m = re.search(rf"^## {n}\.[^\n]*\n(.*?)(?=^## |\Z)", text.replace("\r\n", "\n").replace("\r", "\n"), re.S | re.M)
    return m.group(1) if m else None


def _answer(line: str) -> str:
    """Text after the LAST full-width colon (the question part may hold a link "https://...")."""
    return (line.rsplit("：", 1)[-1] if "：" in line else line.rsplit(":", 1)[-1] if ":" in line else line).strip()


def section_answered(text: str, default_text, n: int, prose: bool) -> bool:
    """True when section n of discussion-feedback.md has an answer:
    - a bullet whose answer is not a placeholder / "基本符合"-type reply, or an added bullet;
    - a table data row (below the |---| line) with any non-placeholder cell;
    - a "问题：【填写…】" line of the repo default whose answer changed;
    - with prose=True and a known repo default: any other non-empty line the default does not have.
    Without the repo default only bullets and table rows count."""
    sec = md_section(text, n)
    if sec is None:
        return False
    dsec = md_section(default_text, n) if default_text is not None else None
    known = {line.strip() for line in (dsec or "").splitlines()}
    questions = {line.strip().rsplit("：", 1)[0] for line in (dsec or "").splitlines()
                 if "：" in line and PLACEHOLDER_RX.search(line.rsplit("：", 1)[-1])}
    in_table = False
    for line in (x.strip() for x in sec.splitlines()):
        if not line:
            in_table = False
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                in_table = True          # separator: data rows follow
            elif in_table and any(c and not _no_change(c) for c in cells):
                return True
            continue
        in_table = False
        if line.startswith("- "):
            if not _no_change(_answer(line[2:])):
                return True
        elif "：" in line and line.rsplit("：", 1)[0] in questions:
            if not _no_change(_answer(line)):
                return True
        elif prose and dsec is not None and line not in known and not PLACEHOLDER_RX.search(line) and not _no_change(line):
            return True
    return False


def mock_feedback_given(text: str, default_text) -> bool:
    """Section 5 (mock data) answered -> 2.4 runs before 2.5."""
    return section_answered(text, default_text, 5, prose=True)


def catalog_feedback_given(text: str, default_text) -> bool:
    """Section 3 (capability catalog) answered -> prompt 04 runs before prompt 03."""
    return section_answered(text, default_text, 3, prose=False)


def git_default(root: Path, name: str):
    """The template as committed in the repo (HEAD), or None when git is not available."""
    try:
        r = subprocess.run(["git", "-C", str(root), "show", f"HEAD:data/templates/{name}"],
                           capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _kb(n: int) -> str:
    return f"{n / 1024:.1f} KB"


def _when(o) -> str:
    t = o.get("LastModified")
    if isinstance(t, datetime):
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t.astimezone(BEIJING).strftime("%m-%d %H:%M 北京时间")
    return str(t or "")


class _Cand:
    def __init__(self, obj, raw: bytes, text: str, enc: str):
        self.obj, self.key, self.raw, self.text, self.enc = obj, obj["Key"], raw, text, enc


# ----------------------------------------------------------------------------- the pull
class Puller:
    def __init__(self, s3, bucket: str, root: Path = ROOT, templates_dir=None, now=None,
                 default_reader=git_default, dry_run: bool = False, force: bool = False,
                 owner=None, out=print):
        self.s3, self.bucket, self.root = s3, bucket, root
        self.tdir = templates_dir or (root / "data" / "templates")
        self.now = now or datetime.now()
        self.default_reader, self.dry_run, self.force, self.out = default_reader, dry_run, force, out
        self.kw = {"ExpectedBucketOwner": owner} if owner else {}
        self.warnings = 0
        self._backup_dir = None

    def warn(self, msg: str):
        self.warnings += 1
        self.out("⚠️  " + msg)

    def list_objects(self):
        objs = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, **self.kw):
            objs += page.get("Contents", [])
        return objs

    def backup_dir(self) -> Path:
        if self._backup_dir is None:
            root = self.tdir / ".backup"
            if root.is_symlink() or (root.exists() and root.resolve().parent != self.tdir.resolve()):
                raise PullError(f"拒绝写入：{root} 是链接或指向 data/templates/ 之外")
            base = root / self.now.strftime("%Y%m%d-%H%M%S")
            d, k = base, 2
            while d.exists():
                d, k = base.with_name(f"{base.name}-{k}"), k + 1
            self._backup_dir = d
        return self._backup_dir

    def _record_path(self) -> Path:
        root = self.tdir / ".backup"
        if root.is_symlink() or (root.exists() and root.resolve().parent != self.tdir.resolve()):
            raise PullError(f"拒绝写入：{root} 是链接或指向 data/templates/ 之外")
        return root / RECORD

    def load_record(self) -> dict:
        p = self._record_path()
        if p.is_symlink():
            raise PullError(f"拒绝读取：{p} 是链接")
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except (OSError, ValueError):
            return {}

    def save_record(self, name: str, data: bytes, key: str):
        if self.dry_run:
            return
        p = self._record_path()
        rec = self.load_record()
        rec[name] = {"sha256": hashlib.sha256(data).hexdigest(), "key": key,
                     "at": self.now.isoformat(timespec="seconds")}
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{RECORD}.tmp")
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)

    def rel(self, p: Path) -> str:
        try:
            return str(p.relative_to(self.root))
        except ValueError:
            return str(p)

    def fetch(self, name: str, o):
        """Download + decode + sanity-check one candidate; return _Cand or an error string."""
        size = int(o.get("Size") or 0)
        if size > MAX_BYTES:
            return f"{o['Key']} 有 {_kb(size)}，太大了，不像模板文件"
        raw = self.s3.get_object(Bucket=self.bucket, Key=o["Key"], **self.kw)["Body"].read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES or not raw.strip():
            return f"{o['Key']} 为空或太大"
        try:
            text, enc = decode(raw)
            text = normalize_text(name, text)
            if name.endswith(".csv"):
                csv_check(text)
        except PullError as e:
            return f"{o['Key']}：{e}"
        return _Cand(o, raw, text, enc)

    def one(self, name: str, objects) -> str:
        """Process one template; returns 'copied' | 'same' | 'kept' | 'missing' | 'error'."""
        p = self.out
        dest = safe_dest(self.tdir, self.root, name)
        exact, near, other = match_objects(objects, name)
        ext = os.path.splitext(name)[1]
        if not exact and not near:
            self.warn(f"{name}：桶里没找到这个文件 → EC2 上的 {self.rel(dest)} 保持不变")
            for o in other:
                p(f"     找到 {o['Key']}，但文件类型不对（需要 {ext}）："
                  f"{'在 Excel 里“另存为 → CSV UTF-8（逗号分隔）”' if ext == '.csv' else '用记事本另存为 .md'}，"
                  f"文件名用 {name}，重新上传")
            return "missing"
        default_raw = self.default_reader(self.root, name)
        try:
            default_view = comparable(name, decode(default_raw)[0]) if default_raw is not None else None
        except PullError:
            default_view = None
        is_default = lambda text: default_view is not None and comparable(name, text) == default_view  # noqa: E731

        if len(exact) + len(near) > MAX_CANDIDATES:
            p(f"❌ {name}：桶里有 {len(exact) + len(near)} 个同名或名字相近的文件，太多了；请在 S3 控制台删掉多余的副本，只留一份")
            return "error"
        cands, errors = [], []
        for o in exact + near:
            c = self.fetch(name, o)
            (cands if isinstance(c, _Cand) else errors).append(c)
        if not cands:
            for e in errors:
                p(f"❌ {name}：{e}；未拷贝")
            return "error"
        for e in errors:
            self.warn(f"{name}：跳过 {e}")
        edited = [c for c in cands if not is_default(c.text)]
        views = []
        for c in edited:
            v = comparable(name, c.text)
            if v not in views:
                views.append(v)
        if len(views) > 1:
            p(f"❌ {name}：桶里有 {len(views)} 份内容不同的改过的版本，不知道该用哪一份；未拷贝：")
            for c in edited:
                p(f"     - {c.key}（{_when(c.obj)}）")
            p("     在 S3 控制台勾选不要的那份 →「删除」，只留一份，再运行本命令")
            return "error"
        pick = edited[0] if edited else cands[0]
        if errors and default_view is not None and is_default(pick.text):
            # the only edited copy (or copies) failed validation: never fall back to an unedited one
            for e in errors:
                p(f"❌ {name}：{e}")
            p(f"❌ {name}：改过的那份读不出来，桶里只剩没改过的模板；未拷贝")
            return "error"
        if pick.obj not in exact:
            self.warn(f"{name}：改用名字相近的 {pick.key}（文件名被改过；下次请保持原名 {name}）")
        for c in cands:
            if c is not pick:
                why = "没改过的" if is_default(c.text) else "内容相同的"
                p(f"     忽略{why} {c.key}（{_when(c.obj)}）")
        for o in other:
            if (o["LastModified"], o["Key"]) > (pick.obj["LastModified"], pick.key):
                self.warn(f"{name}：桶里还有更新的 {o['Key']}（{_when(o)}）没有使用（类型不对）——如果那份才是改好的，"
                          f"请在笔记本上把它存成 {name}（{'CSV UTF-8 格式' if ext == '.csv' else '纯文本 .md'}）重新上传，再运行本命令")

        new = to_utf8(name, pick.text)
        conv = "" if pick.enc == "UTF-8" else f"，{pick.enc} → UTF-8"
        src = f"s3://{self.bucket}/{pick.key}（{_kb(len(pick.raw))}，上传于 {_when(pick.obj)}{conv}）"
        old = dest.read_bytes() if dest.exists() else None
        rec = self.load_record().get(name) or {}
        if old == new:
            p(f"✅ {name}：与 EC2 上现有文件相同，无需拷贝  ← {src}")
            self.save_record(name, new, pick.key)
            status = "same"
        elif old is not None and not self.force and not self._pristine(name, old, rec, is_default, default_view):
            if default_view is None and not rec:
                self.warn(f"{name}：读不到仓库里的默认模板（这个目录不是 git clone 下来的？），无法确认 EC2 上这份有没有被改过 "
                          f"→ 保留 EC2 上的，未拷贝。确认要用桶里的覆盖，加 --force 重跑（旧文件会先备份）")
            elif is_default(pick.text):
                self.warn(f"{name}：桶里这份和仓库默认模板一样（没改过），而 EC2 上这份已经改过 → 保留 EC2 上的，未拷贝。"
                          f"确实要用默认模板覆盖，加 --force 重跑")
            else:
                self.warn(f"{name}：EC2 上这份在上次拷贝之后被改过（例如 coding agent / claude 改的）→ 保留 EC2 上的，未拷贝。"
                          f"确认要用桶里的 {pick.key} 覆盖，加 --force 重跑（旧文件会先备份）")
            return "kept"
        else:
            verb = "将拷贝" if self.dry_run else "已拷贝"
            if not self.dry_run:
                if old is not None:
                    bdir = self.backup_dir()
                    bdir.mkdir(parents=True, exist_ok=True)
                    (bdir / name).write_bytes(old)
                tmp = dest.with_name(f".{name}.tmp")
                if tmp.is_symlink() or tmp.exists():
                    tmp.unlink()  # removes a stale temp file or a planted link itself, never its target
                tmp.write_bytes(new)
                os.replace(tmp, dest)
            p(f"✅ {name}：{verb} → {self.rel(dest)}  ← {src}")
            self.save_record(name, new, pick.key)
            if old is not None:
                p(f"     原文件{'将备份到' if self.dry_run else '已备份到'} {self.rel(self.backup_dir() / name)}")
            status = "copied"
        if name.endswith(".csv"):
            ops = csv_check(pick.text)
            p("     “操作”列：" + " · ".join(f"{k} {v}" for k, v in sorted(ops.items(), key=lambda kv: -kv[1])))
        if is_default(pick.text):
            self.warn(f"{name}：内容和仓库里的默认模板完全一样——是不是改完没保存，或上传了没改过的那份？")
        return status

    @staticmethod
    def _pristine(name, old: bytes, rec: dict, is_default, default_view) -> bool:
        """May the EC2 copy be replaced without --force? Only when nobody edited it on EC2: it is
        exactly what this tool wrote last time, or it still equals the repo default."""
        if rec.get("sha256") == hashlib.sha256(old).hexdigest():
            return True
        if default_view is None:
            return False
        try:
            return is_default(normalize_text(name, decode(old)[0]))
        except PullError:
            return False

    def run(self) -> int:
        p = self.out
        p(f"==> 从 s3://{self.bucket}/ 查找 {' 和 '.join(TEMPLATES)}{'（预览，不写文件）' if self.dry_run else ''}")
        objects = self.list_objects()
        results = {}
        for name in TEMPLATES:
            try:
                results[name] = self.one(name, objects)
            except PullError as e:
                p(f"❌ {e}")
                results[name] = "error"
        ok = [n for n, s in results.items() if s in ("copied", "same", "kept")]
        bad = [n for n, s in results.items() if s == "error"]
        p("")
        if not ok and not bad:
            p("❌ 桶里一个模板都没找到。请在 S3 控制台把两个文件拖进这个桶（不要改文件名），再运行本命令。")
            p(f"   桶：{self.bucket}")
            return 1
        if bad:
            p(f"❌ 有 {len(bad)} 个文件没拷贝（见上面的 ❌），改好后重新上传、重新运行本命令即可。")
            return 1
        if self.warnings:
            p(f"⚠️  完成，但有 {self.warnings} 条提醒（见上面的 ⚠️）：先确认没问题，再做下一步。重复运行是安全的。")
        else:
            p(f"完成：{len(ok)}/{len(TEMPLATES)} 个模板已就位（data/templates/）。重复运行是安全的。")
        if not self.dry_run:
            self.next_steps()
        return 0

    def next_steps(self):
        """Same numbered order every time; 2.4 (mock data) is marked to do only when section 5 was answered."""
        p, md = self.out, self.tdir / "discussion-feedback.md"
        try:
            text = decode(md.read_bytes())[0] if md.is_file() else ""
            raw = self.default_reader(self.root, "discussion-feedback.md")
            default = decode(raw)[0] if raw is not None else None
        except (OSError, PullError):
            text, default = "", None
        mock = mock_feedback_given(text, default)
        catalog = catalog_feedback_given(text, default)
        p("下一步（手册第 2 章，按顺序；2.1/2.2 只读、2.3 只在客户自带数据时做）：")
        if mock:
            p("  1. 2.4 改 mock 数据（模板第 5 节有意见，必须在 2.5 之前）：claude \"$(cat data/prompts/02-regenerate-mock-data.md)\"")
        else:
            p("  1. 2.4 跳过：模板第 5 节没填，或写的是“基本符合”")
        p("  2. 2.5 改黄金集：python3 data/tools/golden_tool.py apply-feedback --dry-run 预览，")
        if catalog:
            p("     模板第 3 节补了能力目录：先 claude \"$(cat data/prompts/04-update-capability-catalog.md)\"，")
        p("     再 claude \"$(cat data/prompts/03-update-golden-set.md)\"")
        p("  3. 2.6 冻结：python3 data/tools/golden_tool.py freeze")
        changed = "mock 数据" if mock and not catalog else "能力目录" if catalog and not mock else "mock 数据和能力目录"
        p("  4. 评估器已注册过：./30-create-evaluators.sh（第 6 章）" + (
            f"；B 版 Runtime 已部署过还要：./20-B-deploy-runtime.sh（B3，把新的 {changed} 打进包）" if (mock or catalog) else ""))


# ----------------------------------------------------------------------------- AWS glue
def _bucket_exists(s3, bucket: str, owner=None) -> bool:
    from botocore.exceptions import ClientError
    try:
        s3.head_bucket(Bucket=bucket, **({"ExpectedBucketOwner": owner} if owner else {}))
        return True
    except ClientError as e:
        code = str(e.response.get("Error", {}).get("Code", ""))
        if code in ("404", "NoSuchBucket", "NotFound"):
            return False
        raise PullError(f"无法访问桶 {bucket}（{code}）：确认桶名正确，并且它在本实验账号、本区域里") from e


_PAB_KEYS = ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")


def ensure_private(s3, bucket: str, owner) -> str:
    """Fail closed: an existing lab bucket must block all public access before we hand out the upload
    link. Missing or partial block -> re-apply all four (the lab bucket is private by design).
    Returns 'ok' | 'reapplied'; raises PullError when the setting cannot be read."""
    from botocore.exceptions import ClientError
    try:
        conf = s3.get_public_access_block(Bucket=bucket, ExpectedBucketOwner=owner)["PublicAccessBlockConfiguration"]
    except ClientError as e:
        code = str(e.response.get("Error", {}).get("Code", ""))
        if code != "NoSuchPublicAccessBlockConfiguration":
            raise PullError(f"读不到桶 {bucket} 的公开访问设置（{code}）：先不要上传，请联系讲师") from e
        conf = {}
    if all(conf.get(k) for k in _PAB_KEYS):
        return "ok"
    s3.put_public_access_block(Bucket=bucket, ExpectedBucketOwner=owner,
                               PublicAccessBlockConfiguration={k: True for k in _PAB_KEYS})
    return "reapplied"


def _aws_hint(e) -> str:
    code = ""
    if hasattr(e, "response"):
        code = str(e.response.get("Error", {}).get("Code", ""))
    name = type(e).__name__
    if code in ("AccessDenied", "403", "AllAccessDisabled") or "AccessDenied" in str(e):
        return "没有权限（AccessDenied）：请在实验 EC2 上运行（实例角色 s12lab-ec2-…），桶名要以 s12lab- 开头、属于本账号"
    if name in ("NoCredentialsError", "PartialCredentialsError") or code in ("ExpiredToken", "InvalidClientTokenId"):
        return "没有可用的 AWS 凭证：请在实验 EC2 上运行（它用实例角色，不需要密钥）"
    if name in ("EndpointConnectionError", "ConnectTimeoutError", "ReadTimeoutError"):
        return "连不上 AWS（网络问题）：稍等一分钟再运行"
    return f"AWS 调用失败（{code or name}）：{e}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tools/pull-templates.sh", description=__doc__.split("\n")[0])
    ap.add_argument("--prepare", action="store_true", help="创建你的私有桶（已存在则跳过），打印桶名和控制台链接")
    ap.add_argument("--bucket", help="从指定的实验桶拉取（同组多台 EC2 时用操作员的桶）")
    ap.add_argument("--dry-run", action="store_true", help="只显示会做什么，不写文件")
    ap.add_argument("--force", action="store_true", help="桶里是没改过的默认模板时，也覆盖 EC2 上已改过的文件")
    a = ap.parse_args(argv)
    if a.prepare and (a.bucket or a.dry_run or a.force):
        print("❌ --prepare 只创建你自己的桶，不能和 --bucket / --dry-run / --force 一起用", file=sys.stderr)
        return 1
    from botocore.exceptions import BotoCoreError, ClientError
    from . import aws
    from .config import ConfigError
    try:
        region = aws.cfg()["AWS_REGION"]
        own, owner = aws.bucket_name(), aws.account_id()
        s3 = aws.client("s3")
        if a.prepare:
            if _bucket_exists(s3, own, owner):
                if ensure_private(s3, own, owner) == "reapplied":
                    print(f"🔒 桶 {own} 之前没有完全屏蔽公开访问，已重新打开「阻止公共访问」（四项全部开启）")
            else:
                from .runtime import ensure_bucket
                ensure_bucket()  # creates it private (all four public-access blocks) and tagged
            print(f"✅ 你的私有桶：{own}")
            print("   名字规则：s12lab-<个人后缀>-<12 位账号 ID>-<区域>；只在本实验账号内可见")
            print("👉 在笔记本浏览器打开下面的链接（需已登录控制台），点「上传」，把 discussion-feedback.md 和")
            print("   golden-set-feedback.csv 拖进去（不要改文件名；放在 templates/ 文件夹里也可以），再点「上传」：")
            print(f"   {console_url(own, region)}")
            print("上传完成后回到 EC2 运行：./tools/pull-templates.sh")
            return 0
        bucket = normalize_bucket(a.bucket) if a.bucket else own
        if not _bucket_exists(s3, bucket, owner):
            if bucket == own:
                print(f"❌ 你的桶 {bucket} 还不存在：先运行 ./tools/pull-templates.sh --prepare", file=sys.stderr)
            else:
                print(f"❌ 桶 {bucket} 不存在：请向同组操作员确认桶名（操作员运行 --prepare 时打印的那个）", file=sys.stderr)
            return 1
        return Puller(s3, bucket, dry_run=a.dry_run, force=a.force, owner=owner).run()
    except (PullError, ConfigError) as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    except (ClientError, BotoCoreError) as e:
        print(f"❌ {_aws_hint(e)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
