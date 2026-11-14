#!/usr/bin/env bash
# =============================================================================
# 00-config.sh - 被其它脚本 `source`，把 config/lab.env (+ config/local.env)
# 导出为环境变量，并派生出所有资源名。不要在这里写具体值，改 config/lab.env。
#   source ./00-config.sh          # 在当前 shell 中加载
#   ./00-config.sh                 # 直接运行：打印当前配置
# =============================================================================
_ADLC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_ADLC_PY="python3"
[ -x "$_ADLC_ROOT/.venv/bin/python" ] && _ADLC_PY="$_ADLC_ROOT/.venv/bin/python"
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  cd "$_ADLC_ROOT" && exec "$_ADLC_PY" -m labkit.config --show
fi
eval "$(cd "$_ADLC_ROOT" && "$_ADLC_PY" -m labkit.config --shell)"
export AWS_PAGER=""
export PYTHONPATH="$_ADLC_ROOT${PYTHONPATH:+:$PYTHONPATH}"
