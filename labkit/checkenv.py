"""01-check-env: verify tools, identity, config and Bedrock model access."""
from __future__ import annotations

import importlib.metadata as md
import re
import sys

from . import aws


def ok(msg):
    print(f"  ✅ {msg}")


def bad(msg):
    print(f"  ❌ {msg}")


def imds_region():
    """Region of this EC2 via IMDSv2 (None when not on EC2)."""
    import urllib.request
    try:
        tok = urllib.request.urlopen(urllib.request.Request(
            "http://169.254.169.254/latest/api/token", method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"}), timeout=1).read().decode()
        return urllib.request.urlopen(urllib.request.Request(
            "http://169.254.169.254/latest/meta-data/placement/region",
            headers={"X-aws-ec2-metadata-token": tok}), timeout=1).read().decode()
    except Exception:  # noqa: BLE001
        return None


def ping_model(model_id: str) -> tuple:
    br = aws.client("bedrock-runtime")
    try:
        r = br.converse(modelId=model_id, messages=[{"role": "user", "content": [{"text": "只回复两个字：收到"}]}],
                        inferenceConfig={"maxTokens": 20})
        txt = r["output"]["message"]["content"][0]["text"].strip()
        return True, txt[:40]
    except Exception as e:  # noqa: BLE001 - show the reason to the participant
        return False, f"{type(e).__name__}: {str(e)[:200]}"


def main() -> int:
    c = aws.cfg()
    failed = 0
    print("① Python 与依赖")
    if sys.version_info < (3, 10):
        bad(f"Python {sys.version.split()[0]} 太旧，需要 3.10+（请先 source .venv/bin/activate）"); failed += 1
    else:
        ok(f"Python {sys.version.split()[0]}")
    for p in ["strands-agents", "bedrock-agentcore", "aws-opentelemetry-distro", "boto3"]:
        try:
            ok(f"{p} {md.version(p)}")
        except md.PackageNotFoundError:
            bad(f"{p} 未安装（运行 bash ec2/setup-ec2.sh）"); failed += 1

    print("② 配置与身份")
    ok(f"区域 {c['AWS_REGION']} · 个人后缀 {c['LAB_SUFFIX']} · 资源名前缀 {c['LAB_NAME']}")
    ec2_region = imds_region()
    if ec2_region and ec2_region != c["AWS_REGION"]:
        print(f"  ⚠️  这台 EC2 在 {ec2_region}，但实验配置的区域是 {c['AWS_REGION']}：资源会建到 {c['AWS_REGION']}。"
              f"如需改成 EC2 所在区域：在 config/local.env 写 AWS_REGION={ec2_region}")
    try:
        arn = aws.client("sts").get_caller_identity()["Arn"]
        ok("身份 " + re.sub(r"\d{12}", "<account>", arn))
    except Exception as e:  # noqa: BLE001
        bad(f"无法获取 AWS 身份: {e}"); return 1

    print("③ Bedrock 模型")
    seen = set()
    for role, mid in [("Agent 模型", c["AGENT_MODEL_ID"]), ("评审模型", c["JUDGE_MODEL_ID"])]:
        if mid in seen:
            continue
        seen.add(mid)
        good, info = ping_model(mid)
        if good:
            ok(f"{role} {mid} 可用（回复：{info}）")
        else:
            bad(f"{role} {mid} 不可用：{info}")
            print(f"     👉 一行切换到备选模型：./tools/use-model.sh nova   （{c['FALLBACK_MODEL_ID']}）")
            failed += 1

    print("④ CloudWatch Transaction Search")
    try:
        d = aws.client("xray").get_trace_segment_destination()
        if d.get("Destination") == "CloudWatchLogs" and d.get("Status") == "ACTIVE":
            ok("已开启（trace 写入 CloudWatch Logs 的 aws/spans）")
        else:
            print(f"  ⚠️  未开启（当前 {d.get('Destination')}/{d.get('Status')}）→ 运行 ./02-enable-observability.sh")
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️  无法查询 Transaction Search 状态: {e}")

    print()
    if failed:
        print(f"❌ 有 {failed} 项检查未通过，请按提示处理后重跑 ./01-check-env.sh")
        return 1
    print("✅ 环境检查通过。下一步：./02-enable-observability.sh（每个账号/区域只需一次）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
