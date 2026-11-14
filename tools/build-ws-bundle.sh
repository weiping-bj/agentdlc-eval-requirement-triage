#!/usr/bin/env bash
# 维护者：构建 Workshop Studio 实验包 workshop-studio/assets/adlc-lab.zip（可重复构建），打印 sha256。
#   ./tools/build-ws-bundle.sh                    # 构建（未提交的改动会被警告并记录在包内 WS_BUNDLE.json）
#   ./tools/build-ws-bundle.sh --require-clean    # 发布用：有未提交改动时失败
#   ./tools/build-ws-bundle.sh --list             # 只列出会打包的文件
# 包含 git 跟踪的文件，排除清单见 tools/ws_bundle.py 的 EXCLUDE。上传方法见 workshop-studio/README.md。
set -euo pipefail
cd "$(dirname "$0")/.."
PY="python3"; [ -x .venv/bin/python ] && PY=".venv/bin/python"
exec "$PY" tools/ws_bundle.py "$@"
