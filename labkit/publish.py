"""Upload a generated HTML page to the lab's private S3 bucket and print an S3 console link.

The bucket blocks all public access; the page opens from the S3 console with your own
console login (Object -> Open). Nothing is made publicly reachable: the upload sets no ACL,
and it is refused (fail closed) unless all four S3 Block Public Access settings are on.

Callers: 32-compare.sh, 34-triage-report.sh, labkit/data_pages.py (tools/publish-data-pages.sh).
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import aws

PAB_KEYS = ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")


class NotPrivateError(RuntimeError):
    """The bucket is not provably private; nothing was uploaded."""


def check_private(s3, bucket: str) -> None:
    """Raise NotPrivateError unless the bucket's own Block Public Access has all four settings on."""
    fix = (f"aws s3api put-public-access-block --bucket {bucket} --public-access-block-configuration "
           "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true")
    try:
        conf = s3.get_public_access_block(Bucket=bucket).get("PublicAccessBlockConfiguration") or {}
    except Exception as e:  # noqa: BLE001 - not set, or not readable: either way not provably private
        raise NotPrivateError(f"无法确认桶 {bucket} 已禁止公开访问（{type(e).__name__}），为避免页面被公开，未上传。"
                              f"\n   确认是你自己的实验桶后，开启全部四项“阻止公开访问”再重试：\n   {fix}") from e
    off = [k for k in PAB_KEYS if conf.get(k) is not True]
    if off:
        raise NotPrivateError(f"桶 {bucket} 没有完全禁止公开访问（未开启：{', '.join(off)}），为避免页面被公开，未上传。"
                              f"\n   开启全部四项后重试：\n   {fix}")


def publish(path: str) -> str:
    from .runtime import ensure_bucket
    p = Path(path)
    b = ensure_bucket()
    s3 = aws.client("s3")
    check_private(s3, b)
    key = f"reports/{p.name}"
    s3.upload_file(str(p), b, key, ExtraArgs={"ContentType": "text/html; charset=utf-8"})
    r = aws.cfg()["AWS_REGION"]
    url = f"https://{r}.console.aws.amazon.com/s3/object/{b}?region={r}&prefix={key}"
    print(f"☁️  已上传到私有桶 s3://{b}/{key}")
    print(f"👉 在笔记本浏览器打开（需已登录控制台），点右上角「打开」查看：\n   {url}")
    return url


if __name__ == "__main__":
    try:
        publish(sys.argv[1])
    except NotPrivateError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
