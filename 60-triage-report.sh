#!/usr/bin/env bash
# 第 6 章（发布与观测）：用当前版本的 Agent 处理“上个月 60 条需求反馈”，生成分诊报告（场景最终输出）
#   ./60-triage-report.sh                 # AgentCore Runtime 上的 Agent（主线）
#   ./60-triage-report.sh --mode local    # 扩展：EC2 上自托管运行的 mock agent（带遥测）
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
MODE=runtime
while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE="${2:-}"; shift 2 || { echo "用法: $0 [--mode runtime|local]" >&2; exit 1; } ;;
    --mode=*) MODE="${1#--mode=}"; shift ;;
    *) echo "用法: $0 [--mode runtime|local]" >&2; exit 1 ;;
  esac
done
case "$MODE" in
  runtime) "${_ADLC_PY}" -m labkit.batch --mode runtime --source feedback --workers 6 ;;
  local)
    OTEL_BIN="$(dirname "${_ADLC_PY}")/opentelemetry-instrument"
    [ -x "$OTEL_BIN" ] || OTEL_BIN="opentelemetry-instrument"
    "${_ADLC_PY}" -m labkit.loggroup "$LOCAL_LOG_GROUP"
    source telemetry/otel_env.sh "$LOCAL_SERVICE_NAME" "$LOCAL_LOG_GROUP"
    "$OTEL_BIN" "${_ADLC_PY}" -m labkit.batch --mode local --source feedback --workers 6 ;;
  *) echo "❌ --mode 只能是 runtime（默认）或 local" >&2; exit 1 ;;
esac
# labkit.report prints status to stderr and only the output path to stdout
OUT=$("${_ADLC_PY}" -m labkit.report | tail -1)
echo "$OUT"
[ -n "$OUT" ] && [ -f "$OUT" ] && "${_ADLC_PY}" -m labkit.publish "$OUT"
