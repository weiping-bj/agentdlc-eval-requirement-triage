#!/usr/bin/env bash
# A5: 检查 CloudWatch 中的遥测数据能否被 AgentCore Evaluations 使用（差距大 → 切换 B 版）
#   ./12-A-check-telemetry.sh                 # 检查最近一次运行/导入
#   ./12-A-check-telemetry.sh --run <run_id>  # 指定运行
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
exec "${_S12_PY}" -m labkit.check_telemetry "$@"
