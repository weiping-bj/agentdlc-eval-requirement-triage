# 任务：把标签方案从“是/否”切换为“P0/P1/P2 分级”

讨论中选择了方案 B（`discussion-feedback.md` 第 1.1 节）。请完成切换。

## 步骤
1. 读取第 1.1 节和 1.2 节：
   - 等级怎么算对：完全一致 → `S12_LEVEL_TOLERANCE=0`；相差一级 → `=1`；
   - P0、P1、P2 各自的口径。
2. 运行 `python3 data/tools/golden_tool.py fill-levels`。缺少 `expected.level` 的条目会先按默认规则推导：重要且紧急 = P0，重要不紧急 = P1，其它 = P2。
3. 如果讨论给的 P0/P1/P2 口径与默认推导不同：
   1. 逐条复核。
   2. 把需要改的写进 `data/templates/golden-set-feedback.csv`（操作=修改，填“优先级”列）。
   3. 运行 `python3 data/tools/golden_tool.py --scheme level apply-feedback`。
4. 运行 `python3 data/tools/golden_tool.py --scheme level freeze`。
5. 更新 `data/golden/LABELING_GUIDE.md` 第 6 节，写入现场确定的口径。
6. 告诉我两件事：
   1. 评估器 Lambda 需要设置环境变量 `S12_LABEL_SCHEME=level` 和 `S12_LEVEL_TOLERANCE=<0 或 1>`，然后重新部署。分级模式下打分项为“优先级 + 处理建议”。
   2. Agent 的提示词需要要求输出 `level` 字段。
   本地打分用：`python3 evaluators/s12_label_accuracy/batch_score.py --scheme level --level-tolerance <0|1> --results <文件>`。
