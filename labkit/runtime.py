"""B path: package the mock agent and deploy it to AgentCore Runtime (direct code deploy, no Docker).

  python -m labkit.runtime deploy [--version v0|v1]   # create or update the Runtime
  python -m labkit.runtime invoke --one FB-2610-001   # smoke test one item
  python -m labkit.runtime status
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import aws, items

ROOT = Path(aws.cfg()["LAB_ROOT"])
BUILD = ROOT / "build" / "runtime"
DATA_FILES = ["capability_catalog.json", "feedback_last_month.jsonl"]


def _deps_dir() -> Path:
    req = (ROOT / "agent" / "requirements.txt").read_bytes()
    py = aws.cfg()["RUNTIME_PYTHON"].replace("PYTHON_", "").replace("_", ".")
    h = hashlib.sha256(req + py.encode()).hexdigest()[:12]
    d = BUILD / f"deps-{h}"
    if (d / ".complete").exists():
        print(f"  ♻️  复用已下载的依赖 {d.relative_to(ROOT)}")
        return d
    shutil.rmtree(d, ignore_errors=True)
    print(f"  ⬇️  下载 linux/arm64 依赖（Python {py}，首次约 1-2 分钟）…")
    last = None
    for plat in ("aarch64-manylinux2014", "aarch64-manylinux_2_28", "aarch64-manylinux_2_34"):
        cmd = ["uv", "pip", "install", "--quiet", "--target", str(d), "--python-platform", plat,
               "--python-version", py, "--only-binary=:all:", "-r", str(ROOT / "agent" / "requirements.txt")]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            (d / ".complete").write_text(plat)
            return d
        last = r.stderr[-2000:]
        shutil.rmtree(d, ignore_errors=True)
    raise SystemExit(f"❌ 依赖下载失败：\n{last}")


def build_zip() -> Path:
    BUILD.mkdir(parents=True, exist_ok=True)
    deps = _deps_dir()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = BUILD / f"agent-{ts}.zip"

    def add(zf: zipfile.ZipFile, src: Path, arc: str):
        info = zipfile.ZipInfo(arc, date_time=(2026, 1, 1, 0, 0, 0))
        info.external_attr = (0o100644 << 16)
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, src.read_bytes())

    n = 0
    with zipfile.ZipFile(out, "w") as zf:
        for p in sorted(deps.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts and p.name != ".complete":
                add(zf, p, str(p.relative_to(deps))); n += 1
        add(zf, ROOT / "agent" / "main.py", "main.py"); n += 1
        for p in sorted((ROOT / "agent" / "s12agent").rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
                add(zf, p, "s12agent/" + str(p.relative_to(ROOT / "agent" / "s12agent"))); n += 1
        for f in DATA_FILES:
            src = ROOT / "data" / f
            if src.exists():
                add(zf, src, f"s12agent/agent_data/{f}"); n += 1
    print(f"  📦 打包完成：{out.relative_to(ROOT)}（{n} 个文件，{out.stat().st_size/1e6:.1f} MB）")
    return out


def ensure_bucket() -> str:
    s3, b, region = aws.client("s3"), aws.bucket_name(), aws.cfg()["AWS_REGION"]
    try:
        s3.head_bucket(Bucket=b)
    except Exception:  # noqa: BLE001
        kw = {} if region == "us-east-1" else {"CreateBucketConfiguration": {"LocationConstraint": region}}
        s3.create_bucket(Bucket=b, **kw)
        s3.put_public_access_block(Bucket=b, PublicAccessBlockConfiguration={
            "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
        s3.put_bucket_tagging(Bucket=b, Tagging={"TagSet": aws.tags_list()})
        print(f"  🪣 已创建 S3 桶 {b}")
    return b


def ensure_role() -> str:
    c, a, r = aws.cfg(), aws.account_id(), aws.cfg()["AWS_REGION"]
    trust = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": "bedrock-agentcore.amazonaws.com"}, "Action": "sts:AssumeRole",
        "Condition": {"StringEquals": {"aws:SourceAccount": a},
                      "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{r}:{a}:*"}}}]}
    inline = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["logs:DescribeLogStreams", "logs:CreateLogGroup", "logs:CreateLogStream",
                                       "logs:PutLogEvents", "logs:PutResourcePolicy"],
         "Resource": [f"arn:aws:logs:{r}:{a}:log-group:/aws/bedrock-agentcore/runtimes/*",
                      f"arn:aws:logs:{r}:{a}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"]},
        {"Effect": "Allow", "Action": ["logs:DescribeLogGroups"], "Resource": f"arn:aws:logs:{r}:{a}:log-group:*"},
        {"Effect": "Allow", "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules",
                                       "xray:GetSamplingTargets"], "Resource": "*"},
        {"Effect": "Allow", "Action": "cloudwatch:PutMetricData", "Resource": "*",
         "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}}},
        {"Effect": "Allow", "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream",
                                       "bedrock:Converse", "bedrock:ConverseStream"],
         "Resource": ["arn:aws:bedrock:*::foundation-model/*", f"arn:aws:bedrock:*:{a}:inference-profile/*"]},
        {"Effect": "Allow", "Action": ["bedrock-agentcore:GetWorkloadAccessToken",
                                       "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                                       "bedrock-agentcore:GetWorkloadAccessTokenForUserId"],
         "Resource": [f"arn:aws:bedrock-agentcore:{r}:{a}:workload-identity-directory/default",
                      f"arn:aws:bedrock-agentcore:{r}:{a}:workload-identity-directory/default/workload-identity/*"]},
    ]}
    return aws.ensure_role(c["RUNTIME_ROLE_NAME"], trust, inline, "s12lab AgentCore Runtime execution role")


def find_runtime(name: str):
    ctl = aws.client("bedrock-agentcore-control")
    token = None
    while True:
        kw = {"maxResults": 100}
        if token:
            kw["nextToken"] = token
        r = ctl.list_agent_runtimes(**kw)
        for rt in r.get("agentRuntimes", []):
            if rt.get("agentRuntimeName") == name:
                return rt
        token = r.get("nextToken")
        if not token:
            return None


def deploy(version: str) -> dict:
    c = aws.cfg()
    ctl = aws.client("bedrock-agentcore-control")
    print(f"▶ 部署 {c['RUNTIME_NAME']} · Agent 版本 {version} · 模型 {c['AGENT_MODEL_ID']} · 区域 {c['AWS_REGION']}")
    bucket = ensure_bucket()
    z = build_zip()
    key = f"runtime/{z.name}"
    aws.client("s3").upload_file(str(z), bucket, key)
    print(f"  ⬆️  已上传 s3://{bucket}/{key}")
    role_arn = ensure_role()
    print(f"  🔐 执行角色 {c['RUNTIME_ROLE_NAME']}")
    args = dict(
        agentRuntimeArtifact={"codeConfiguration": {
            "code": {"s3": {"bucket": bucket, "prefix": key}},
            "runtime": c["RUNTIME_PYTHON"], "entryPoint": ["opentelemetry-instrument", "main.py"]}},
        roleArn=role_arn,
        networkConfiguration={"networkMode": "PUBLIC"},
        protocolConfiguration={"serverProtocol": "HTTP"},
        environmentVariables={"AGENT_VERSION": version, "AGENT_MODEL_ID": c["AGENT_MODEL_ID"]},
        description=f"s12lab scenario 12 triage agent ({version})",
    )
    existing = find_runtime(c["RUNTIME_NAME"])
    for attempt in range(6):
        try:
            if existing:
                r = ctl.update_agent_runtime(agentRuntimeId=existing["agentRuntimeId"], **args)
                print(f"  🔁 更新已有 Runtime {existing['agentRuntimeId']}")
            else:
                r = ctl.create_agent_runtime(agentRuntimeName=c["RUNTIME_NAME"], tags=aws.tags_dict(), **args)
                print(f"  🆕 创建 Runtime {r['agentRuntimeId']}")
            break
        except ctl.exceptions.ValidationException as e:
            # freshly created IAM roles can take a few seconds to become assumable
            if "role" in str(e).lower() and attempt < 5:
                time.sleep(10)
                continue
            raise
    rid = r["agentRuntimeId"]

    def ready():
        g = ctl.get_agent_runtime(agentRuntimeId=rid)
        st = g["status"]
        if st.endswith("FAILED"):
            raise SystemExit(f"❌ Runtime 状态 {st}: {g.get('failureReason', '')}")
        print(f"  ⏳ Runtime 状态 {st}")
        return g if st == "READY" else None

    g = aws.wait_until(ready, "runtime READY", timeout=900, interval=15)
    state = {"agentRuntimeArn": g["agentRuntimeArn"], "agentRuntimeId": rid,
             "agentRuntimeVersion": g.get("agentRuntimeVersion"), "agentVersion": version,
             "modelId": c["AGENT_MODEL_ID"], "logGroup": f"/aws/bedrock-agentcore/runtimes/{rid}-DEFAULT",
             "serviceName": f"{c['RUNTIME_NAME']}.DEFAULT", "bucket": bucket, "codeKey": key,
             "deployedAt": datetime.now(timezone.utc).isoformat()}
    aws.save_state("runtime", state)
    print(f"✅ Runtime 就绪：{rid}（版本 {state['agentRuntimeVersion']}，Agent {version}）")
    print("   下一步：./20-B-deploy-runtime.sh invoke   （调用一次确认可用）")
    return state


def invoke_one(fid: str | None) -> int:
    st = aws.load_state("runtime")
    if not st:
        print("❌ 还没有部署 Runtime：先运行 ./20-B-deploy-runtime.sh"); return 1
    rows = items.load("golden")
    row = next((r for r in rows if items.fid_of(r) == fid), rows[0]) if fid else rows[0]
    import uuid
    sid = f"{aws.cfg()['LAB_NAME']}-smoke-{uuid.uuid4().hex}"
    from .batch import _runtime_one
    t0 = time.time()
    out = _runtime_one(row, st["agentRuntimeArn"], sid)
    print(f"需求 {items.fid_of(row)}：{items.item_of(row).get('title', '')}")
    print(json.dumps(out["result"], ensure_ascii=False, indent=2))
    print(f"✅ Runtime 调用成功，用时 {time.time()-t0:.1f}s，session = {sid}")
    return 0


def status() -> int:
    st = aws.load_state("runtime")
    print(json.dumps(st or {"status": "未部署"}, ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    d = sub.add_parser("deploy")
    d.add_argument("--version", default=os.environ.get("AGENT_VERSION") or aws.cfg()["AGENT_VERSION"])
    i = sub.add_parser("invoke")
    i.add_argument("--one")
    sub.add_parser("status")
    a = ap.parse_args(argv)
    if a.cmd in (None, "deploy"):
        deploy(getattr(a, "version", None) or aws.cfg()["AGENT_VERSION"])
        return 0
    if a.cmd == "invoke":
        return invoke_one(a.one)
    return status()


if __name__ == "__main__":
    sys.exit(main())
