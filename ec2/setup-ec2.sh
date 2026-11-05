#!/usr/bin/env bash
# Scenario 12 lab - install the toolchain ON the lab EC2 (Amazon Linux 2023).
#
#   Run as ec2-user from the cloned repo:   bash ec2/setup-ec2.sh
#
# Idempotent (safe to re-run). Installs: uv + Python 3.12, Node.js (>=20),
# AWS CLI v2 (verify), Claude Code (npm, user prefix ~/.npm-global), configures
# Claude Code for Amazon Bedrock via the EC2 instance role, creates .venv and
# installs requirements.txt. Log: ~/s12lab-setup.log
#
# Region / model ids: read from config/lab.env when present (plain KEY=VALUE:
# AWS_REGION, CODING_AGENT_MODEL_ID, CODING_AGENT_FAST_MODEL_ID); otherwise the
# region comes from instance metadata (IMDSv2) and the defaults below are used.
set -euo pipefail

DEFAULT_MODEL="us.anthropic.claude-sonnet-5-5"
DEFAULT_FAST_MODEL="us.anthropic.claude-haiku-5-5"
TOTAL=8

if [ "$(id -u)" -eq 0 ] || [ "$(id -un)" = "ssm-user" ]; then
  echo "请先切换到 ec2-user:  sudo su - ec2-user   然后再运行: bash ec2/setup-ec2.sh" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
LOG="$HOME/s12lab-setup.log"
exec > >(tee -a "$LOG") 2>&1
trap 'echo; echo "!! 第 $LINENO 行出错，安装中止。完整日志: $LOG  （可直接重新运行本脚本）" >&2' ERR
echo "===== s12lab setup start $(date '+%F %T') ($REPO_ROOT) ====="

export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"
step() { echo; echo "==> [$1/$TOTAL] $2"; }

