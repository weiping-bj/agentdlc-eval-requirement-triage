#!/usr/bin/env bash
# 第 7、9 章：对最近一次（或指定）运行做按需评估（AgentCore Evaluations Evaluate API）
#   ./31-run-eval.sh                  # 评估最近一次运行
#   ./31-run-eval.sh --run <run_id>   # 评估指定运行（run_id 见 .lab-state/runs.json）
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
exec "${_S12_PY}" -m labkit.evaluate "$@"
