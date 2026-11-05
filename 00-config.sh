#!/usr/bin/env bash
# =============================================================================
# 00-config.sh - 被其它脚本 `source`，把 config/lab.env (+ config/local.env)
# 导出为环境变量，并派生出所有资源名。不要在这里写具体值，改 config/lab.env。
#   source ./00-config.sh          # 在当前 shell 中加载
#   ./00-config.sh                 # 直接运行：打印当前配置
# =============================================================================
_S12_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_S12_PY="python3"
[ -x "$_S12_ROOT/.venv/bin/python" ] && _S12_PY="$_S12_ROOT/.venv/bin/python"
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  cd "$_S12_ROOT" && exec "$_S12_PY" -m labkit.config --show
fi
eval "$(cd "$_S12_ROOT" && "$_S12_PY" -m labkit.config --shell)"
export AWS_PAGER=""
export PYTHONPATH="$_S12_ROOT${PYTHONPATH:+:$PYTHONPATH}"
