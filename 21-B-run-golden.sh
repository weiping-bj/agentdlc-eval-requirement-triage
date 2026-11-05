#!/usr/bin/env bash
# B4 / 第 7、9 章：用黄金集批量调用 AgentCore Runtime（每条需求一个 session）
#   ./21-B-run-golden.sh            # 全部黄金集
#   ./21-B-run-golden.sh --limit 3  # 只跑 3 条
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
exec "${_S12_PY}" -m labkit.batch --mode runtime --source golden "$@"