# Read KEY from config/lab.env (plain KEY=VALUE; comments/blank lines ignored;
# optional "export ", quotes and ${VAR:-default} are tolerated). Last match wins.
lab_env() {
  local file="$REPO_ROOT/config/lab.env" line key val found=""
  [ -f "$file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line#export }"
    [[ "$line" == *=* ]] || continue
    key="${line%%=*}"
    [ "${key//[[:space:]]/}" = "$1" ] || continue
    val="${line#*=}"
    val="${val%%[[:space:]]#*}"
    val="${val#"${val%%[![:space:]]*}"}"
    val="${val%"${val##*[![:space:]]}"}"
    val="${val#\"}"; val="${val%\"}"; val="${val#\'}"; val="${val%\'}"
    if [[ "$val" =~ ^\$\{([A-Za-z_][A-Za-z0-9_]*):-(.*)\}$ ]]; then
      local ref="${BASH_REMATCH[1]}" def="${BASH_REMATCH[2]}"
      val="${!ref:-$def}"
    fi
    found="$val"
  done < "$file"
  printf '%s' "$found"
}

imds() {  # imds <path>  -> value via IMDSv2, empty on failure
  local token
  token="$(curl -fsS -m 3 -X PUT http://169.254.169.254/latest/api/token \
    -H 'X-aws-ec2-metadata-token-ttl-seconds: 300' 2>/dev/null)" || return 0
  curl -fsS -m 3 -H "X-aws-ec2-metadata-token: $token" \
    "http://169.254.169.254/latest/meta-data/$1" 2>/dev/null || true
}

node_major() { local v; v="$(node --version 2>/dev/null || echo v0)"; v="${v#v}"; echo "${v%%.*}"; }

# ------------------------------------------------------------------ 1. OS packages
step 1 "安装基础工具 git / jq / tar / gzip / unzip"
missing=()
for p in git jq tar gzip unzip; do command -v "$p" >/dev/null 2>&1 || missing+=("$p"); done
if [ "${#missing[@]}" -gt 0 ]; then
  sudo dnf install -y "${missing[@]}"
else
  echo "    已安装，跳过"
fi

# ------------------------------------------------------------------ 2. region / models
step 2 "读取区域和模型配置"
IMDS_REGION="$(imds placement/region)"
CFG_REGION="$(lab_env AWS_REGION)"
REGION="${CFG_REGION:-${IMDS_REGION:-${AWS_REGION:-$(aws configure get region 2>/dev/null || true)}}}"
if [ -z "$REGION" ]; then
  echo "无法确定 AWS 区域（config/lab.env 和实例元数据都没有）。" >&2
  exit 1
fi
if [ -n "$CFG_REGION" ] && [ -n "$IMDS_REGION" ] && [ "$CFG_REGION" != "$IMDS_REGION" ]; then
  echo "    注意: config/lab.env 中 AWS_REGION=$CFG_REGION，但本机在 $IMDS_REGION。以 config/lab.env 为准。"
fi
MODEL="$(lab_env CODING_AGENT_MODEL_ID)"; MODEL="${MODEL:-$DEFAULT_MODEL}"
FAST_MODEL="$(lab_env CODING_AGENT_FAST_MODEL_ID)"; FAST_MODEL="${FAST_MODEL:-$DEFAULT_FAST_MODEL}"
# Lab suffix: /etc/profile.d (CloudFormation path) -> instance tags (manual path).
SUFFIX="${LAB_SUFFIX:-}"
if [ -z "$SUFFIX" ]; then
  IID="$(imds instance-id)"
  if [ -n "$IID" ]; then
    SUFFIX="$(aws ec2 describe-tags --region "$REGION" \
      --filters "Name=resource-id,Values=$IID" "Name=key,Values=s12lab:owner" \
      --query 'Tags[0].Value' --output text 2>/dev/null || true)"
    if [ -z "$SUFFIX" ] || [ "$SUFFIX" = "None" ]; then
      SUFFIX="$(aws ec2 describe-tags --region "$REGION" \
        --filters "Name=resource-id,Values=$IID" "Name=key,Values=Name" \
        --query 'Tags[0].Value' --output text 2>/dev/null || true)"
      SUFFIX="${SUFFIX#s12lab-}"
    fi
  fi
fi
[[ "$SUFFIX" =~ ^[a-z][a-z0-9]{1,11}$ ]] || SUFFIX=""
echo "    区域: $REGION   编码 Agent 模型: $MODEL   快速模型: $FAST_MODEL   后缀: ${SUFFIX:-（未识别）}"

# ------------------------------------------------------------------ 3. uv + Python
step 3 "安装 uv 和 Python 3.12"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh
fi
uv --version
uv python install 3.12

# ------------------------------------------------------------------ 4. Node.js
step 4 "安装 Node.js (>= 20)"
if [ "$(node_major)" -ge 20 ]; then
  echo "    已安装 node $(node --version)，跳过"
else
  PICKED=""
  # AL2023 ships namespaced packages nodejs20/22/24 (binaries node-NN / npm-NN,
  # default names managed by `alternatives`). Node 20 is EOL since 2026-04, so try 22 first.
  for v in 22 24 20; do
    if sudo dnf -q info "nodejs$v" >/dev/null 2>&1; then
      sudo dnf install -y "nodejs$v" "nodejs$v-npm"
      PICKED="$v"
      break
    fi
  done
  if [ -n "$PICKED" ]; then
    if [ -x "/usr/bin/node-$PICKED" ] && alternatives --display node >/dev/null 2>&1; then
      sudo alternatives --set node "/usr/bin/node-$PICKED" || true
    fi
    hash -r
    for b in node npm npx; do
      if { [ "$b" = node ] && [ "$(node_major)" -lt 20 ]; } || ! command -v "$b" >/dev/null 2>&1; then
        if [ -x "/usr/bin/$b-$PICKED" ]; then sudo ln -sf "/usr/bin/$b-$PICKED" "/usr/local/bin/$b"; fi
      fi
    done
    hash -r
  else
    sudo dnf install -y nodejs npm
    hash -r
  fi
  if [ "$(node_major)" -lt 20 ]; then
    echo "    警告: 当前 node 版本 $(node --version 2>/dev/null || echo 无) 低于 20，Claude Code 可能无法正常运行，请联系讲师。"
  fi
fi
command -v npm >/dev/null 2>&1 || { echo "npm 未安装成功" >&2; exit 1; }
echo "    node $(node --version) / npm $(npm --version)"

# ------------------------------------------------------------------ 5. AWS CLI v2
step 5 "检查 AWS CLI v2"
AWS_VER="$(aws --version 2>&1 || true)"
if [[ "$AWS_VER" == aws-cli/2.* ]]; then
  echo "    $AWS_VER"
else
  echo "    未检测到 AWS CLI v2（$AWS_VER），开始安装..."
  TMP_DIR="$(mktemp -d)"
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" -o "$TMP_DIR/awscliv2.zip"
  unzip -q "$TMP_DIR/awscliv2.zip" -d "$TMP_DIR"
  sudo "$TMP_DIR/aws/install" --update
  rm -rf "$TMP_DIR"
  hash -r
  aws --version
fi

# ------------------------------------------------------------------ 6. Claude Code
step 6 "安装 Claude Code（npm 用户目录 ~/.npm-global，无需 sudo）"
mkdir -p "$HOME/.npm-global"
if [ "$(npm config get prefix)" != "$HOME/.npm-global" ]; then
  npm config set prefix "$HOME/.npm-global"
fi
if command -v claude >/dev/null 2>&1 && claude --version >/dev/null 2>&1; then
  echo "    已安装 claude $(claude --version)，跳过"
else
  if ! npm install -g @anthropic-ai/claude-code; then
    echo "    npm 安装失败，改用 Claude Code 官方安装脚本（安装到 ~/.local/bin）"
    curl -fsSL https://claude.ai/install.sh | bash
  fi
  hash -r
fi
claude --version

# ------------------------------------------------------------------ 7. Claude Code -> Bedrock
step 7 "配置 Claude Code 使用 Amazon Bedrock（EC2 实例角色）"
mkdir -p "$HOME/.claude"
SETTINGS="$HOME/.claude/settings.json"
if [ -s "$SETTINGS" ] && ! jq -e 'type == "object"' "$SETTINGS" >/dev/null 2>&1; then
  cp "$SETTINGS" "$SETTINGS.bak.$(date +%s)"
  echo "    原 settings.json 不是合法 JSON，已备份后重建"
  echo '{}' > "$SETTINGS"
fi
[ -s "$SETTINGS" ] || echo '{}' > "$SETTINGS"
TMP_JSON="$(mktemp)"
jq --arg region "$REGION" --arg model "$MODEL" --arg fast "$FAST_MODEL" \
  '.env = ((.env // {}) + {
      CLAUDE_CODE_USE_BEDROCK: "1",
      AWS_REGION: $region,
      ANTHROPIC_MODEL: $model,
      ANTHROPIC_SMALL_FAST_MODEL: $fast,
      ANTHROPIC_DEFAULT_HAIKU_MODEL: $fast
    })' "$SETTINGS" > "$TMP_JSON"
