"""Upload a generated HTML page to the lab's private S3 bucket and print an S3 console link.

The bucket blocks all public access; the page opens from the S3 console with your own
console login (Object -> Open). Nothing is made publicly reachable.
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import aws


def publish(path: str) -> str:
    from .runtime import ensure_bucket
    p = Path(path)
    b = ensure_bucket()
    key = f"reports/{p.name}"
    aws.client("s3").upload_file(str(p), b, key, ExtraArgs={"ContentType": "text/html; charset=utf-8"})
    r = aws.cfg()["AWS_REGION"]
    url = f"https://{r}.console.aws.amazon.com/s3/object/{b}?region={r}&prefix={key}"
    print(f"☁️  已上传到私有桶 s3://{b}/{key}")
    print(f"👉 在笔记本浏览器打开（需已登录控制台），点右上角「打开」查看：\n   {url}")
    return url


if __name__ == "__main__":
    publish(sys.argv[1])
