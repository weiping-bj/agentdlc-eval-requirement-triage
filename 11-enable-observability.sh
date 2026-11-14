#!/usr/bin/env bash
# 第 1 章（环境）：开启 CloudWatch Transaction Search（每个账号/区域一次）。
# Workshop Studio 的 CloudFormation 在准备环境时已经运行过它；自己的账号里才需要手动运行。
#   ./11-enable-observability.sh                    # 开启（已开启则不做修改）
#   ./11-enable-observability.sh --verify --wait 600 # 只检查是否就绪（最多等 600 秒）
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
exec "${_ADLC_PY}" -m labkit.observability "$@"
