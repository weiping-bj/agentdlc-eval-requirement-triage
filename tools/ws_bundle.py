"""Build the Workshop Studio lab bundle (adlc-lab.zip) from this repository.

  python3 tools/ws_bundle.py                       # -> workshop-studio/assets/adlc-lab.zip, prints its sha256
  python3 tools/ws_bundle.py --out /tmp/x.zip      # another output path
  python3 tools/ws_bundle.py --list                # only print the file list
  python3 tools/ws_bundle.py --allow-dirty         # build from uncommitted changes (testing only)

What goes in: every file git tracks (so nothing gitignored and machine-local can slip in: .venv, .lab-state,
results, config/local.env, logs, zips), minus the paths in EXCLUDE (maintainer-only or other delivery paths).
The workshop-studio/static template unpacks the zip into ~ec2-user/adlc-lab and runs ec2/setup-ec2.sh.

Deterministic: sorted entries, fixed 1980-01-01 timestamps, file modes from git (0755 / 0644), one compression
level, and a WS_BUNDLE.json manifest that names the source commit. The same tree gives the same bytes (with the
same Python/zlib). Content is read from the working tree, so a tree with uncommitted changes in bundled files
is refused (a maintainer's dry-run edits would ship to every participant); --allow-dirty builds it anyway and
records source_dirty=true.
Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "workshop-studio" / "assets" / "adlc-lab.zip"
MANIFEST = "WS_BUNDLE.json"
ZIP_TIME = (1980, 1, 1, 0, 0, 0)

# Path prefixes (directories end with "/") or exact paths left out of the bundle.
EXCLUDE = (
    "workshop-studio/",              # the Workshop Studio content repo itself
    "tests/",                        # maintainer suite (checks the GitHub handbook pages, needs docs/img)
    "docs/img/",                     # screenshots for the GitHub Pages handbook only
    "docs/.nojekyll",
    ".repo-url",                     # GitHub publishing helpers
    "tools/set-repo-url.sh",
    "tools/build-ws-bundle.sh",      # this builder
    "tools/ws_bundle.py",
    "ec2/adlc-ec2.yaml",             # CloudShell / manual EC2 path: Workshop Studio deploys static/adlc-lab.yaml
    "ec2/launch-from-cloudshell.sh",
    "ec2/manual-iam-inline-policy.json",
    "ec2/README.md",
)

# Never in a bundle, even if someone force-adds them to git.
FORBIDDEN_PARTS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules",
                   ".lab-state", "results", "reports", "logs", "outputs", "build", "dist"}
FORBIDDEN_NAMES = {"config/local.env", ".lab-suffix", ".env"}
FORBIDDEN_SUFFIXES = (".pyc", ".log", ".zip", ".swp")
FORBIDDEN_PREFIXES = ("data/customer/", "data/templates/.backup/")


def _git(*args: str, root: Path = ROOT) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def excluded(rel: str) -> bool:
    return any(rel == e or (e.endswith("/") and rel.startswith(e)) for e in EXCLUDE)


def forbidden(rel: str) -> str | None:
    """Why a path must never ship (None when it may)."""
    parts = rel.split("/")
    if set(parts[:-1]) & FORBIDDEN_PARTS or parts[-1] in FORBIDDEN_PARTS:
        return "machine-local directory"
    if rel in FORBIDDEN_NAMES or parts[-1].startswith(".env.") or rel.startswith(FORBIDDEN_PREFIXES):
        return "machine-local file"
    if rel.endswith(FORBIDDEN_SUFFIXES):
        return "build/runtime artifact"
    return None


def tracked() -> list[tuple[str, int]]:
    """[(path, mode)] of every tracked file, mode 0o755 or 0o644 as git records it."""
    out = []
    for line in _git("ls-files", "-s", "-z").split("\0"):
        if not line:
            continue
        meta, rel = line.split("\t", 1)
        gmode = meta.split()[0]
        if gmode not in ("100644", "100755"):
            continue  # symlinks / submodules are not part of the lab
        out.append((rel, 0o755 if gmode == "100755" else 0o644))
    return out


def tracked_but_ignored(root: Path = ROOT) -> set[str]:
    """Files git tracks although the repo's .gitignore files match them (force-added): machine-local by the
    repo's own rules. Only the committed .gitignore files count, not a maintainer's global excludes."""
    out = _git("-c", "core.excludesFile=/dev/null", "ls-files", "-ci", "--exclude-per-directory=.gitignore", "-z",
               root=root)
    return {rel for rel in out.split("\0") if rel}


def select() -> list[tuple[str, int]]:
    files = sorted((rel, mode) for rel, mode in tracked() if not excluded(rel))
    ignored = tracked_but_ignored()
    bad = [f"{rel} ({forbidden(rel) or 'matched by .gitignore'})" for rel, _ in files
           if forbidden(rel) or rel in ignored]
    if bad:
        raise SystemExit("❌ refusing to bundle machine-local paths tracked by git:\n  " + "\n  ".join(bad))
    missing = [rel for rel, _ in files if not (ROOT / rel).is_file()]
    if missing:
        raise SystemExit("❌ tracked files missing from the working tree (commit or restore them):\n  "
                         + "\n  ".join(missing))
    return files


def source_state(files, root: Path = ROOT) -> tuple[str, bool]:
    """(HEAD commit, dirty): dirty = any staged or unstaged change, deletions and renames included, to a path
    the bundle would contain. Untracked files never ship, so they do not count."""
    commit = _git("rev-parse", "HEAD", root=root).strip()
    fields = _git("status", "--porcelain", "-z", "--untracked-files=no", root=root).split("\0")
    paths, i = [], 0
    while i < len(fields):
        rec = fields[i]
        i += 1
        if len(rec) < 4:
            continue
        paths.append(rec[3:])
        if rec[0] in "RC":  # rename / copy: the next field is the original path
            paths.append(fields[i])
            i += 1
    return commit, any(not excluded(p) for p in paths)


def _info(name: str, mode: int) -> zipfile.ZipInfo:
    zi = zipfile.ZipInfo(name, date_time=ZIP_TIME)
    zi.create_system = 3  # Unix, so unzip restores the mode bits below
    zi.external_attr = (0o100000 | mode) << 16
    zi.compress_type = zipfile.ZIP_DEFLATED
    return zi


def build_bytes(files, commit: str, dirty: bool) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel, mode in files:
            z.writestr(_info(rel, mode), (ROOT / rel).read_bytes(), compresslevel=9)
        manifest = {"name": "adlc-lab", "source_commit": commit, "source_dirty": dirty, "files": len(files)}
        z.writestr(_info(MANIFEST, 0o644), json.dumps(manifest, indent=2, sort_keys=True) + "\n", compresslevel=9)
    return buf.getvalue()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--list", action="store_true", help="print the bundled paths and exit")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="build even when bundled files have uncommitted changes (recorded as source_dirty)")
    a = ap.parse_args(argv)
    files = select()
    if a.list:
        print("\n".join(rel for rel, _ in files))
        return 0
    commit, dirty = source_state(files)
    if dirty:
        if not a.allow_dirty:
            print("❌ bundled files have uncommitted changes: commit them first (or --allow-dirty for a test build)",
                  file=sys.stderr)
            return 1
        print("⚠️  building from uncommitted changes (WS_BUNDLE.json records source_dirty=true)", file=sys.stderr)
    data = build_bytes(files, commit, dirty)
    out = Path(a.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    shown = out.relative_to(ROOT) if out.is_relative_to(ROOT) else out
    print(f"✅ {shown}  {len(files)} files + {MANIFEST}  {len(data)} bytes  commit {commit[:12]}"
          f"{' (dirty)' if dirty else ''}", file=sys.stderr)
    print(f"{digest}  {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
