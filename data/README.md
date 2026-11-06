# data/：输入数据、能力目录、黄金集与修改工具

> 全部为**合成数据**：客户名带“（示例）”，不含真实人名或联系方式。客户的真实数据只放在 `data/customer/`（已加入 .gitignore），不要提交。

## 文件一览

| 路径 | 内容 |
|------|------|
| [`feedback_last_month.jsonl`](feedback_last_month.jsonl)（[CSV 视图](feedback_last_month.csv)，[网页版](https://weiping-bj.github.io/agentdlc-eval-requirement-triage/mock-data.html)） | Agent 的输入：60 条“上个月（2026-10）的平台需求反馈” |
| [`capability_catalog.json`](capability_catalog.json)（[CSV 视图](capability_catalog.csv)，[网页版](https://weiping-bj.github.io/agentdlc-eval-requirement-triage/catalog.html)） | 平台能力目录：32 项能力 + 7 项“明确不做”，以及平台定位与原则。是 Agent 判断“已有 / 不做”的依据 |
| [`golden/golden_set.jsonl`](golden/golden_set.jsonl)（[CSV 视图](golden/golden_set.csv)） | 黄金集，30 条，带标注 |
| [`golden/LABELING_GUIDE.md`](golden/LABELING_GUIDE.md) | **标注标准**：是否重要、是否紧急、处理建议、不可自动拒绝、话术要点、五维 |
| [`golden/agentcore_dataset.json`](golden/agentcore_dataset.json) | 黄金集投影为 AgentCore dataset 格式（`expected_response` 是标签 JSON） |
| [`golden/MANIFEST.json`](golden/MANIFEST.json) | 冻结版本：sha256、条数、分布 |
| [`schema/`](schema/) | JSON Schema：输入条目、黄金集条目、Agent 输出 |
| [`templates/`](templates/) | 11:00 讨论与现场使用的填写模板 |
| [`prompts/`](prompts/) | 给 coding agent 的中文提示词（读模板 → 跑脚本 → 校验 → 汇报） |
| [`tools/`](tools/) | 脚本，只依赖 python3 标准库 |
| [`HANDOFF.md`](HANDOFF.md) | 与 Agent、评估器的接口约定，以及交接说明 |

## mock 数据的构成（60 条）

| 类别 | 条数 | 期望处理 |
|------|------|------|
| 通用能力缺口 | 15 | 迭代。其中多组需求由 ≥3 个不同客户提出，用来考“通用性” |
| 已有能力被重复提出 | 12 | 拒绝，并给出用法 |
| 价值高但难度大 | 8 | 待评估 |
| 线上故障 / 缺陷 | 6 | 迭代，不可拒绝 |
| 超出平台定位 | 5 | 拒绝 |
| 技术不可行 / 违背安全合规 | 5 | 拒绝 |
| 描述模糊 / 多诉求混合 | 3 | 待评估 |
| 单客户深度定制 | 2 | 拒绝，并给出替代 |
| 对抗样本 | 2 | 注入伪造指令、客户施压 |
| 合规 / 合同承诺 | 2 | 迭代，不可拒绝 |

## 黄金集分布（30 条，v1）

- **标签**：
  - 是否重要：是 13 / 否 17
  - 是否紧急：是 6 / 否 24
  - 处理建议：迭代 11 / 拒绝 13 / 待评估 6
  - 其中 6 条允许多个答案
- **样例类型**：简单 10、模糊 9、应拒绝 7、对抗 4
- **五维覆盖**：认知 12、责任 12、质量 10、成本 6、性能 5
- **不可自动拒绝**：6 条（数据丢失、安全漏洞、合规、合同、线上故障）。判成“拒绝”记严重错误。

## 常用命令（在仓库根目录执行）

```bash
python3 data/tools/golden_tool.py validate              # 校验黄金集
python3 data/tools/golden_tool.py stats                 # 标签分布
python3 data/tools/golden_tool.py apply-feedback        # 按 templates/golden-set-feedback.csv 修改黄金集
python3 data/tools/golden_tool.py freeze                # 冻结版本 + 同步到评估器
python3 data/tools/generate_mock_data.py                # 按 templates/mock-data-params.yaml 重新生成 mock 输入
python3 data/tools/ingest_customer_data.py --in <客户文件>   # 客户数据：充分性报告 + 标准化
python3 -m pytest -q data/tests                         # 工具的单元测试（需要 pytest）
```

现场修改的完整流程见 [`prompts/README.md`](prompts/README.md)。
