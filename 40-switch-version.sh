#!/usr/bin/env bash
# 第 8 章：切换 Agent 版本（v0 基线 / v1 优化版），写入 config/local.env
#   ./40-switch-version.sh v1
# B 版切换后需要重新部署：./20-B-deploy-runtime.sh
set -euo pipefail
cd "$(dirname "$0")"
V="${1:-}"
[[ "$V" =~ ^v[0-9]+$ ]] || { echo "用法: $0 v0|v1" >&2; exit 1; }
[ -f "agent/s12agent/prompts/$V.md" ] || { echo "❌ 找不到 agent/s12agent/prompts/$V.md" >&2; exit 1; }
touch config/local.env
grep -v '^AGENT_VERSION=' config/local.env > config/local.env.tmp || true
echo "AGENT_VERSION=$V" >> config/local.env.tmp && mv config/local.env.tmp config/local.env
echo "✅ Agent 版本已切换为 $V"
echo "   A 版：./10-A-run-local.sh golden      B 版：./20-B-deploy-runtime.sh 然后 ./21-B-run-golden.sh"
