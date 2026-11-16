"""Data access for the agent tools: platform capability catalog + last month's feedback.

Locations (first match wins):
  1. $S12_DATA_DIR
  2. <package>/agent_data   (bundled into the AgentCore Runtime zip by 20-B-deploy-runtime.sh)
  3. <repo>/data            (local runs from the repo)
File names follow data/HANDOFF.md; loaders accept a few field-name variants.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

PKG = Path(__file__).resolve().parent
CATALOG_FILES = ["capability_catalog.json", "capabilities.json"]
FEEDBACK_FILES = ["feedback_last_month.jsonl", "feedback.jsonl", "input/feedback_last_month.jsonl"]


def data_dir() -> Path:
    cands = []
    if os.environ.get("S12_DATA_DIR"):
        cands.append(Path(os.environ["S12_DATA_DIR"]))
    cands += [PKG / "agent_data", PKG.parent.parent / "data"]
    for c in cands:
        if any((c / f).exists() for f in CATALOG_FILES):
            return c
    raise FileNotFoundError(f"capability catalog not found in: {[str(c) for c in cands]}")


def _first(d: Path, names: list) -> Path:
    for n in names:
        if (d / n).exists():
            return d / n
    raise FileNotFoundError(f"none of {names} in {d}")


def _read_jsonl(p: Path) -> list:
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


@lru_cache(maxsize=1)
def capabilities() -> list:
    raw = json.loads(_first(data_dir(), CATALOG_FILES).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("capabilities") or raw.get("items") or []
    return raw


@lru_cache(maxsize=1)
def feedback() -> list:
    try:
        return _read_jsonl(_first(data_dir(), FEEDBACK_FILES))
    except FileNotFoundError:
        return []


def g(d: dict, *keys, default=""):
    """Get the first present key (tolerates schema variants)."""
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def cap_text(c: dict) -> str:
    parts = [g(c, "name", "title"), g(c, "module", "category", "domain"), g(c, "description", "desc"),
             " ".join(c.get("keywords", []) if isinstance(c.get("keywords"), list) else [str(c.get("keywords", ""))]),
             g(c, "limits", "limitations", "constraints"), g(c, "workaround", "alternatives")]
    return " ".join(str(p) for p in parts if p)


def fb_text(f: dict) -> str:
    return " ".join(str(g(f, k)) for k in ("title", "content", "description", "detail", "body") if g(f, k))
