# 任务：按讨论反馈重新生成 mock 需求数据

你是本仓库（场景 12：需求价值打分 Agent 实验）的数据助手。11:00 讨论后，客户对 mock 数据提了意见。请按意见更新 mock 数据。

## 输入（先读）
1. `data/templates/discussion-feedback.md` 第 5 节：客户对 mock 数据的意见。
2. `data/templates/mock-data-params.yaml`：生成参数，有注释。
3. 种子库 `data/tools/seed/pool.jsonl`：60 条带标注的合成需求。每行是输入字段加 `_meta`，`_meta` 里有类别、三项标签、五维和拒绝要点。
4. `data/capability_catalog.json`：新写的需求要和能力目录一致。
5. `data/golden/LABELING_GUIDE.md`：给新条目打 `_meta` 标签时按它来。

## 步骤
0. 如果第 5 节没填（还是 `【填写】`），或者写的都是“基本符合 / 无”这类意思：**不要改任何文件**，直接告诉我“第 5 节没有意见，可以跳过 2.4”，到此结束。
1. 把第 5 节的意见翻译成 `mock-data-params.yaml` 里的参数，并在回复里列出改了哪些参数：
   - 条数 → `count`；
   - 月份 → `month`；
   - 类别占比 → `category_mix`；
   - 行业 → `industries`；
   - 渠道 → `channels`。
2. 如果客户要的需求类型种子库里没有，或某类条数不够：
   1. 新建或追加到 `data/templates/extra-feedback.jsonl`。
   2. 格式与 `data/templates/extra-feedback.example.jsonl` 完全一致：一行一个 JSON，包含全部输入字段和 `_meta`。
   3. 要求：
      - 内容是真实感强的中文 CRM/PaaS 平台需求；
      - 客户名用“XX（示例）”这种明显合成的公司名；
      - 不出现任何真实人名、真实手机号或邮箱；
      - `_meta.category` 必须是 yaml 注释里列出的类别之一；
      - `_meta` 的标签按 LABELING_GUIDE 判断，`rationale` 写一句理由；
      - `_meta.golden_id` 填 null（新条目默认不进黄金集）；
      - 需要支撑“通用性”判断时，给同一诉求写 ≥3 个不同客户的条目。
3. 运行 `python3 data/tools/generate_mock_data.py`。看输出里的类别分布和“警告”，有缺口就回到第 2 步补写。它会同时刷新讨论用的网页版 `docs/mock-data.html`（生成文件，不要手改）。
4. 运行 `python3 data/tools/golden_tool.py validate`，确认黄金集不受影响。默认会保留全部黄金集样本。
5. 汇报（按这个顺序）：
   1. 改了哪些参数（`git diff data/templates/mock-data-params.yaml` 能看到的那些）；
   2. 把第 3 步 `generate_mock_data.py` 打印的类别分布**原样贴出来**，并逐条对照第 5 节的要求说明是否满足；
   3. 新增了哪些条目（编号 + 标题），没有就写“无”；
   4. 还剩哪些警告，没有就写“无”。

## 约束
- 不要修改种子库 `pool.jsonl` 和 `data/tools/` 下的脚本，新增内容只写进 `extra-feedback.jsonl`。
- 不要用 `--write-golden`，黄金集用 03 号提示词单独维护。
