#!/usr/bin/env bash
# 第 6 章：部署评估器 Lambda，并把自定义评估器注册到 AgentCore Evaluations（可重复运行）
#   ./30-create-evaluators.sh          # 创建/更新
#   ./30-create-evaluators.sh --list   # 查看已注册的评估器
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
exec "${_S12_PY}" -m labkit.evaluators_reg "$@"
