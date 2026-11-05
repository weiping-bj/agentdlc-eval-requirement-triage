# data/ + evaluators/ 交接说明（给 lead）

> 状态：契约已定稿（字段名已对齐 `agent/s12agent/core.py` / `data.py` 现有命名）。所有数据为合成数据。

## 1. 文件位置（与 `agent/s12agent/data.py` 的查找顺序一致）

| 文件 | 内容 |
|------|------|
| `data/capability_catalog.json` | 平台能力目录：`{"platform": {...}, "capabilities": [...]}`；“明确不做”的条目也在 `capabilities` 里（`status="明确不做"`） |
| `data/feedback_last_month.jsonl` | 60 条合成“上个月需求反馈”（Agent 的输入，不含任何标签） |
| `data/golden/golden_set.jsonl` | 30 条黄金集（含期望标签、拒绝要点、话术要点、五维标签） |
| `data/golden/golden_set.csv` | 同上的 Excel 友好视图（脚本生成，不要手改） |
| `data/golden/agentcore_dataset.json` | 黄金集投影成 AgentCore dataset 格式（`scenario_id/turns/expected_response/assertions/metadata`） |
| `data/golden/LABELING_GUIDE.md` | 标注标准（中文） |
| `data/schema/*.schema.json` | 输入条目 / 黄金集条目 / Agent 输出 三份 JSON Schema |
| `data/templates/` | 11:00 讨论用填写模板：`discussion-feedback.md`、`golden-set-feedback.csv`、`mock-data-params.yaml` |
| `data/prompts/` | 给 coding agent 的中文提示词（读模板 → 跑脚本 → 校验） |
| `data/tools/` | 校验/标准化/重新生成脚本（纯标准库，python3 即可） |

**请求 lead**：`docs/discussion.html` 里链接的是 `templates/…`，我只能写 `data/`，模板实际在 `data/templates/`。请把文档链接改成 `data/templates/…`（脚本默认读 `data/templates/`，也兼容仓库根 `templates/`，二选一即可）。

## 2. 输入条目字段（`feedback_last_month.jsonl` 每行一个）

`id`（如 `FB-2610-001`）、`title`、`content`、`customer`（合成公司名，带“（示例）”）、`industry`、`customer_tier`（战略/KA/腰部/长尾）、`channel`、`module`、`submitted_at`（YYYY-MM-DD）、`feedback_type`（新需求/功能缺陷/体验优化/咨询）、`contract_commitment`（是/否）。
请在 `format_input()` 里把 `feedback_type` 渲染出来（标签“反馈类型”），它是判断“功能缺陷不可拒绝”的依据。

## 3. Agent 输出契约（每条需求一个 JSON 对象）

```json
{"feedback_id": "FB-2610-001", "important": "是", "urgent": "否", "decision": "迭代",
 "level": null, "reasoning": "...", "reject_reason": "", "customer_reply": ""}
```

- `important`/`urgent` ∈ {是, 否}；`decision` ∈ {迭代, 拒绝, 待评估}；`level` ∈ {P0, P1, P2, null}（分级模式才用）。
- `decision="拒绝"` 时 `reject_reason`（内部专业理由）与 `customer_reply`（对客户话术）必须非空。
- 批量模式也支持：`{"results": [ {...}, {...} ]}`。
- 评估器也容忍别名（`id`/`需求编号`、`是否重要`、`是否紧急`、`处理建议`、`true/false` 等），但请按上面的标准键输出。

## 4. Span 埋点契约（评估器 adapter 读取顺序）

1. **首选（必须做）**：每条需求一次调用，在该 trace 内任一 span 上写属性
   - `s12lab.feedback_id` = 需求编号（你已经通过 `trace_attributes` 写在 `invoke_agent` span 上 ✔）
   - `s12lab.triage_output` = Agent 输出 JSON 字符串（**请新增**：在 Agent 调用外包一层 `s12lab.triage` span，拿到结果后 `span.set_attribute("s12lab.triage_output", json.dumps(out, ensure_ascii=False))`）
   - 可选 `s12lab.agent_version` = v0/v1（已有 ✔）
   - 原因：实测（参考 workshop-2-6/spans.py 注释）AgentCore 交给评估器的 `chat` span 往往**没有消息正文**，只靠模型消息解析不可靠。
