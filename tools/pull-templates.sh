#!/usr/bin/env bash
# 第 2.0 步：把上午在笔记本上填好的两份模板，从你的私有 S3 桶拷到 EC2 的 data/templates/。
#   ./tools/pull-templates.sh --prepare    # 先做：创建你的私有桶（已存在就跳过），打印桶名和控制台链接
#   ./tools/pull-templates.sh              # 在控制台上传后：拷贝 discussion-feedback.md 和 golden-set-feedback.csv
#   ./tools/pull-templates.sh --bucket <同组操作员的桶名>   # 一组多台 EC2：从同一个桶拉同一份文件
#   ./tools/pull-templates.sh --dry-run    # 只看会拷什么，不写文件
#   ./tools/pull-templates.sh --force      # 桶里是没改过的模板时，也覆盖 EC2 上已改过的文件
# 只会写 data/templates/ 下这两个文件（旧文件先备份到 data/templates/.backup/<时间>/）；
# 使用 EC2 实例角色，不需要任何密钥；重复运行是安全的。逻辑见 labkit/pull_templates.py。
set -euo pipefail
cd "$(dirname "$0")/.."
source ./00-config.sh
exec "${_S12_PY}" -m labkit.pull_templates "$@"
