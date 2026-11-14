#!/usr/bin/env bash
# 第 5 章（优化）：切换 Agent 版本，写入 config/local.env
#   ./50-switch-version.sh v1       # 你自己的 V1（第 5.2 步用 Claude Code 写出的 prompts/v1.md）
#   ./50-switch-version.sh v1-ref   # V1 参考版（仓库准备好的 prompts/v1-ref.md，时间不够时用）
#   ./50-switch-version.sh v0       # 切回基线
# 切换后重新部署 Runtime 才生效：./30-deploy-runtime.sh
set -euo pipefail
cd "$(dirname "$0")"
V="${1:-}"
[[ "$V" =~ ^v[0-9]+(-ref)?$ ]] || { echo "用法: $0 v0|v1|v1-ref" >&2; exit 1; }
if [ ! -f "agent/adlc_agent/prompts/$V.md" ]; then
  echo "❌ 找不到 agent/adlc_agent/prompts/$V.md" >&2
  [ "$V" = "v1" ] && echo "   先做第 5.2 步（用 Claude Code 写出你的 V1）；时间不够就用参考版：$0 v1-ref" >&2
  exit 1
fi
touch config/local.env
grep -v '^AGENT_VERSION=' config/local.env > config/local.env.tmp || true
echo "AGENT_VERSION=$V" >> config/local.env.tmp && mv config/local.env.tmp config/local.env
echo "✅ Agent 版本已切换为 $V"
echo "   下一步：./30-deploy-runtime.sh（重新部署 Runtime），然后 ./42-run-golden.sh"
