"""Algorithm: LLM judge for rejection reason + customer reply quality (code-based variant).

Model resolution (first hit wins):
  env JUDGE_MODEL_ID -> config/local.env -> config/lab.env (JUDGE_MODEL_ID) -> us.anthropic.claude-sonnet-5-5
Fallback model (used when the primary is not accessible / not valid in the region):
  env FALLBACK_MODEL_ID -> config files (FALLBACK_MODEL_ID) -> us.amazon.nova-2-lite-v1:0
Region: env JUDGE_REGION -> AWS_REGION -> AWS_DEFAULT_REGION -> config AWS_REGION -> us-west-2

Score: each of 5 dimensions 1..5 -> value = mean((s-1)/4); any red flag caps value at 0.4.
Label: Pass (value >= PASS_THRESHOLD and no red flag) | Fail.
"""
from __future__ import annotations

import json
import os
import random
import time
from typing import Any, Callable, Dict, List, Optional

import rubric

DEFAULT_MODEL = "us.anthropic.claude-sonnet-5-5"
DEFAULT_FALLBACK = "us.amazon.nova-2-lite-v1:0"
FALLBACK_ERRORS = {"AccessDeniedException", "ValidationException", "ResourceNotFoundException",
                   "UnrecognizedClientException", "ModelNotReadyException"}
RETRY_ERRORS = {"ThrottlingException", "TooManyRequestsException", "ServiceUnavailableException",
                "InternalServerException", "ModelTimeoutException"}


def _find_config_dir(start: str) -> Optional[str]:
    d = os.path.abspath(start)
    for _ in range(6):
        cand = os.path.join(d, "config", "lab.env")
        if os.path.exists(cand):
            return os.path.join(d, "config")
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return None


