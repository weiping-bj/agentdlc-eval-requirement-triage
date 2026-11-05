"""Tiny dependency-free Chinese/English text similarity (character bigrams + words)."""
from __future__ import annotations

import re

_WORD = re.compile(r"[A-Za-z0-9_]+")
_CJK = re.compile(r"[一-鿿]+")


def grams(text: str) -> set:
    text = (text or "").lower()
    out = set(_WORD.findall(text))
    for run in _CJK.findall(text):
        if len(run) == 1:
            out.add(run)
        out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out


def score(query: str, doc: str) -> float:
    q, d = grams(query), grams(doc)
    if not q or not d:
        return 0.0
    inter = len(q & d)
    return inter / (len(q) ** 0.5 * len(d) ** 0.5)
