"""The Bedrock models the lab may invoke (layer-1 allowlist).

Enforced on the CloudFormation paths (workshop-studio/static/adlc-lab.yaml, ec2/adlc-ec2.yaml): the EC2 instance
role (Claude Code and the lab scripts), the permissions boundary every lab-created role carries, and the roles
labkit creates at runtime (AgentCore Runtime, evaluator Lambda, online evaluation). The manual console path
(ec2/manual-iam-inline-policy.json on top of PowerUserAccess, roles without a boundary) only gets best-effort
Denies: it can escalate (see ec2/README.md) and is not an enforcement boundary. tests/test_model_allowlist.py
keeps every copy and every model id the lab is configured with in sync with this list.

  anthropic.claude-sonnet-5-5   agent (AGENT_MODEL_ID), LLM judge (JUDGE_MODEL_ID), Claude Code (ANTHROPIC_MODEL)
  anthropic.claude-haiku-5-5    Claude Code small/fast model (ANTHROPIC_SMALL_FAST_MODEL / DEFAULT_HAIKU_MODEL)
  amazon.nova-2-lite-v1:0       FALLBACK_MODEL_ID: judge fallback, ./tools/use-model.sh nova

The lab calls them through the `us.` cross-Region inference profiles, which route to the Regions below; a
profile call needs the profile ARN (calling Region) and the foundation-model ARN in every destination Region.
ROUTE_REGIONS was read from `aws bedrock get-inference-profile` (2026-11); re-check before an event, because a
Region AWS adds to a profile would see intermittent AccessDenied until it is listed here.
Stdlib only.
"""
from __future__ import annotations

ALLOWED_MODELS = ("anthropic.claude-sonnet-5-5", "anthropic.claude-haiku-5-5", "amazon.nova-2-lite-v1:0")
PROFILE_PREFIX = "us."
ROUTE_REGIONS = ("us-east-1", "us-east-2", "us-west-2")
INVOKE_ACTIONS = ("bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream")


def profile_ids() -> list[str]:
    return [PROFILE_PREFIX + m for m in ALLOWED_MODELS]


def invoke_resources(region: str, account: str) -> list[str]:
    """ARNs an allowlisted invocation touches: the `us.` profiles in `region`, the models in each route Region."""
    return ([f"arn:aws:bedrock:{region}:{account}:inference-profile/{p}" for p in profile_ids()]
            + [f"arn:aws:bedrock:{r}::foundation-model/{m}" for r in ROUTE_REGIONS for m in ALLOWED_MODELS])
