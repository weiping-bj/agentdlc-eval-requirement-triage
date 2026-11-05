#!/usr/bin/env python3
"""Copy shared code + bundled data into each Lambda package directory, and render the LLM-judge config.

    python3 evaluators/sync_shared.py          # write copies
    python3 evaluators/sync_shared.py --check  # exit 1 if any copy is stale (used by tests)

Copies:
  evaluators/shared/span_adapter.py, golden_store.py   -> s12_label_accuracy/, s12_reject_quality/
  data/golden/golden_set.jsonl  -> */golden_set.json   ({"version", "items": {feedback_id: gold}})
  data/capability_catalog.json  -> s12_reject_quality/capability_catalog.json
  rubric.py                     -> evaluators/llm_judge_config/s12_reject_quality.json
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
    version = None
    with open(golden_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                version = json.loads(line).get("version")
                break
    doc = {"version": version, "source": "data/golden/golden_set.jsonl", "count": len(items),
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
            "inferenceConfig": {"maxTokens": 1024, "temperature": 0.0}}}}}
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
    return files


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--golden", default=os.path.join(REPO, "data", "golden", "golden_set.jsonl"))
    ap.add_argument("--catalog", default=os.path.join(REPO, "data", "capability_catalog.json"))
    a = ap.parse_args(argv)
    stale = []
    for path, content in planned(a.golden, a.catalog).items():
        cur = None
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                cur = f.read()
        if cur != content:
            stale.append(os.path.relpath(path, REPO))
            if not a.check:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
    if a.check:
        if stale:
            print("过期的副本（请运行 python3 evaluators/sync_shared.py）：\n  " + "\n  ".join(stale))
            return 1
        print("评估器副本已同步。")
        return 0
    print("已更新：" + (", ".join(stale) if stale else "无（已是最新）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
