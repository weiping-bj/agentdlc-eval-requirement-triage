#!/usr/bin/env bash
# 第 9 章：生成 V0 vs V1 前后对比页（静态 HTML），并上传到你的私有 S3 桶以便在浏览器查看
#   ./32-compare.sh                              # 自动选最早的 v0 评估和最新的其它版本评估
#   ./32-compare.sh --before results/eval-A.json --after results/eval-B.json
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
# labkit.compare prints status to stderr and only the output path to stdout
OUT=$("${_S12_PY}" -m labkit.compare "$@" | tail -1)
echo "$OUT"
[ -n "$OUT" ] && [ -f "$OUT" ] && "${_S12_PY}" -m labkit.publish "$OUT"
