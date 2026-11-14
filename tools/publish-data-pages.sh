#!/usr/bin/env bash
# 手册 2.1（可选）/ 扩展「按讨论意见重新生成模拟数据」：把 EC2 上“当前”的能力目录、mock 需求、黄金集生成自包含网页，上传到你的私有 S3 桶，打印控制台链接。
#   ./tools/publish-data-pages.sh                 # 生成 3 页并上传（桶禁止公开访问，用控制台登录后「打开」）
#   ./tools/publish-data-pages.sh --no-upload     # 只生成本地文件 reports/data-pages/*.html，不调用 AWS
#   ./tools/publish-data-pages.sh --only golden   # 只做一页：catalog / mock / golden
# 数据再改后重跑即可：同名对象被覆盖，链接不变。逻辑见 labkit/data_pages.py。
set -euo pipefail
cd "$(dirname "$0")/.."
_local=0   # exact flags only (argparse runs with allow_abbrev=False, so --no-up is rejected there too)
for _a in "$@"; do case "$_a" in --no-upload|-h|--help) _local=1 ;; esac; done
if [ "$_local" = 1 ]; then   # local only: no lab config (LAB_SUFFIX) and no AWS needed
  _ADLC_PY="python3"; [ -x .venv/bin/python ] && _ADLC_PY=".venv/bin/python"
else
  source ./00-config.sh
fi
exec "${_ADLC_PY}" -m labkit.data_pages "$@"
