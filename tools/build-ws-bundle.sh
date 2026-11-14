#!/usr/bin/env bash
# 维护者：构建 Workshop Studio 实验包 workshop-studio/assets/adlc-lab.zip（可重复构建），打印 sha256。
#   ./tools/build-ws-bundle.sh                  # 构建；打包的文件有未提交改动时拒绝（先提交）
#   ./tools/build-ws-bundle.sh --allow-dirty    # 仅测试用：用未提交的改动构建（包内 WS_BUNDLE.json 记录 source_dirty）
#   ./tools/build-ws-bundle.sh --list           # 只列出会打包的文件
#   ./tools/build-ws-bundle.sh --out x.zip      # 输出到别处（相对路径按当前目录解析）
# 包含 git 跟踪的文件，排除清单见 tools/ws_bundle.py 的 EXCLUDE。上传方法见 workshop-studio/README.md。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="python3"; [ -x "$ROOT/.venv/bin/python" ] && PY="$ROOT/.venv/bin/python"
exec "$PY" "$ROOT/tools/ws_bundle.py" "$@"