2. **兜底**：解析 span 事件 / 属性里的最终 assistant 文本（`events`/`span_events` 的 `gen_ai.choice`、`gen_ai.assistant.message` body，`gen_ai.output.messages`、`gen_ai.completion` 属性，以及顶层 log record 的 `body.output.messages`），取最后一个能解析出上述 JSON 的文本。
3. 需求编号兜底：输出 JSON 的 `feedback_id` → 属性 → 用户输入里的 `需求编号: FB-xxxx`。

## 5. 评估器（`evaluators/`，详见 `evaluators/README.md`）

| 评估器名（建议注册名） | 类型 / 级别 | value / label |
|------|------|------|
| `s12_label_accuracy` | code-based, TRACE | 三项命中比例 0~1；`Pass`=三项全对，`Fail`，`CriticalFail`=误拒了不可自动拒绝的需求 |
| （可选）`s12_important_acc` / `s12_urgent_acc` / `s12_decision_acc` | 同一份代码，Lambda 环境变量 `S12_SCORE_FIELD=important|urgent|decision` | 单项 0/1，控制台平均分 = 该项准确率，直接对比 70% |
| `s12_reject_quality` | code-based（Lambda 内调 Bedrock 做 LLM 评审），TRACE | 只评 `decision=拒绝` 的 trace，0~1；`Pass`≥0.7；非拒绝返回 `Skipped` |
| `s12_reject_quality_llm`（备选） | LLM-as-judge 配置（`evaluators/llm_judge_config/`） | 同一套中文 rubric，5 档 |

- 黄金集随 Lambda 打包（`evaluators/*/golden_set.json`，用 `python3 data/tools/golden_tool.py sync-evaluators` 同步）；评估时若 Evaluations 传入 `evaluationReferenceInputs`（expected_response 为标签 JSON），优先用它。
- 评审模型读取顺序：Lambda 环境变量 `JUDGE_MODEL_ID` → `config/local.env` → `config/lab.env` 的 `JUDGE_MODEL_ID` → 默认 `us.anthropic.claude-sonnet-5-5`；失败（无权限/模型不可用）自动回退 `FALLBACK_MODEL_ID`（默认 `us.amazon.nova-2-lite-v1:0`）。
- 通过线：`TARGET_ACCURACY`（`config/lab.env`，默认 0.70），三项各自 ≥ 0.70 才算达标。
- 本地批量打分（第 7 章用）：`python3 evaluators/s12_label_accuracy/batch_score.py --results <agent 输出 jsonl> --report-md out.md`，也可直接喂导出的 spans JSON。

## 6. 待确认问题（我先按默认做了）

- 模板目录：默认 `data/templates/`（见第 1 节请求）。
- 一次调用处理一条需求（一个 trace）；批量模式评估器也能算，但控制台的“平均分=准确率”只在一条一 trace 时成立。

## Requests from lead

- (lead, done) 文档链接已改为 `data/templates/…`；`format_input()` 已渲染 `反馈类型`；已加 `s12lab.triage` 包裹 span，属性 `session.id`、`s12lab.feedback_id`、`s12lab.agent_version`、`s12lab.triage_output`（scope 名 `s12lab.triage`，与 `invoke_agent` 同一 trace）。
- 单项准确率评估器：请让**同一个 Lambda** 也能从 `event["evaluatorName"]` 后缀推断打分字段（名字以 `_important` / `_urgent` / `_decision` 结尾 → 单项 0/1；其它 → 三项比例），环境变量 `S12_SCORE_FIELD` 仍可覆盖。这样我只部署 1 个 label Lambda，注册 4 个评估器。我的注册名形如 `s12lab_<suffix>_label_accuracy`、`s12lab_<suffix>_label_important` 等。
- 请在 `evaluators/README.md` 写清每个 Lambda：打包目录（我会把整个目录 zip 进 Lambda 根目录）、handler 名（如 `lambda_function.handler`）、需要的环境变量、是否依赖目录外文件。我部署为 python3.12 / arm64 / 512MB / 超时 120s；reject_quality Lambda 我会设置 `JUDGE_MODEL_ID`、`FALLBACK_MODEL_ID` 并授予 `bedrock:InvokeModel`/`Converse`。
- 原生 LLM-as-judge 配置（`evaluators/llm_judge_config/`）：请提供可直接传给 `create-evaluator --evaluator-config` 的 JSON，`modelId` 写占位符 `__JUDGE_MODEL_ID__`；不要用 `{expected_response}`（要能挂到在线评估）。
- 真实 span 样例稍后放到 `telemetry/fixtures/`（只读），可用来测 adapter。