mv "$TMP_JSON" "$SETTINGS"
echo "    已写入 $SETTINGS"

BASHRC="$HOME/.bashrc"
touch "$BASHRC"
sed -i '/^# >>> s12lab >>>$/,/^# <<< s12lab <<<$/d' "$BASHRC"
{
  echo '# >>> s12lab >>>'
  echo '# Managed by ec2/setup-ec2.sh - re-run that script to update this block.'
  # shellcheck disable=SC2016  # $HOME/$PATH must expand when .bashrc runs, not now
  echo 'case ":$PATH:" in *":$HOME/.npm-global/bin:"*) ;; *) export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH" ;; esac'
  echo "export AWS_REGION=\"$REGION\""
  echo "export AWS_DEFAULT_REGION=\"$REGION\""
  echo 'export CLAUDE_CODE_USE_BEDROCK=1'
  echo "export ANTHROPIC_MODEL=\"$MODEL\""
  echo "export ANTHROPIC_SMALL_FAST_MODEL=\"$FAST_MODEL\""
  echo "export ANTHROPIC_DEFAULT_HAIKU_MODEL=\"$FAST_MODEL\""
  if [ -n "$SUFFIX" ]; then echo "export LAB_SUFFIX=\"$SUFFIX\""; fi
  echo '# <<< s12lab <<<'
} >> "$BASHRC"
echo "    已更新 $BASHRC（# >>> s12lab >>> 标记块）"

# ------------------------------------------------------------------ 8. Python venv
step 8 "创建实验 Python 虚拟环境 .venv 并安装依赖"
if [ -x .venv/bin/python ] && .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)' 2>/dev/null; then
  echo "    .venv 已存在（Python 3.12），复用"
else
  rm -rf .venv
  uv venv --seed --python 3.12 .venv
fi
if [ -f requirements.txt ]; then
  uv pip install --python .venv/bin/python -r requirements.txt
else
  echo "    警告: 没有找到 requirements.txt，跳过依赖安装"
fi

# ------------------------------------------------------------------ summary
trap - ERR
ARN="$(aws sts get-caller-identity --region "$REGION" --query Arn --output text 2>/dev/null \
  | sed -E 's/[0-9]{12}/<account>/g' || true)"
echo
echo "==================== s12lab 安装完成 ===================="
printf '  %-12s %s\n' "python(.venv)" "$(.venv/bin/python --version 2>&1)"
printf '  %-12s %s\n' "node"   "$(node --version 2>&1 || echo 未安装)"
printf '  %-12s %s\n' "npm"    "$(npm --version 2>&1 || echo 未安装)"
printf '  %-12s %s\n' "aws"    "$(aws --version 2>&1 || echo 未安装)"
printf '  %-12s %s\n' "claude" "$(claude --version 2>&1 || echo 未安装)"
printf '  %-12s %s\n' "region" "$REGION"
printf '  %-12s %s\n' "model"  "$MODEL  (fast: $FAST_MODEL)"
printf '  %-12s %s\n' "identity" "${ARN:-（获取失败，请检查实例角色）}"
echo "  日志:   $LOG"
echo
echo "下一步（复制整行执行）:"
printf '  cd %q && source ~/.bashrc && source .venv/bin/activate && ./01-check-env.sh\n' "$REPO_ROOT"
echo "========================================================="
