#!/usr/bin/env bash
# 第 4 章（评估）：开启在线评估，让新 session 自动打分并显示在 CloudWatch 控制台
#   ./41-online-eval.sh enable                # AgentCore Runtime 上的 Agent（主线）
#   ./41-online-eval.sh disable               # 停用
#   ./41-online-eval.sh status
#   ./41-online-eval.sh enable --mode local   # 扩展：EC2 上自托管运行的 mock agent（ADOT 埋点）
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
exec "${_ADLC_PY}" -m labkit.online "${@:-status}"
