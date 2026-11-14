#!/usr/bin/env bash
# 第 1 章：检查实验环境（Python 依赖、配置与身份、Bedrock 模型、CloudWatch Transaction Search）
#   ./10-check-env.sh
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
exec "${_ADLC_PY}" -m labkit.checkenv "$@"
