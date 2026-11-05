"""Create the CloudWatch log group + stream that ADOT writes model I/O events to (A path)."""
from __future__ import annotations

import sys

from . import aws


def ensure(group: str, stream: str = "runtime-logs", retention: int = 7) -> None:
    logs = aws.client("logs")
    try:
        logs.create_log_group(logGroupName=group, tags=aws.tags_dict())
        print(f"  🆕 创建日志组 {group}")
    except logs.exceptions.ResourceAlreadyExistsException:
        print(f"  ✔ 日志组已存在 {group}")
    logs.put_retention_policy(logGroupName=group, retentionInDays=retention)
    try:
        logs.create_log_stream(logGroupName=group, logStreamName=stream)
    except logs.exceptions.ResourceAlreadyExistsException:
        pass


if __name__ == "__main__":
    ensure(sys.argv[1] if len(sys.argv) > 1 else aws.cfg()["LOCAL_LOG_GROUP"])
