#!/usr/bin/env bash
# 第 10 章：删除实验资源并检查残留
#   ./99-cleanup.sh                 # 删除我的资源（s12lab-<后缀>-*），然后列出残留
#   ./99-cleanup.sh --dry-run       # 只显示将删除什么
#   ./99-cleanup.sh --verify-only   # 只检查残留
#   ./99-cleanup.sh --all           # 讲师（在自己电脑上运行）：删除本账号本区域所有 s12lab 资源（含 EC2 堆栈）
#   ./99-cleanup.sh --all --account-level   # 讲师最终清理：若 Transaction Search 是本实验开启的，再还原它
#   ./99-cleanup.sh --all --account-level --service-linked-roles   # 并删除实验中自动创建的服务关联角色
set -euo pipefail
cd "$(dirname "$0")"
if [[ " $* " == *" --all "* ]]; then
  export LAB_SUFFIX="${LAB_SUFFIX:-instructor}"
fi
source ./00-config.sh
exec "${_S12_PY}" -m labkit.cleanup "$@"
