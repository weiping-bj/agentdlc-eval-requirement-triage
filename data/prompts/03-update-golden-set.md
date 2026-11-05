# 任务：按讨论结论更新黄金集

你是本仓库（场景 12：需求价值打分 Agent 实验）的评估数据助手。11:00 讨论已经确定了标注口径，并对样例黄金集提了修改意见。请据此更新黄金集，并冻结一个新版本。

## 输入（先读）
1. `data/templates/discussion-feedback.md`：
   - 第 1 节：标签方案与口径；
   - 第 2 节：不可自动拒绝的规则、话术要求；
   - 第 4 节：五维要点。
2. `data/templates/golden-set-feedback.csv`：逐条修改意见。“操作”列取值为 保留 / 修改 / 删除 / 新增。
3. 当前黄金集 `data/golden/golden_set.jsonl` 和标注标准 `data/golden/LABELING_GUIDE.md`。
4. `data/capability_catalog.json` 与 `data/feedback_last_month.jsonl`：用于判断“已有能力”和“几个客户提过”。

## 步骤
1. **对比口径**：把讨论口径（第 1、2 节）和 `LABELING_GUIDE.md` 对比。
   1. 列出有变化的条款。
   2. 修改 `LABELING_GUIDE.md` 对应段落，保持原结构，版本号 +1。
2. **按新口径复核**：逐条检查现有黄金集，找出因口径变化需要改标签的条目，例如：
   - “战略客户需求”改为不可自动拒绝；
   - “通用”的门槛从 3 个客户改为 5 个。
   对这些条目：
   - 在 `golden-set-feedback.csv` 中把“操作”改为“修改”，填新标签；
   - 在“标注理由”里写明依据哪条新口径。
   **不要直接改 jsonl。**
3. **补齐“新增”行**：对 csv 里“新增”的行，补齐缺的字段：
   - 需求原文；
   - 三项标签；
   - 可自动拒绝；
   - 五维、样例类型；
   - 拒绝时的拒绝要点和话术要点（用“；”分隔）。
   如果只给了 `feedback_id`，就从 `data/feedback_last_month.jsonl` 取原文。
4. **试运行**：运行 `python3 data/tools/golden_tool.py apply-feedback --dry-run`。有错误就修 csv，直到没有错误。
5. **写入**：运行 `python3 data/tools/golden_tool.py apply-feedback`。会自动备份、写入并导出 csv 和 dataset。
6. **冻结**：运行 `python3 data/tools/golden_tool.py freeze`。会校验、写 MANIFEST.json，并同步到 `evaluators/*/golden_set.json`。
7. **汇报**（中文，10 行以内）：
   - 改了几条、删了几条、增了几条；
   - 新的标签分布；
   - 五维覆盖；
   - 剩余警告，尤其是“紧急=是”太少这类会让准确率失真的问题；
   - 提醒我：评估器 Lambda 需要重新部署，新黄金集才生效（见 `evaluators/README.md`）。

## 约束与要点
- 黄金集每条必须**自包含需求原文**。`feedback_id` 不能重复。
- 标为“不可自动拒绝”的条目，期望建议里不能有“拒绝”。
- 模糊样例可以给多个可接受答案（“可接受建议”列，用 / 分隔），但不要超过总数的 25%，否则 70% 目标会被放水。
- 每类处理建议至少 3 条，五维每维至少 2 条；如果不满足，在汇报里提出补哪类样例。
- 所有数据保持合成，不写真实客户名或人名。
