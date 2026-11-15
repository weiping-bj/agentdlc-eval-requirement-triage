#!/usr/bin/env bash
# 第 4、5 章：结果页（静态 HTML）：每个版本的结论（红线 · 门禁 · 通过率）、版本趋势、按条筛选；点一条看需求原文、黄金集答案、
# Agent 输出、工具调用和每个评估器的解释。生成后上传到你的私有 S3 桶，在笔记本浏览器查看
#   ./45-results-page.sh                            # 默认：每个版本最近一次评估（V0、V1、V1 参考版……按版本排）
#   ./45-results-page.sh <run_id> [<run_id> ...]    # 指定几次评估（run_id 或 results/eval-…json），按给出的顺序
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
# labkit.results_page prints status to stderr and only the output path to stdout
OUT=$("${_ADLC_PY}" -m labkit.results_page "$@" | tail -1)
echo "$OUT"
[ -n "$OUT" ] && [ -f "$OUT" ] && "${_ADLC_PY}" -m labkit.publish "$OUT"
