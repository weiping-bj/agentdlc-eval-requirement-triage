#!/usr/bin/env bash
# Scenario 12 lab - create your lab EC2 from AWS CloudShell.
#
#   用法 / Usage:   bash launch-from-cloudshell.sh <suffix>      例如: bash launch-from-cloudshell.sh zhang3
#
# - Stack name: s12lab-ec2-<suffix>, template: s12lab-ec2.yaml (same folder as this script)
# - Region: the CloudShell region (${AWS_REGION:-$(aws configure get region)})
# - Uses the default VPC when it exists (avoids the 5-VPCs-per-Region quota);
#   otherwise the template creates a small dedicated VPC.
# - Re-running is safe: an existing healthy stack is NOT redeployed (set
#   S12LAB_UPDATE=1 to force an update), it just prints the login link again.
# Optional env: S12LAB_INSTANCE_TYPE (t3.large | t3.xlarge | m7i.large),
#               S12LAB_ALLOW_ANY_REGION=1 (skip the us-west-2/us-east-1 check)
set -euo pipefail
export AWS_PAGER=""

SUFFIX="${1:-}"
if [[ ! "$SUFFIX" =~ ^[a-z][a-z0-9]{1,11}$ ]]; then
  echo "用法: bash $0 <后缀>    例如: bash $0 zhang3" >&2
  echo "后缀规则: 2-12 位，小写字母或数字，必须以字母开头" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/s12lab-ec2.yaml"
if [ ! -f "$TEMPLATE" ]; then
  echo "找不到模板文件: $TEMPLATE" >&2
  echo "请把 s12lab-ec2.yaml 和本脚本下载到同一个目录。" >&2
  exit 1
fi

REGION="${AWS_REGION:-$(aws configure get region 2>/dev/null || true)}"
if [ -z "$REGION" ]; then
  echo "无法确定区域。请先执行: export AWS_REGION=us-west-2   (或 us-east-1)" >&2
  exit 1
fi
if [ "$REGION" != "us-west-2" ] && [ "$REGION" != "us-east-1" ] && [ "${S12LAB_ALLOW_ANY_REGION:-0}" != "1" ]; then
  echo "当前区域是 $REGION。本实验只支持 us-west-2 或 us-east-1。" >&2
  echo "请在控制台右上角切换到讲师指定的区域，重新打开 CloudShell 后再运行。" >&2
  exit 1
fi

STACK="s12lab-ec2-$SUFFIX"
INSTANCE_TYPE="${S12LAB_INSTANCE_TYPE:-t3.large}"
aws_() { aws --region "$REGION" "$@"; }
stack_out() {
  aws_ cloudformation describe-stacks --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue | [0]" --output text
}

echo "==> 区域: $REGION    堆栈: $STACK"
if ! aws_ sts get-caller-identity --query Arn --output text >/dev/null; then
  echo "AWS 凭证不可用，请确认已登录控制台并在 CloudShell 中运行。" >&2
  exit 1
fi

# ---------------------------------------------------------------- stack state
STATUS="$(aws_ cloudformation describe-stacks --stack-name "$STACK" \
  --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo NONE)"

case "$STATUS" in
  NONE) ;;
  REVIEW_IN_PROGRESS)
    # A previous run was interrupted before the change set was executed; deploy handles this.
    STATUS=NONE
    ;;
  ROLLBACK_COMPLETE)
    echo "==> 堆栈 $STACK 上次创建失败（ROLLBACK_COMPLETE，里面没有资源），需要先删除才能重建。"
    ANSWER="n"
    if [ -t 0 ]; then read -r -p "    现在删除并重建吗？输入 y 确认: " ANSWER; fi
    if [ "$ANSWER" != "y" ] && [ "$ANSWER" != "Y" ]; then
      echo "已取消。手动删除命令: aws cloudformation delete-stack --region $REGION --stack-name $STACK"
      exit 1
    fi
    aws_ cloudformation delete-stack --stack-name "$STACK"
    aws_ cloudformation wait stack-delete-complete --stack-name "$STACK"
    STATUS=NONE
    ;;
  *_IN_PROGRESS)
    echo "堆栈 $STACK 正在变更中（$STATUS），请 1-2 分钟后再运行本脚本。" >&2
    exit 1
    ;;
  CREATE_COMPLETE|UPDATE_COMPLETE|UPDATE_ROLLBACK_COMPLETE|IMPORT_COMPLETE|IMPORT_ROLLBACK_COMPLETE)
    echo "==> 堆栈已存在（$STATUS）。"
    echo "    如果这不是你本人创建的，请换一个后缀重新运行。"
    ;;
  *)
    echo "堆栈 $STACK 状态异常（$STATUS），请联系讲师。" >&2
    exit 1
    ;;
esac

