#!/usr/bin/env bash
# 开启 CloudWatch Transaction Search（每个账号/区域一次）
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
exec "${_S12_PY}" -m labkit.observability "$@"
