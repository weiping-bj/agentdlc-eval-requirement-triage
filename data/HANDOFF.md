# data/ + evaluators/ 交接说明

> 全部为合成数据。本文件记录接口约定的最终状态。过程记录见 test-run.log（测试日志）与 code-review.md（独立评审：23 条发现，21 条已修复，2 条说明后保留）。

## 1. 交付了什么

- **mock 输入**：60 条“上个月（2026-10）平台需求反馈”。
  - 覆盖 10 类：通用缺口、已有能力、单客户定制、超出定位、安全红线、模糊、线上故障、价值高但难度大、对抗、合规与合同。
  - 含 5 组由 ≥3 个不同客户提出的相似需求，用来考“通用性”判断。
- **平台能力目录**：32 项能力 + 7 项“明确不做”，以及平台定位与原则。
- **黄金集**：30 条。每条自包含需求原文，标注三项标签、可接受答案、可否自动拒绝、拒绝要点与话术要点、五维、样例类型。
  - G01–G05 与 `docs/discussion.html` 的样例表一致。
  - 已冻结 v1，见 `data/golden/MANIFEST.json`。
- **标注标准**（中文）：`data/golden/LABELING_GUIDE.md`。lead 的 V1 提示词已按它对齐。
- **工具**（纯标准库，Python 3.9+）：
  - 客户数据校验与标准化：`ingest_customer_data.py`，支持 csv/xlsx/json，输出充分性报告并给个人信息打码；
  - mock 数据重新生成：`generate_mock_data.py`；
  - 黄金集校验、修改、冻结：`golden_tool.py`。
- **填写模板 + coding agent 中文提示词**：`data/templates/`（4 个模板 + 1 个样例）与 `data/prompts/`（5 个提示词）。
- **评估器**：
  - 标签准确率（代码型）；
  - 拒绝话术质量（代码型 LLM 评审）；
  - 原生 LLM-as-judge 配置。
- **测试**：共 103 个，含真实 Bedrock 调用测试（Claude Sonnet 5.5）。
  - Python 3.12：103 个全部通过；
  - Python 3.9：99 个通过，4 个跳过（可选依赖与 Bedrock 测试）。

## 2. 文件地图

| 路径 | 用途 |
|------|------|
| `data/feedback_last_month.jsonl`（`.csv`） | Agent 输入 |
| `data/capability_catalog.json` | `{"platform", "capabilities":[...]}`。字段：`id`、`name`、`module`、`status`、`description`、`limits`、`workaround`、`effort_hint`、`keywords`、`how_to` |
| `data/golden/golden_set.jsonl` | 黄金集，唯一事实源。`.csv` 视图、`agentcore_dataset.json`、`MANIFEST.json` 由脚本生成 |
| `data/schema/*.schema.json` | 输入条目 / 黄金集条目 / Agent 输出 |
| `data/templates/` | 讨论用模板：`discussion-feedback.md`、`golden-set-feedback.csv`（已预填 30 行）、`mock-data-params.yaml`、`column-mapping.example.json`、`extra-feedback.example.jsonl` |
| `data/prompts/01~05` | 客户数据评估、mock 重新生成、黄金集更新、能力目录更新、切换分级 |
| `data/tools/` | 脚本。`seed/pool.jsonl` 是 60 条带标注的种子库 |
| `evaluators/s12_label_accuracy/` | 标签准确率 Lambda，另含 `batch_score.py`（本地批量打分 + 分布报告） |
| `evaluators/s12_reject_quality/` | 拒绝话术 LLM 评审 Lambda，另含 `execution-role-policy.json` |
| `evaluators/llm_judge_config/s12_reject_quality.json` | 原生 LLM-as-judge 配置，可直接作为 `--evaluator-config` 使用 |
| `evaluators/shared/` | 适配层与黄金集加载的权威副本；`sync_shared.py` 负责复制到各 Lambda |
| `evaluators/sample_events/`、`run_local.py` | 本地调用 handler 的样例事件 |

## 3. 接口约定（lead 已按此实现）

### 输入条目

字段：`id`、`title`、`content`、`customer`、`industry`、`customer_tier`、`channel`、`module`、`submitted_at`、`feedback_type`、`contract_commitment`。

用户消息第 1 行为 `需求编号: <id>`，后面每行一个字段，例如 `标题: …`。

### Agent 输出

只输出一个 JSON：

```json
{"feedback_id", "important": "是|否", "urgent": "是|否", "decision": "迭代|拒绝|待评估",
 "level": null|"P0"|"P1"|"P2", "reasoning", "reject_reason", "customer_reply"}
```

`decision` 为“拒绝”时，后两项必填。

### span 埋点

