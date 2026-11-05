"""Golden-set loading shared by the evaluators (canonical copy in evaluators/shared/).

Accepts the bundled golden_set.json ({"items": {feedback_id: gold}}), a JSON list, or the
data/golden/golden_set.jsonl rows. Standard library only.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List


def gold_from_golden_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Golden-set row (data/golden/golden_set.jsonl) -> compact gold record."""
    exp = item.get("expected") or {}
    dec = exp.get("decision")
    acc = item.get("acceptable_decisions") or ([dec] if dec else [])
    return {
        "golden_id": item.get("golden_id"),
        "feedback_id": item.get("feedback_id") or (item.get("input") or {}).get("id"),
        "important": exp.get("important"),
        "urgent": exp.get("urgent"),
        "decision": dec,
        "level": exp.get("level"),
        "acceptable_decisions": list(acc),
        "can_auto_reject": bool(item.get("can_auto_reject", True)),
        "case_type": item.get("case_type"),
        "dimensions": list(item.get("dimensions") or []),
        "reject_points": list(item.get("reject_points") or []),
        "reply_points": list(item.get("reply_points") or []),
        "title": (item.get("input") or {}).get("title"),
    }


def load_golden(path: str) -> Dict[str, Dict[str, Any]]:
    """Load either the bundled golden_set.json ({"items": {...}} or list) or the data/golden jsonl."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    rows: List[Dict[str, Any]] = []
    st = text.lstrip()
    if st.startswith("{") or st.startswith("["):
        try:
            doc = json.loads(text)
        except ValueError:
            doc = None
        if isinstance(doc, dict) and isinstance(doc.get("items"), dict):
            return {k: dict(v) for k, v in doc["items"].items()}
        if isinstance(doc, dict) and isinstance(doc.get("items"), list):
            rows = doc["items"]
        elif isinstance(doc, list):
            rows = doc
        elif doc is None:
            rows = [json.loads(l) for l in text.splitlines() if l.strip()]
        else:
            rows = [doc]
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        g = gold_from_golden_item(r) if "expected" in r else dict(r)
        if g.get("feedback_id"):
            out[g["feedback_id"]] = g
    return out
