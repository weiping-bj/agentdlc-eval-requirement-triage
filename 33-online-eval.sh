#!/usr/bin/env bash
# 第 6 章：开启在线评估，让新 session 自动打分并显示在 CloudWatch 控制台
#   ./33-online-eval.sh enable --path b    # B 版（Runtime 上的 mock agent）
#   ./33-online-eval.sh enable --path a    # A 版（EC2 上的 mock agent；自带 Agent 见 docs/own-agent-guide.html）
#   ./33-online-eval.sh disable --path b   # 停用（更新评估器前需要）
#   ./33-online-eval.sh status
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
exec "${_S12_PY}" -m labkit.online "${@:-status}"