- 读取顺序：
  1. 首选属性 `s12lab.triage_output`（输出 JSON 字符串）；
  2. 再取 `s12lab.feedback_id`；
  3. 都没有时，再解析最终 assistant 文本：events / span_events / log-record body，按事件时间取最新的一条。
- 每个 span 带 `session.id`。

### 评估器返回

| 评估器 | 返回 |
|------|------|
| 标签准确率 | `value` = 答对项比例；`label` ∈ Pass / Fail / CriticalFail / FormatError / Skipped |
| 拒绝话术 | `value` 0~1，`label` 为 Pass 或 Fail；非拒绝返回 `label=不适用`，不带 value |
| 原生配置 | 档位 1.0 / 0.8 / 0.6 / 0.4 / 0.2，另有 0.0“不适用” |

### 部署

| 项 | 标签准确率 Lambda | 拒绝话术 Lambda |
|------|------|------|
| 打包 | 整个目录 zip | 整个目录 zip |
| handler | `lambda_function.handler` | `lambda_function.handler` |
| 依赖 | 只用标准库 | boto3 |
| Bedrock 权限 | 不需要 | 需要 `bedrock:InvokeModel` / `Converse` |

- **标签准确率**：
  - 打分项按评估器名后缀决定：`_important` / `_urgent` / `_decision` 只打单项，其它名字打三项。`S12_SCORE_FIELD` 可覆盖。
  - 名字依次从 `evaluatorName`、`evaluatorId`、`evaluatorArn` 读取，并去掉 id 末尾的 `-XXXXXXXXXX`。
  - 每次调用打印一行 JSON 日志。
- **拒绝话术**：
  - 模型取 `JUDGE_MODEL_ID`，失败时回退 `FALLBACK_MODEL_ID`；评审输出上限 `JUDGE_MAX_TOKENS`（默认 3000）。
  - 评审输出被截断时，按已输出的维度分数计分。
  - 单次调用最多评审 2 条拒绝。

## 4. 已确认的决定

- **模板放 `data/templates/`**：lead 已把文档链接改过去。脚本也兼容仓库根目录的 `templates/`。
- **原生评审配置“不适用”档 = 0.0**：档位值必须互不重复，避免服务按值反查时把“优秀”和“不适用”弄混。lead 的对比页按 label 排除“不适用”。
  - Supersedes：lead 起初提议“不适用 = 1.0”。改动原因：1.0 与“优秀”重复。
- **Claude Sonnet 5.5 不接受 `temperature` 参数**：实测返回 ValidationException。
  - 原生配置不传 temperature；
  - 代码型评审遇到该错误会自动去掉参数后重试；
  - lead 的 Agent 已对 Claude 模型去掉 temperature。
- **用 G26（集成链路故障）替换原“导入按编码更新”样例**：让“紧急=是”有 6 条，避免“全判否”就拿到 80% 以上的假高分。批量报告同时给出各类别召回率。
- **客户真实数据只写 `data/customer/`**：lead 已把该目录加入 .gitignore。
  - `--use-as-input` 生成 `data/customer/agent_data/`，再用 `S12_DATA_DIR` 指向它（Agent 的 `data.py` 优先读这个变量），不再覆盖仓库里的 mock 文件。
  - Supersedes：之前是“覆盖 `data/feedback_last_month.jsonl` 并留 .bak”。改动原因：该文件被 git 跟踪，客户数据可能被提交到公开仓库。
- **目标 trace 不在 session 里时，两个评估器都返回 `Skipped`**，不借用其它 trace 的输出。
  - Supersedes：之前是“改用整个 session 并在说明里注明”。改动原因：会给错误的 trace 打分。
- **参考输入（`evaluationReferenceInputs`）只覆盖它写明的字段**，其余（可接受答案、不可自动拒绝）沿用打包黄金集。
  - Supersedes：之前是“整条替换”。改动原因：只写三项标签的参考输入会让严重错误（CriticalFail）判定失效。
- **适配层已用真实 span 验证**：样例为 `telemetry/fixtures/local-adot-v0-sessions.json`（Strands 1.64 + ADOT 0.22，split 模式），3 个 session 全部正确解析。
  - 属性路径直接取到输出；
  - 去掉 `s12lab.triage_output` 后，仍能从 log record 的 `body.output.messages` 和 `需求编号:` 行还原。
  - `test_span_adapter.py` 有针对这份样例的测试。

## 5. 待确认问题（我已按默认值处理）

- **“战略客户的任何需求”是否列为不可自动拒绝**：默认否，留到 11:00 讨论，结论填模板第 2 节。
- **原生 LLM-as-judge 配置**：没有在 AgentCore 中实际注册过（按约束不创建 AWS 资源）。由 lead 的注册脚本首次验证；如果服务拒绝档位或模型 ID，告诉我调整 `rubric.py`。
