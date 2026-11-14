#!/usr/bin/env bash
# 第 4 章（评估）：部署评估器 Lambda，并把自定义评估器注册到 AgentCore Evaluations（可重复运行）
#   ./40-create-evaluators.sh          # 创建/更新（黄金集改过、冻结后也要重跑）
#   ./40-create-evaluators.sh --list   # 查看已注册的评估器
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
exec "${_ADLC_PY}" -m labkit.evaluators_reg "$@"
