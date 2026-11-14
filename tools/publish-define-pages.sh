#!/usr/bin/env bash
# 定义环：把鱼骨图（adlc-fishbone skill）和判据表（adlc-criteria skill）生成自包含网页，连同 fishbone.drawio
# 上传到你的私有 S3 桶，打印控制台链接（网页点「打开」，drawio 点「下载」后用 app.diagrams.net 打开）。
#   ./tools/publish-define-pages.sh outputs/fishbone/<组名>-<日期>              # 生成并上传
#   ./tools/publish-define-pages.sh outputs/fishbone/<组名>-<日期> --no-upload  # 只生成本地网页，不调用 AWS
# 改了 fishbone-data.json / criteria.json 后重跑即可：同名对象被覆盖，链接不变。逻辑见 labkit/define_pages.py。
set -euo pipefail
cd "$(dirname "$0")/.."
_local=0   # exact flags only (argparse runs with allow_abbrev=False, so --no-up is rejected there too)
for _a in "$@"; do case "$_a" in --no-upload|-h|--help) _local=1 ;; esac; done
if [ "$_local" = 1 ]; then   # local only: no lab config (LAB_SUFFIX) and no AWS needed
  _ADLC_PY="python3"; [ -x .venv/bin/python ] && _ADLC_PY=".venv/bin/python"
else
  source ./00-config.sh
fi
exec "${_ADLC_PY}" -m labkit.define_pages "$@"
