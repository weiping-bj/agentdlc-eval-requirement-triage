#!/usr/bin/env bash
# 扩展「自托管 Agent 埋点接入」：在实验 EC2 上直接运行 mock agent（模拟不在 AgentCore Runtime 上的 Agent），
# 通过 OTel + ADOT 把 trace 发到 CloudWatch。完整黄金集用 ./42-run-golden.sh --mode local。
#   ./ext-local-agent.sh smoke            # 不带遥测，跑 1 条，确认 Agent 正常
#   ./ext-local-agent.sh trace [N]        # 带遥测跑 N 条（默认 3）用于检查数据
# 可加 --version v1-ref 等参数，透传给 labkit.batch
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
MODE="${1:-}"; shift || true
case "$MODE" in
  smoke)
    exec "${_ADLC_PY}" -m labkit.batch --mode local --limit 1 --workers 1 --label "local-smoke-$(date +%H%M%S)" "$@" ;;
  trace)
    "${_ADLC_PY}" -m labkit.loggroup "$LOCAL_LOG_GROUP"
    source telemetry/otel_env.sh "$LOCAL_SERVICE_NAME" "$LOCAL_LOG_GROUP"
    OTEL_BIN="$(dirname "${_ADLC_PY}")/opentelemetry-instrument"
    [ -x "$OTEL_BIN" ] || OTEL_BIN="opentelemetry-instrument"
    N="${1:-3}"; [[ "$N" =~ ^[0-9]+$ ]] && shift || N=3
    exec "$OTEL_BIN" "${_ADLC_PY}" -m labkit.batch --mode local --limit "$N" "$@"
    ;;
  golden) echo "完整黄金集改用：./42-run-golden.sh --mode local" >&2; exit 1 ;;
  *) echo "用法: $0 smoke|trace [N] [--version v0|v1|v1-ref]" >&2; exit 1 ;;
esac
