#!/usr/bin/env bash
# 第 4、5 章：给已部署的 Runtime 发一条需求，打印 Agent 的结构化输出（对照黄金集）；--eval 再对这一个 session 跑评估器
#   ./46-try-one.sh FB-2610-009              # 黄金集或上月需求池里的一条（也可以写黄金集编号 G06）
#   ./46-try-one.sh --text "希望……"           # 自己写的一条需求
#   ./46-try-one.sh FB-2610-004 --eval       # 再跑一遍评估器（要等 trace 写入 CloudWatch，约 2–3 分钟）
#   ./46-try-one.sh FB-2610-004 --session <session id>   # 不再调用 Agent，只重新评估之前试过的那个 session
# 试一条不算一次运行：不写 .lab-state/runs.json，不进 43 / 45 / 51
set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh
exec "${_ADLC_PY}" -m labkit.try_one "$@"
