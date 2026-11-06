#!/usr/bin/env python3
"""Copy shared code + bundled data into each Lambda package directory, render the LLM-judge config,
and regenerate the readable capability-catalog views.

    python3 evaluators/sync_shared.py          # write copies
    python3 evaluators/sync_shared.py --check  # exit 1 if any copy is stale (used by tests)

Copies:
  evaluators/shared/span_adapter.py, golden_store.py   -> s12_label_accuracy/, s12_reject_quality/
  data/golden/golden_set.jsonl  -> */golden_set.json   ({"version", "items": {feedback_id: gold}})
  data/capability_catalog.json  -> s12_reject_quality/capability_catalog.json
  rubric.py                     -> evaluators/llm_judge_config/s12_reject_quality.json
  data/capability_catalog.json  -> docs/catalog.html, data/capability_catalog.csv   (data/tools/catalog_views.py;
                                   always the repo catalog, never --catalog: these two files are published)
  data/feedback_last_month.jsonl -> docs/mock-data.html  (data/tools/mock_views.py; categories from seed/pool.jsonl)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

EV = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(EV)
PKGS = ("s12_label_accuracy", "s12_reject_quality")
SHARED = ("span_adapter.py", "golden_store.py")


def _golden_bundle(golden_path: str) -> str:
    sys.path.insert(0, os.path.join(EV, "shared"))
    import golden_store  # noqa: E402
    items = golden_store.load_golden(golden_path)
    versions = set()
    with open(golden_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                versions.add(json.loads(line).get("version") or "v1")
    doc = {"version": ",".join(sorted(versions)), "source": "data/golden/golden_set.jsonl", "count": len(items),
           "items": dict(sorted(items.items()))}
    return json.dumps(doc, ensure_ascii=False, indent=1) + "\n"


def _judge_config() -> str:
    sys.path.insert(0, os.path.join(EV, "s12_reject_quality"))
    import rubric  # noqa: E402
    cfg = {"llmAsAJudge": {
        "instructions": rubric.llm_judge_instructions(),
        "ratingScale": rubric.rating_scale(),
        "modelConfig": {"bedrockEvaluatorModelConfig": {
            "modelId": "__JUDGE_MODEL_ID__",
            "inferenceConfig": {"maxTokens": 1024}}}}}
    return json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"


def planned(golden_path: str, catalog_path: str) -> dict:
    files = {}
    for pkg in PKGS:
        for name in SHARED:
            with open(os.path.join(EV, "shared", name), encoding="utf-8") as f:
                files[os.path.join(EV, pkg, name)] = f.read()
        files[os.path.join(EV, pkg, "golden_set.json")] = _golden_bundle(golden_path)
    with open(catalog_path, encoding="utf-8") as f:
        files[os.path.join(EV, "s12_reject_quality", "capability_catalog.json")] = f.read()
    files[os.path.join(EV, "llm_judge_config", "s12_reject_quality.json")] = _judge_config()
    files = {p: c.encode("utf-8") for p, c in files.items()}
    files.update(_catalog_views().planned())  # repo catalog only: a --catalog (e.g. customer data) must not reach docs/
    import mock_views  # noqa: E402  (data/tools is on sys.path now)
    files.update(mock_views.planned())
    return files


def _catalog_views():
    tools = os.path.join(REPO, "data", "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import catalog_views  # noqa: E402
    return catalog_views


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--golden", default=os.path.join(REPO, "data", "golden", "golden_set.jsonl"))
    ap.add_argument("--catalog", default=os.path.join(REPO, "data", "capability_catalog.json"))
    a = ap.parse_args(argv)
    stale = [os.path.relpath(p, REPO) for p in _catalog_views().sync(planned(a.golden, a.catalog), a.check)]
    if a.check:
        if stale:
            print("过期的副本（请运行 python3 evaluators/sync_shared.py）：\n  " + "\n  ".join(stale))
            return 1
        print("评估器副本与网页版视图（能力目录、mock 数据）已同步。")
        return 0
    print("已更新：" + (", ".join(stale) if stale else "无（已是最新）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
