#!/usr/bin/env python3
"""本地调用评估器 Lambda handler（不需要部署），用来确认评估器在当前环境可用。

    python3 evaluators/run_local.py s12_label_accuracy evaluators/sample_events/label_pass_G01.json
    python3 evaluators/run_local.py s12_reject_quality evaluators/sample_events/reject_G04.json   # 会真实调用 Bedrock
"""
import importlib
import json
import os
import sys

EV = os.path.dirname(os.path.abspath(__file__))


def main(argv) -> int:
    if len(argv) != 2 or argv[0] not in ("s12_label_accuracy", "s12_reject_quality"):
        print(__doc__)
        return 2
    sys.path.insert(0, os.path.join(EV, argv[0]))
    lf = importlib.import_module("lambda_function")
    with open(argv[1], encoding="utf-8") as f:
        ev = json.load(f)
    print(json.dumps(lf.handler(ev, None), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
