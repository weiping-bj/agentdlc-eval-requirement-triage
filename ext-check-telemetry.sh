#!/usr/bin/env bash
# 扩展「已有日志接入」/「自托管 Agent 埋点接入」：检查 CloudWatch 中的遥测数据能否被 AgentCore Evaluations 使用
#   ./ext-check-telemetry.sh                 # 检查最近一次运行/导入
#   ./ext-check-telemetry.sh --run <run_id>  # 指定运行
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
exec "${_ADLC_PY}" -m labkit.check_telemetry "$@"
