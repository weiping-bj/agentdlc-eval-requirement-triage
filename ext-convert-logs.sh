#!/usr/bin/env bash
# 扩展「已有日志接入」：已有 Agent 日志 → OTel span → CloudWatch（格式见 telemetry/LOG_FORMAT.md）
#   ./ext-convert-logs.sh check telemetry/samples/customer-agent-logs.jsonl
#   ./ext-convert-logs.sh send  telemetry/samples/customer-agent-logs.jsonl
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
OTEL_BIN="$(dirname "${_ADLC_PY}")/opentelemetry-instrument"
[ -x "$OTEL_BIN" ] || OTEL_BIN="opentelemetry-instrument"
CMD="${1:-}"; FILE="${2:-}"
[ -n "$CMD" ] && [ -f "$FILE" ] || { echo "用法: $0 check|send <logs.jsonl>" >&2; exit 1; }
if [ "$CMD" = "send" ]; then
  "${_ADLC_PY}" -m labkit.loggroup "$CONVERTED_LOG_GROUP"
  source telemetry/otel_env.sh "$CONVERTED_SERVICE_NAME" "$CONVERTED_LOG_GROUP"
  exec "$OTEL_BIN" "${_ADLC_PY}" -m labkit.convert_logs send "$FILE"
fi
exec "${_ADLC_PY}" -m labkit.convert_logs "$CMD" "$FILE"
