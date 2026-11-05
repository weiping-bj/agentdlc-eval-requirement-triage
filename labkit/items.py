"""Load feedback items / golden rows in the formats defined by data/HANDOFF.md."""
from __future__ import annotations

import json
from pathlib import Path

from .config import ROOT

GOLDEN_CANDIDATES = ["data/golden/golden_set.jsonl", "data/golden/golden.jsonl", "data/golden_set.jsonl"]
FEEDBACK_CANDIDATES = ["data/feedback_last_month.jsonl", "data/feedback.jsonl", "data/input/feedback_last_month.jsonl"]


def _resolve(cands: list) -> Path:
    for c in cands:
        if (ROOT / c).exists():
            return ROOT / c
    raise FileNotFoundError(f"none of {cands} exists")


def read_jsonl(path: Path) -> list:
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


def golden_path() -> Path:
    return _resolve(GOLDEN_CANDIDATES)


def feedback_path() -> Path:
    return _resolve(FEEDBACK_CANDIDATES)


def item_of(row: dict) -> dict:
    """The agent input of a golden row (rows may embed the item under input/feedback)."""
    for k in ("input", "feedback", "item"):
        if isinstance(row.get(k), dict):
            it = dict(row[k])
            it.setdefault("id", row.get("feedback_id") or row.get("id"))
            return it
    return row


def fid_of(row: dict) -> str:
    it = item_of(row)
    return str(row.get("feedback_id") or it.get("id") or it.get("feedback_id") or row.get("id") or "")


def expected_of(row: dict) -> dict:
    """Expected labels of a golden row: {important, urgent, decision, level?}."""
    exp = row.get("expected") or row.get("labels") or row.get("expected_labels") or row
    return {k: exp.get(k) for k in ("important", "urgent", "decision", "level") if k in exp}


def load(source: str) -> list:
    """source: 'golden' | 'feedback' | <path to jsonl>. Returns golden-like rows."""
    if source == "golden":
        return read_jsonl(golden_path())
    if source == "feedback":
        return read_jsonl(feedback_path())
    return read_jsonl(Path(source))
