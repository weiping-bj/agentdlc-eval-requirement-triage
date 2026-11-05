"""Agent tools.

V0 (deliberately weak, "tool gap"): only `lookup_capability`, which returns capability
NAMES without support status, limits or workarounds, so the model cannot tell
"already supported" from "not planned" and has no signal about how many customers ask.

V1: `search_capabilities` (status / limits / workaround / not-planned scope) and
`find_similar_requests` (how many distinct customers raised a similar need last month).
"""
from __future__ import annotations

import json

from strands import tool

from . import data
from .textsim import score


def _rank_caps(query: str, k: int) -> list:
    caps = data.capabilities()
    ranked = sorted(caps, key=lambda c: score(query, data.cap_text(c)), reverse=True)
    return [c for c in ranked[:k] if score(query, data.cap_text(c)) > 0]


@tool
def lookup_capability(keyword: str) -> str:
    """查询平台功能名称。

    Args:
        keyword: 关键词
    """
    hits = _rank_caps(keyword, 3)
    return json.dumps([data.g(c, "name", "title") for c in hits], ensure_ascii=False)


@tool
def search_capabilities(query: str) -> str:
    """在平台能力目录中检索与需求相关的能力，返回支持状态、限制和替代方案。

    判断需求前必须先调用本工具：status 为“已支持”说明客户可能不会用（用 how_to 给出用法，而不是新开发）；
    “部分支持”看 limits 判断真实缺口；“明确不做”是“超出平台定位”拒绝的依据（引用其 id）。

    Args:
        query: 需求的核心诉求（用一句话描述，例如“列表页批量编辑自定义字段”）
    """
    hits = _rank_caps(query, 5)
    out = []
    for c in hits:
        out.append({
            "id": data.g(c, "id", "capability_id"),
            "name": data.g(c, "name", "title"),
            "module": data.g(c, "module", "category", "domain"),
            "status": data.g(c, "status", "support_status"),
            "description": data.g(c, "description", "desc"),
            "limits": data.g(c, "limits", "limitations", "constraints"),
            "workaround": data.g(c, "workaround", "alternatives"),
            "effort_hint": data.g(c, "effort_hint", "dev_effort", "difficulty"),
            "how_to": data.g(c, "how_to", "usage"),
        })
    return json.dumps(out or [{"note": "能力目录中没有相关能力"}], ensure_ascii=False)


@tool
def find_similar_requests(query: str) -> str:
    """在上个月的需求反馈中查找相似需求，统计有多少个不同客户提出过（判断通用性）。

    Args:
        query: 需求的核心诉求（一句话）
    """
    rows = []
    for f in data.feedback():
        s = score(query, data.fb_text(f))
        if s >= 0.22:
            rows.append((s, f))
    rows.sort(key=lambda x: x[0], reverse=True)
    customers = {data.g(f, "customer", "customer_name", "customer_id", "tenant") for _, f in rows}
    customers.discard("")
    return json.dumps({
        "similar_count": len(rows),
        "distinct_customers": len(customers),
        "examples": [{"id": data.g(f, "id", "feedback_id"), "title": data.g(f, "title"),
                      "customer": data.g(f, "customer", "customer_name", "customer_id", "tenant")}
                     for _, f in rows[:5]],
    }, ensure_ascii=False)


TOOLSETS = {
    "v0": [lookup_capability],
    "v1": [search_capabilities, find_similar_requests],
}
