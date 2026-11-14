#!/usr/bin/env bash
# 第 4、5 章：对最近一次（或指定）运行做按需评估（AgentCore Evaluations Evaluate API），按三大支柱给出结论
#   ./43-run-eval.sh                  # 评估最近一次运行
#   ./43-run-eval.sh --run <run_id>   # 评估指定运行（run_id 见 .lab-state/runs.json）
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
exec "${_ADLC_PY}" -m labkit.evaluate "$@"