# ---------------------------------------------------------------- deploy
DEPLOY=0
PARAMS=("LabSuffix=$SUFFIX")
if [ "$STATUS" = "NONE" ]; then
  DEPLOY=1
  PARAMS+=("InstanceType=$INSTANCE_TYPE")
  echo "==> 选择网络（机型 $INSTANCE_TYPE）..."
  AZ_LIST="$(aws_ ec2 describe-instance-type-offerings --location-type availability-zone \
    --filters "Name=instance-type,Values=$INSTANCE_TYPE" \
    --query 'InstanceTypeOfferings[].Location' --output text | tr -s '[:space:]' '\n' | sort)"
  if [ -z "$AZ_LIST" ]; then
    echo "区域 $REGION 不支持机型 $INSTANCE_TYPE。" >&2
    exit 1
  fi
  AZ_CSV="$(echo "$AZ_LIST" | paste -sd, -)"
  DEFAULT_VPC="$(aws_ ec2 describe-vpcs --filters Name=is-default,Values=true \
    --query 'Vpcs[0].VpcId' --output text 2>/dev/null || echo None)"
  SUBNET="None"
  if [ -n "$DEFAULT_VPC" ] && [ "$DEFAULT_VPC" != "None" ]; then
    SUBNET="$(aws_ ec2 describe-subnets --filters "Name=vpc-id,Values=$DEFAULT_VPC" \
      "Name=default-for-az,Values=true" "Name=availability-zone,Values=$AZ_CSV" \
      --query 'sort_by(Subnets,&AvailabilityZone)[0].SubnetId' --output text 2>/dev/null || echo None)"
  fi
  if [ -n "$SUBNET" ] && [ "$SUBNET" != "None" ]; then
    echo "    使用默认 VPC: $DEFAULT_VPC  子网: $SUBNET"
    PARAMS+=("VpcId=$DEFAULT_VPC" "SubnetId=$SUBNET")
  else
    FIRST_AZ="$(echo "$AZ_LIST" | head -n1)"
    echo "    没有可用的默认 VPC，将新建一个小 VPC（可用区 $FIRST_AZ）。"
    VPC_COUNT="$(aws_ ec2 describe-vpcs --query 'length(Vpcs)' --output text 2>/dev/null || echo 0)"
    if [ "${VPC_COUNT:-0}" -ge 5 ]; then
      echo "    注意: 本区域已有 $VPC_COUNT 个 VPC，可能达到默认上限 5 个，创建可能失败（请联系讲师）。"
    fi
    PARAMS+=("NewSubnetAz=$FIRST_AZ")
  fi
elif [ "${S12LAB_UPDATE:-0}" = "1" ]; then
  DEPLOY=1
  if [ -n "${S12LAB_INSTANCE_TYPE:-}" ]; then PARAMS+=("InstanceType=$S12LAB_INSTANCE_TYPE"); fi
  echo "==> S12LAB_UPDATE=1: 将更新堆栈。注意: 如果期间发布了新的 AL2023 AMI，EC2 会被重建，盘上数据会丢失。"
fi

if [ "$DEPLOY" = "1" ]; then
  echo "==> 正在创建/更新 EC2（约 3-5 分钟，请勿关闭窗口）..."
  if ! aws_ cloudformation deploy \
      --template-file "$TEMPLATE" \
      --stack-name "$STACK" \
      --capabilities CAPABILITY_NAMED_IAM \
      --parameter-overrides "${PARAMS[@]}" \
      --tags s12lab:lab=scenario12 "s12lab:owner=$SUFFIX" \
      --no-fail-on-empty-changeset; then
    echo "创建失败。失败原因如下（请截图发给讲师）:" >&2
    aws_ cloudformation describe-stack-events --stack-name "$STACK" --max-items 40 \
      --query "StackEvents[?contains(ResourceStatus,'FAILED')].[LogicalResourceId,ResourceStatusReason]" \
      --output table >&2 || true
    exit 1
  fi
fi

INSTANCE_ID="$(stack_out InstanceId)"
CONNECT_URL="$(stack_out SessionManagerUrl)"
echo "==> 实例 ID:   $INSTANCE_ID"
echo "==> 登录链接:  $CONNECT_URL"

# ---------------------------------------------------------------- wait for SSM
echo "==> 等待 EC2 在 Session Manager 中上线（最多约 5 分钟）..."
PING="None"
for i in $(seq 1 20); do
  PING="$(aws_ ssm describe-instance-information \
    --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
    --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || echo None)"
  if [ "$PING" = "Online" ]; then break; fi
  echo "    [$i/20] 尚未上线（$PING），15 秒后再查..."
  sleep 15
done

echo
echo "==================== s12lab EC2 ===================="
echo "  后缀:      $SUFFIX"
echo "  区域:      $REGION"
echo "  实例 ID:   $INSTANCE_ID"
echo "  登录链接:  $CONNECT_URL"
if [ "$PING" = "Online" ]; then
  echo "  状态:      已上线，可以登录"
else
  echo "  状态:      还未上线。再等 2-3 分钟后打开登录链接；10 分钟后仍不行请联系讲师。"
fi
echo "  下一步:    打开登录链接 → 点击 Connect / 连接 → 在命令行中先执行:"
echo "             sudo su - ec2-user"
echo "===================================================="
