#!/usr/bin/env bash
# 一行切换 Agent / 评审模型（写入 config/local.env，不改共享配置）。
#   ./tools/use-model.sh nova      # 切到 FALLBACK_MODEL_ID（Amazon Nova 2 Lite）
#   ./tools/use-model.sh claude    # 切回 config/lab.env 中的默认模型
#   ./tools/use-model.sh <model-id> # 指定任意 Bedrock 模型/推理配置文件 ID
set -euo pipefail
cd "$(dirname "$0")/.."
ARG="${1:-}"
[ -z "$ARG" ] && { echo "用法: $0 nova|claude|<model-id>" >&2; exit 1; }
touch config/local.env
grep -vE '^(AGENT_MODEL_ID|JUDGE_MODEL_ID)=' config/local.env > config/local.env.tmp || true
case "$ARG" in
  claude) : ;;
  nova) M=$(grep '^FALLBACK_MODEL_ID=' config/lab.env | cut -d= -f2-)
        printf 'AGENT_MODEL_ID=%s\nJUDGE_MODEL_ID=%s\n' "$M" "$M" >> config/local.env.tmp ;;
  *) printf 'AGENT_MODEL_ID=%s\nJUDGE_MODEL_ID=%s\n' "$ARG" "$ARG" >> config/local.env.tmp ;;
esac
mv config/local.env.tmp config/local.env
echo "✅ 当前模型设置："; grep -E '^(AGENT_MODEL_ID|JUDGE_MODEL_ID)=' config/local.env || echo "  使用 config/lab.env 默认值"
echo "   B 版需重新部署 Runtime 才生效：./20-B-deploy-runtime.sh；评审模型需重跑 ./30-create-evaluators.sh"
