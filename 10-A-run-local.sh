#!/usr/bin/env bash
# A3: 在实验 EC2 上运行 mock agent（模拟不在 AgentCore Runtime 上的 Agent），通过 ADOT 把 trace 发到 CloudWatch。
#   ./10-A-run-local.sh smoke            # 不带遥测，跑 1 条，确认 Agent 正常
#   ./10-A-run-local.sh trace [N]        # 带遥测跑 N 条（默认 3）用于检查数据
#   ./10-A-run-local.sh golden           # 带遥测跑完整黄金集（第 7 章 / 第 9 章）
# 可加 --version v1 等参数，透传给 labkit.batch
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
MODE="${1:-golden}"; shift || true
case "$MODE" in
  smoke)
    exec "${_S12_PY}" -m labkit.batch --mode local --limit 1 --workers 1 --label "A-smoke-$(date +%H%M%S)" "$@" ;;
  trace|golden)
    "${_S12_PY}" -m labkit.loggroup "$LOCAL_LOG_GROUP"
    source telemetry/otel_env.sh "$LOCAL_SERVICE_NAME" "$LOCAL_LOG_GROUP"
    OTEL_BIN="$(dirname "${_S12_PY}")/opentelemetry-instrument"
    [ -x "$OTEL_BIN" ] || OTEL_BIN="opentelemetry-instrument"
    if [ "$MODE" = "trace" ]; then
      N="${1:-3}"; [[ "$N" =~ ^[0-9]+$ ]] && shift || N=3
      exec "$OTEL_BIN" "${_S12_PY}" -m labkit.batch --mode local --limit "$N" "$@"
    fi
    exec "$OTEL_BIN" "${_S12_PY}" -m labkit.batch --mode local "$@" ;;
  *) echo "用法: $0 smoke|trace [N]|golden [--version v0|v1]" >&2; exit 1 ;;
esac
