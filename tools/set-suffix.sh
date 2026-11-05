#!/usr/bin/env bash
# 设置个人后缀（同一账号多人实验时区分资源）。写入 config/local.env（不提交到 git）。
# 用法: ./tools/set-suffix.sh zhang3
set -euo pipefail
cd "$(dirname "$0")/.."
S="${1:-}"
if [[ ! "$S" =~ ^[a-z][a-z0-9]{1,11}$ ]]; then
  echo "❌ 后缀需小写字母开头，2-12 位小写字母或数字，例如 zhang3" >&2; exit 1
fi
touch config/local.env
grep -v '^LAB_SUFFIX=' config/local.env > config/local.env.tmp || true
echo "LAB_SUFFIX=$S" >> config/local.env.tmp && mv config/local.env.tmp config/local.env
echo "✅ 个人后缀已设置为: $S   （你的资源名形如 s12lab-$S-...）"
