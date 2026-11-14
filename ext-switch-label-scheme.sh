#!/usr/bin/env bash
# 扩展「标签方案切换」：一条命令把整个实验从“是/否”切到“P0/P1/P2 分级”（或切回来）
#   ./ext-switch-label-scheme.sh level               # 是/否 → P0/P1/P2（优先级完全一致才算对）
#   ./ext-switch-label-scheme.sh level --tolerance 1 # 相差不超过一级也算对
#   ./ext-switch-label-scheme.sh binary              # 切回 是/否
#   ./ext-switch-label-scheme.sh level --dry-run     # 只打印要做的步骤
# 依次处理：标准（config/local.env）→ 黄金集（补优先级 + 冻结）→ 评估器（重新部署，多一个 label_level）
#          → 提示词（重新部署 Runtime，要求输出 level）→ 门禁（重新启用在线评估）。逻辑见 labkit/label_scheme.py
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
exec "${_ADLC_PY}" -m labkit.label_scheme "$@"
