#!/usr/bin/env bash
# 第 9.4 步：用当前版本的 Agent 处理“上个月 60 条需求反馈”，生成分诊报告（场景最终输出）
#   ./34-triage-report.sh a    # A 版：本机 Agent（带遥测）
#   ./34-triage-report.sh b    # B 版：AgentCore Runtime
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
P="${1:-b}"
if [ "$P" = "a" ]; then
  "${_S12_PY}" -m labkit.loggroup "$LOCAL_LOG_GROUP"
  source telemetry/otel_env.sh
  "$(dirname "${_S12_PY}")/opentelemetry-instrument" "${_S12_PY}" -m labkit.batch --mode local --source feedback --workers 6
else
  "${_S12_PY}" -m labkit.batch --mode runtime --source feedback --workers 6
fi
OUT=$("${_S12_PY}" -m labkit.report | tee /dev/stderr | grep -Eo 'results/[^ ]+\.html' | tail -1)
[ -n "$OUT" ] && [ -f "$OUT" ] && "${_S12_PY}" -m labkit.publish "$OUT"
