#!/usr/bin/env bash
# B3: 打包 mock agent 并部署到 AgentCore Runtime（直接代码部署，无需 Docker）
#   ./20-B-deploy-runtime.sh                # 按 config 中的 AGENT_VERSION 部署/更新
#   ./20-B-deploy-runtime.sh --version v1   # 指定版本
#   ./20-B-deploy-runtime.sh invoke         # 调用一次（冒烟测试）
#   ./20-B-deploy-runtime.sh status         # 查看部署状态
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
case "${1:-deploy}" in
  invoke|status) exec "${_S12_PY}" -m labkit.runtime "$@" ;;
  deploy) shift || true; exec "${_S12_PY}" -m labkit.runtime deploy "$@" ;;
  *) exec "${_S12_PY}" -m labkit.runtime deploy "$@" ;;
esac
