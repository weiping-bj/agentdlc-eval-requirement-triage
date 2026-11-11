"""Upload a generated HTML page to the lab's private S3 bucket and print an S3 console link.

The bucket blocks all public access; the page opens from the S3 console with your own
console login (Object -> Open). Nothing is made publicly reachable: the upload sets no ACL,
only goes to a bucket this account owns (ExpectedBucketOwner), and only after all four S3
Block Public Access settings are confirmed on (re-applied if missing, the same rule as
tools/pull-templates.sh). If that cannot be confirmed, nothing is uploaded (fail closed).

Callers: 32-compare.sh, 34-triage-report.sh, labkit/data_pages.py (tools/publish-data-pages.sh).
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import aws


class NotPrivateError(RuntimeError):
    """The bucket could not be confirmed private and owned by this account; nothing was uploaded."""


def check_private(s3, bucket: str, owner: str) -> None:
    """Fail closed. Missing or partial Block Public Access is re-applied (the lab bucket is private by design);
    an unreadable setting or a bucket owned by another account raises NotPrivateError."""
    from .pull_templates import ensure_private
    try:
        if ensure_private(s3, bucket, owner) == "reapplied":
            print(f"🔒 桶 {bucket} 的四项“阻止公开访问”没有全开，已重新开启")
    except Exception as e:  # noqa: BLE001 - whatever went wrong, the bucket is not provably private
        raise NotPrivateError(f"无法确认桶 {bucket} 是本账号的私有桶（{type(e).__name__}: {e}），为避免页面被公开，未上传。"
                              "\n   网络或限流问题重试即可；否则请联系讲师") from e


def publish(path: str) -> str:
    from .runtime import ensure_bucket
    p = Path(path)
    b = ensure_bucket()
    s3 = aws.client("s3")
    owner = aws.account_id()
    check_private(s3, b, owner)
    key = f"reports/{p.name}"
    s3.upload_file(str(p), b, key, ExtraArgs={"ContentType": "text/html; charset=utf-8", "ExpectedBucketOwner": owner})
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
