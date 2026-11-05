#!/usr/bin/env bash
# =============================================================================
# A 版：让“不在 AgentCore Runtime 上”的 Agent 通过 ADOT 把 trace 发到 CloudWatch。
# 用法（先 source ./00-config.sh）：
#   source telemetry/otel_env.sh                 # 默认用 mock agent 的服务名/日志组
#   source telemetry/otel_env.sh <服务名> <日志组>  # 接入你自己的 Agent 时改成自己的名字
#   opentelemetry-instrument python your_agent.py
# 参考：AgentCore 开发者指南 “Add observability to agents hosted outside AgentCore Runtime”
# =============================================================================
_SVC="${1:-${LOCAL_SERVICE_NAME:?请先 source ./00-config.sh}}"
_LG="${2:-${LOCAL_LOG_GROUP}}"
export AGENT_OBSERVABILITY_ENABLED=true            # ADOT 打开 GenAI 观测默认值（100% 采样、session.id 透传）
export OTEL_PYTHON_DISTRO=aws_distro
export OTEL_PYTHON_CONFIGURATOR=aws_configurator
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_TRACES_EXPORTER=otlp                   # span → X-Ray OTLP 端点 → aws/spans（Transaction Search）
export OTEL_LOGS_EXPORTER=otlp                     # 模型输入/输出事件 → 下面的日志组
export OTEL_RESOURCE_ATTRIBUTES="service.name=${_SVC},aws.log.group.names=${_LG}"
export OTEL_EXPORTER_OTLP_LOGS_HEADERS="x-aws-log-group=${_LG},x-aws-log-stream=runtime-logs,x-aws-metric-namespace=bedrock-agentcore"
echo "📡 OTel 已配置：service.name=${_SVC} · 日志组 ${_LG} · 区域 ${AWS_REGION}"
unset _SVC _LG
