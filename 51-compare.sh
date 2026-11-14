#!/usr/bin/env bash
# 第 5 章（优化）：生成前后对比页（静态 HTML），并上传到你的私有 S3 桶以便在笔记本浏览器查看
#   ./51-compare.sh                              # 默认：V0 → 最后评估的那个其它版本（V1 或 V1 参考版）
#   ./51-compare.sh --before <run_id> --after <run_id>   # 指定两次评估（run_id 或 results/eval-…json）
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
# labkit.compare prints status to stderr and only the output path to stdout
OUT=$("${_ADLC_PY}" -m labkit.compare "$@" | tail -1)
echo "$OUT"
[ -n "$OUT" ] && [ -f "$OUT" ] && "${_ADLC_PY}" -m labkit.publish "$OUT"
