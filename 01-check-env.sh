#!/usr/bin/env bash
# 检查环境（工具、身份、模型、Transaction Search）
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
exec "${_ADLC_PY}" -m labkit.checkenv "$@"
