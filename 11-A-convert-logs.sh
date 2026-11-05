#!/usr/bin/env bash
# A4: 已有 Agent 日志 → OTel span → CloudWatch（格式见 telemetry/LOG_FORMAT.md）
#   ./11-A-convert-logs.sh check telemetry/samples/customer-agent-logs.jsonl
#   ./11-A-convert-logs.sh send  telemetry/samples/customer-agent-logs.jsonl
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
CMD="${1:-}"; FILE="${2:-}"
[ -n "$CMD" ] && [ -f "$FILE" ] || { echo "用法: $0 check|send <logs.jsonl>" >&2; exit 1; }
if [ "$CMD" = "send" ]; then
  "${_S12_PY}" -m labkit.loggroup "$CONVERTED_LOG_GROUP"
  source telemetry/otel_env.sh "$CONVERTED_SERVICE_NAME" "$CONVERTED_LOG_GROUP"
  exec "$(dirname "${_S12_PY}")/opentelemetry-instrument" "${_S12_PY}" -m labkit.convert_logs send "$FILE"
fi
exec "${_S12_PY}" -m labkit.convert_logs "$CMD" "$FILE"
