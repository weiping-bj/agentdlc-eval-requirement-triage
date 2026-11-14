#!/usr/bin/env bash
# 第 4、5 章：用黄金集批量调用 Agent（每条需求一个 session），结果登记到 .lab-state/runs.json
#   ./42-run-golden.sh                 # AgentCore Runtime 上的 Agent（主线）
#   ./42-run-golden.sh --limit 3       # 只跑 3 条
#   ./42-run-golden.sh --mode local    # 扩展：在 EC2 上自托管运行 mock agent（ADOT 埋点，版本取 AGENT_VERSION）
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
MODE=runtime; ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE="${2:-}"; shift 2 || { echo "用法: $0 [--mode runtime|local] [--limit N]" >&2; exit 1; } ;;
    --mode=*) MODE="${1#--mode=}"; shift ;;
    *) ARGS+=("$1"); shift ;;
  esac
done
case "$MODE" in
  runtime) exec "${_ADLC_PY}" -m labkit.batch --mode runtime --source golden ${ARGS[@]+"${ARGS[@]}"} ;;
  local)
    "${_ADLC_PY}" -m labkit.loggroup "$LOCAL_LOG_GROUP"
    source telemetry/otel_env.sh "$LOCAL_SERVICE_NAME" "$LOCAL_LOG_GROUP"
    OTEL_BIN="$(dirname "${_ADLC_PY}")/opentelemetry-instrument"
    [ -x "$OTEL_BIN" ] || OTEL_BIN="opentelemetry-instrument"
    exec "$OTEL_BIN" "${_ADLC_PY}" -m labkit.batch --mode local --source golden ${ARGS[@]+"${ARGS[@]}"} ;;
  *) echo "❌ --mode 只能是 runtime（默认）或 local" >&2; exit 1 ;;
esac
