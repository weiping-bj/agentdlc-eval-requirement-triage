#!/usr/bin/env bash
# 第 3 章（构建）：打包 mock agent 并部署到 AgentCore Runtime（直接代码部署，无需 Docker）
#   ./30-deploy-runtime.sh                    # 按 config 中的 AGENT_VERSION 部署/更新（第 3、5 章）
#   ./30-deploy-runtime.sh --version v1-ref   # 指定版本
#   ./30-deploy-runtime.sh invoke             # 调用一次（冒烟测试）；--one FB-2610-002 换一条需求
#   ./30-deploy-runtime.sh status             # 查看部署状态
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
case "${1:-deploy}" in
  invoke|status) exec "${_ADLC_PY}" -m labkit.runtime "$@" ;;
  deploy) shift || true; exec "${_ADLC_PY}" -m labkit.runtime deploy "$@" ;;
  *) exec "${_ADLC_PY}" -m labkit.runtime deploy "$@" ;;
esac
