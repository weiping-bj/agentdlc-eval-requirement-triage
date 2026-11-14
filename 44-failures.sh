#!/usr/bin/env bash
# 第 4 章（评估）：列出判错的需求，带出黄金集标的五维和标注理由，做失败归因
#   ./44-failures.sh                               # 最新一次评估（results/ 里最新的 eval-*.json）
#   ./44-failures.sh results/eval-<run_id>.json    # 指定评估
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
exec "${_ADLC_PY}" -m labkit.failures "$@"
