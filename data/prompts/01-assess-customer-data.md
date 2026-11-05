# 任务：评估并标准化客户自带的需求数据

你是本仓库（场景 12：需求价值打分 Agent 实验）的数据助手。客户提供了一份“上个月的需求反馈”文件，路径是：`<客户文件路径>`（如果我没写路径，请先问我）。

## 背景
- 实验统一的输入格式见 `data/schema/feedback_item.schema.json`，样例见 `data/feedback_last_month.jsonl`。
- 判断三项标签（是否重要 / 是否紧急 / 处理建议）需要哪些信息，见 `data/golden/LABELING_GUIDE.md`。
- 这个仓库是**公开**的，客户的真实数据**绝不能**写进 `data/customer/` 以外的位置，也不能提交。

## 步骤
1. 运行：`python3 data/tools/ingest_customer_data.py --in <客户文件路径>`
   - 如果提示“找不到标题/需求描述列”，或报告里“字段识别”表映射错了：
     1. 参考 `data/templates/column-mapping.example.json`，新建 `data/customer/mapping.json`，写清“源列名 → 目标字段”。
     2. 加上 `--mapping data/customer/mapping.json` 重跑。
   - Excel 有多个工作表时，用 `--sheet <工作表名>`。
2. 阅读 `data/customer/report.md`，用 5 行以内的中文向我汇报：
   - 结论（充足 / 基本可用 / 不足）；
   - 最关键的 1~2 个缺项；
   - 是否带有历史判断、能否做黄金集候选。
3. 根据结论给出建议，**等我确认后再执行**：
   - **充足**：建议加 `--use-as-input` 重跑，把客户数据作为 Agent 输入（原 mock 文件会自动备份）。
   - **基本可用**：列出客户需要补充的字段。也可以建议与 mock 数据合并：把客户条目追加到 mock 文件末尾，`id` 不冲突即可。
   - **不足**：建议继续使用 mock 数据。用一句话说明原因，方便我说服客户，例如“缺少客户等级和反馈类型，Agent 无法判断重要和紧急，评估结果没有可比性”。
4. 如果客户数据带有历史判断（输出了 `data/customer/golden_candidates.jsonl`），并且三项齐全的有 ≥20 条，告诉我可以按 `data/prompts/03-update-golden-set.md` 把它们并入黄金集。只给建议，不要自动合并。

## 约束
- 不要修改 `data/tools/` 下的脚本。脚本报错时，把错误原文发给我。
- 不要把客户数据内容复制到聊天回复以外的任何地方。汇报里引用原文最多 1 条，并且要打码。