def read_env_file(path: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not path or not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def repo_config(start: Optional[str] = None) -> Dict[str, str]:
    cdir = _find_config_dir(start or os.path.dirname(os.path.abspath(__file__)))
    if not cdir:
        return {}
    cfg = read_env_file(os.path.join(cdir, "lab.env"))
    cfg.update(read_env_file(os.path.join(cdir, "local.env")))
    return cfg


def resolve_models(cfg: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    cfg = repo_config() if cfg is None else cfg
    return {
        "model": os.environ.get("JUDGE_MODEL_ID") or cfg.get("JUDGE_MODEL_ID") or DEFAULT_MODEL,
        "fallback": os.environ.get("FALLBACK_MODEL_ID") or cfg.get("FALLBACK_MODEL_ID") or DEFAULT_FALLBACK,
        "region": (os.environ.get("JUDGE_REGION") or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
                   or cfg.get("AWS_REGION") or "us-west-2"),
    }


class BedrockJudgeClient:
    """Minimal Converse wrapper with bounded retry (throttling) and model fallback."""

    def __init__(self, model: str, fallback: Optional[str], region: str, client=None, sleep: Callable[[float], None] = time.sleep):
        self.model, self.fallback, self.region = model, fallback, region
        self._client = client
        self._sleep = sleep
        self.used_model: Optional[str] = None
        self.usage = {"inputTokens": 0, "outputTokens": 0}

    def _c(self):
        if self._client is None:
            import boto3
            from botocore.config import Config
            self._client = boto3.client("bedrock-runtime", region_name=self.region,
                                        config=Config(read_timeout=90, connect_timeout=10, retries={"max_attempts": 2}))
        return self._client

    def _call(self, model: str, system: str, prompt: str) -> str:
        kwargs = {"modelId": model, "messages": [{"role": "user", "content": [{"text": prompt}]}],
                  "inferenceConfig": {"maxTokens": 1200, "temperature": 0.0}}
        if system:
            kwargs["system"] = [{"text": system}]
        last = None
        for attempt in range(4):
            try:
                resp = self._c().converse(**kwargs)
                usage = resp.get("usage") or {}
                self.usage["inputTokens"] += int(usage.get("inputTokens") or 0)
                self.usage["outputTokens"] += int(usage.get("outputTokens") or 0)
                blocks = resp["output"]["message"]["content"]
                return "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
            except Exception as e:  # botocore ClientError or test doubles
                code = getattr(e, "response", {}).get("Error", {}).get("Code", "") if hasattr(e, "response") else ""
                last = e
                if code in RETRY_ERRORS and attempt < 3:
                    self._sleep(min(20.0, (2 ** attempt) + random.random()))
                    continue
                raise
        raise last  # pragma: no cover

    def complete(self, system: str, prompt: str) -> str:
        try:
            out = self._call(self.model, system, prompt)
            self.used_model = self.model
            return out
        except Exception as e:
            code = getattr(e, "response", {}).get("Error", {}).get("Code", "") if hasattr(e, "response") else ""
            if self.fallback and self.fallback != self.model and code in FALLBACK_ERRORS:
                out = self._call(self.fallback, system, prompt)
                self.used_model = self.fallback
                return out
            raise


def parse_verdict(text: str) -> Dict[str, Any]:
    """Parse the judge JSON; tolerant to fences / surrounding prose."""
    import span_adapter as sa
    for v in sa.json_values_in_text(text or ""):
        if isinstance(v, dict) and isinstance(v.get("scores"), dict):
            return v
    raise ValueError(f"judge output is not the expected JSON: {(text or '')[:200]!r}")


def score_verdict(verdict: Dict[str, Any]) -> Dict[str, Any]:
    keys = [k for k, _, _ in rubric.DIMENSIONS]
    scores = {}
    for k in keys:
        try:
            s = float(verdict["scores"].get(k))
        except (TypeError, ValueError):
            s = 1.0
        scores[k] = max(1.0, min(5.0, s))
    value = sum((s - 1.0) / 4.0 for s in scores.values()) / len(scores)
    flags = [f for f in (verdict.get("red_flags") or []) if isinstance(f, str) and f.strip() and f.strip() not in ("无", "none", "None")]
    if flags:
        value = min(value, 0.4)
    value = round(value, 4)
    label = "Pass" if (value >= rubric.PASS_THRESHOLD and not flags) else "Fail"
    return {"value": value, "label": label, "scores": scores, "red_flags": flags}


def explain(result: Dict[str, Any], verdict: Dict[str, Any], fid: str, model: Optional[str], golden_id: Optional[str] = None,
            note: str = "") -> str:
    names = {k: n for k, n, _ in rubric.DIMENSIONS}
    dims = "，".join(f"{names[k]} {int(v)}" for k, v in result["scores"].items())
    txt = f"需求 {fid}（{golden_id or '-'}）拒绝话术评审 {result['value']}：{dims}。"
    if result["red_flags"]:
        txt += "红线：" + "；".join(result["red_flags"]) + "。"
    miss = [m for m in (verdict.get("missing_points") or []) if isinstance(m, str)]
    if miss:
        txt += "未覆盖要点：" + "；".join(miss[:4]) + "。"
    if verdict.get("comment"):
        txt += "点评：" + str(verdict["comment"])[:300]
    txt += f" [评审模型 {model}]{note}"
    return txt[:2000]


def judge_rejection(client, requirement: str, output: Dict[str, Any], gold: Optional[Dict[str, Any]] = None,
                    capability_hints: str = "") -> Dict[str, Any]:
    gold = gold or {}
    prompt = rubric.judge_user_prompt(requirement, output.get("reject_reason", ""), output.get("customer_reply", ""),
                                      output.get("reasoning", ""), gold.get("reject_points") or (),
                                      gold.get("reply_points") or (), capability_hints)
    text = client.complete(rubric.judge_system_prompt(), prompt)
    verdict = parse_verdict(text)
    res = score_verdict(verdict)
    if gold and gold.get("can_auto_reject") is False:
        res["red_flags"].append("拒绝了不应自动拒绝的需求（黄金集标注）")
        res["value"] = min(res["value"], 0.0)
        res["label"] = "Fail"
    return {"result": res, "verdict": verdict, "model": getattr(client, "used_model", None)}


def requirement_text(user_text: str, gold: Optional[Dict[str, Any]] = None) -> str:
    if user_text:
        return user_text[:4000]
    g = gold or {}
    return f"{g.get('title') or ''}".strip() or "（trace 中未找到需求原文）"


def is_rejection(output: Dict[str, Any]) -> bool:
    return output.get("decision") == "拒绝"


def summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    vals = [r["value"] for r in results]
    return {"n": len(vals), "mean": round(sum(vals) / len(vals), 4) if vals else None,
            "pass_rate": round(sum(1 for r in results if r["label"] == "Pass") / len(vals), 4) if vals else None}


def dumps(o: Any) -> str:
    return json.dumps(o, ensure_ascii=False)
