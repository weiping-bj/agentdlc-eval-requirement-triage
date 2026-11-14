# AgentDLC 动手实验 自定义评估器

两个事先写好、测试过的自定义评估器，加上一份原生 LLM-as-a-judge 配置。都对接 Amazon Bedrock AgentCore Evaluations。

| 目录 | 评估器（建议注册名） | 类型 / 级别 | 衡量什么 | 对应目标 |
|------|------|------|------|------|
| [`adlc_label_accuracy/`](adlc_label_accuracy/) | `…_label_accuracy`，可选 `…_label_important` / `…_label_urgent` / `…_label_decision` | 代码型（Lambda），TRACE | 三项标签与黄金集是否一致 | “是否重要 / 是否紧急 / 是否迭代 三项 70%” |
| [`adlc_reject_quality/`](adlc_reject_quality/) | `…_reject_quality` | 代码型（Lambda 内调 Bedrock 做 LLM 评审），TRACE | 拒绝理由与客户话术的专业性、说服力 | “自动拒绝 + 专业话术” |
| [`llm_judge_config/adlc_reject_quality.json`](llm_judge_config/adlc_reject_quality.json) | `…_reject_quality_llm` | 原生 LLM-as-a-judge，TRACE | 同上（同一套中文评分标准），可挂到在线评估 | 同上 |

每个评估器都分三层：
- **算法**：`label_scoring.py`，或 `judge.py` + `rubric.py`；
- **适配层**：`span_adapter.py`，把 ADOT span 转成评估输入；
- **Lambda 入口**：`lambda_function.py`。

适配层、黄金集和三大支柱判定（`pillars.py`）在 [`shared/`](shared/) 里各有一份权威副本，由 [`sync_shared.py`](sync_shared.py) 复制到每个 Lambda 目录。**每个 Lambda 目录都是自包含的，整个目录 zip 即可部署。**

## 0. 结论怎么判：三大支柱（红线 / 门禁 / 观测）

所有报告（本地 `batch_score.py`、`31-run-eval.sh` 的终端结果、`32-compare.sh` 的对比页）用同一条规则，代码在 [`shared/pillars.py`](shared/pillars.py)：

| 支柱 | 包含什么 | 对结论的作用 |
|------|------|------|
| **红线**（一票否决） | ① 误拒：Agent 把黄金集 `can_auto_reject=false` 的需求判为“拒绝”（数据丢失、安全、合规、合同承诺、线上缺陷）；② 话术红线：`reject_quality` 评审列出的 red_flags（编造功能或配置路径、承诺上线时间、泄露其他客户或内部信息、话术为空或与理由矛盾） | 出现 **1 条**就整体不通过；这条需求**整条判错**：标签三项都按错计（value 0），话术判 Fail（value ≤ 0.2） |
| **门禁**（全部达标） | 是否重要 / 是否紧急 / 处理建议准确率（分级模式再加优先级）各 ≥ `TARGET_ACCURACY`（默认 0.70），红线条目和没有输出的条目都按错计；拒绝话术通过率 ≥ `TARGET_ACCURACY`（每条 Pass = 评审分 ≥ 0.7 且无红线） | 任一门禁未达标就不通过；有拒绝条目却没有话术评审结果时该门禁“未评估”，结论为“不完整”，不能判通过 |
| **观测**（只看趋势） | 五维准确率、用例类型整条答对率、话术分项 A–E 与未覆盖要点、能力目录命中率、格式错误率、处理建议分布、各评估器原始平均分、内置评估器 | **不影响结论**，用于归因和看趋势 |

结论 = 没有红线 **且** 门禁全部达标。标签 Lambda 只看得到误拒，话术红线的“整条判错”在汇总时加上（单项评估器返回的原始分数放在观测里）。已经部署的 Lambda 如果还是旧版本（红线话术只封顶 0.4、误拒时其它单项照常给分），对比页和 `labkit.evaluate` 会按上面的规则重新读取已保存的结果，结论一致；重新部署后 Lambda 自己返回的分数也按新规则。

---

## 1. Agent 输出契约（Agent 必须这样输出）

每条需求调用一次 Agent（一个 trace）。Agent 的最终消息只输出一个 JSON 对象：

```json
{"feedback_id": "FB-2610-001", "important": "是", "urgent": "否", "decision": "迭代",
 "level": null, "reasoning": "能力目录 CAP-002 仅支持标准字段；上个月 3 个不同客户提出",
 "reject_reason": "", "customer_reply": ""}
```

| 字段 | 取值 | 说明 |
|------|------|------|
| `feedback_id` | 需求编号 | 与输入第 1 行 `需求编号: …` 一致 |
| `important` / `urgent` | `是` / `否` | 是否重要 / 是否紧急 |
| `decision` | `迭代` / `拒绝` / `待评估` | 处理建议 |
| `level` | `P0` / `P1` / `P2` / `null` | 分级模式才填 |
| `reject_reason` | 文本 | `decision=拒绝` 时必填：面向产研的专业理由 |
| `customer_reply` | 文本 | `decision=拒绝` 时必填：可直接发给客户的话术 |

- 批量模式 `{"results": [ … ]}` 也能评，但控制台的“平均分 = 准确率”只在一条需求一个 trace 时成立。
- 适配层兼容常见别名（`id`、`需求编号`、`是否重要`、`处理建议`、`true/false`、`"建议拒绝"` 等），也兼容 ```` ```json ```` 代码块和前后夹带的文字。
- JSON Schema：[`data/schema/agent_output.schema.json`](../data/schema/agent_output.schema.json)。

## 2. 埋点契约（适配层读哪些 span 数据）

**按以下顺序查找，命中即停。**

1. **首选：span 属性**。在该 trace 的任一 span 上写入以下属性（mock agent 在 `adlc.triage` 包裹 span 上写）：

   | 属性 | 内容 |
   |------|------|
   | `adlc.triage_output` | 上面的输出 JSON（字符串） |
   | `adlc.feedback_id` | 需求编号 |
   | `adlc.agent_version` | `v0` / `v1`，可选 |
   | `session.id` | 每个 span 都要有（Evaluations 按 session 取数） |

   之所以首选属性，是因为实测交给评估器的 `chat` span 往往不带消息正文，只靠解析模型消息不可靠。

2. **兜底：最终 assistant 文本**。按时间倒序找第一个能解析出输出 JSON 的文本，来源依次为：
   - span 的 `events` / `span_events`（`gen_ai.choice`、`gen_ai.assistant.message` 的 `body.message.content` / `body.content`）；
   - 属性 `gen_ai.completion`、`gen_ai.output.messages`、`output.value`；
   - split 模式下单独的 log record：`body.output.messages[].content.message`，可以是 `[{"text": …}]` 字符串。

   工具调用块（`toolUse` / `toolResult`）会被忽略。

3. **需求编号**：依次取输出 JSON 的 `feedback_id` → 属性 `adlc.feedback_id` → 用户输入里的 `需求编号: FB-xxxx`（`gen_ai.user.message`、`body.input.messages`、`gen_ai.prompt`）。

其它兼容处理：
- **trace 过滤**：按 `evaluationTarget.traceIds[0]` 过滤，兼容 `traceId` / `trace_id`，也兼容 X-Ray 格式。session 中找不到目标 trace 时返回 `Skipped`，不借用其它 trace 的输出。
- **草稿与终稿**：同一段文本里有多个分诊 JSON，但没有出现 2 个以上不同的 `feedback_id` 时，按单条需求处理，只取最后一个（优先取与 trace 的需求编号一致的那个）。出现多个不同编号时按批量处理。reasoning / thinking 类内容块会被忽略。
- **时间排序**：优先用事件时间；span 属性里的输出按 span 结束时间排序。支持 ns / ms / s 数值和 ISO-8601 字符串。双重编码的 JSON 字符串也能解析。
- **键名风格**：camelCase（`traceId`、`startTimeUnixNano`）和 snake_case（`trace_id`、`start_time`、`span_events`）都支持。

## 3. `adlc_label_accuracy`：三项标签准确率（代码型）

- **打包**：
  - 把 [`adlc_label_accuracy/`](adlc_label_accuracy/) 整个目录 zip 到 Lambda 根目录；
  - handler 为 `lambda_function.handler`；
  - 只用标准库，**不依赖 bedrock-agentcore SDK**；
  - 运行环境 python3.12，arm64 或 x86 都可以；
  - 512 MB、超时 60~120 秒即可；
  - 不需要 Bedrock 权限。
- **真值来源**：
  1. 先按 `feedback_id` 查同目录的 `golden_set.json`。在线评估不会传真值，所以黄金集要随 Lambda 打包。
  2. 如果 `evaluationReferenceInputs[].expectedResponse.text` 里有标签 JSON（与 [`data/golden/agentcore_dataset.json`](../data/golden/agentcore_dataset.json) 的 `expected_response` 同格式），就用其中出现的字段覆盖打包黄金集的对应字段。没出现的字段（可接受答案、不可自动拒绝）沿用打包黄金集。
  3. 写明了其它 `feedback_id` 的参考输入不会被借用。
- **打分**：
  - `important`、`urgent` 必须完全一致；
  - `decision` 只要在黄金集 `acceptable_decisions` 里就算对（模糊样例允许多个答案）；
  - 分级模式下比较 `level`，可允许相差 `ADLC_LEVEL_TOLERANCE` 级。

| 返回 | 含义 |
|------|------|
| `label=Pass`, `value=1.0` | 打分的各项全对 |
| `label=Fail`, `value=0~1` | 部分错误；value = 答对的项数 / 打分项数（三项时为 0 / 0.33 / 0.67 / 1） |
| `label=CriticalFail`, `value=0` | **红线**：把“不可自动拒绝”的需求判成了拒绝（数据、安全、合规、合同、缺陷类）。整条判错：打分的各项都按错计，单项评估器（`_important` / `_urgent` / `_decision` / `_level`）也返回 0 和 `CriticalFail`；explanation 里仍写出各项原本的对错（“原本 2/3 项正确”），供归因 |
| `label=FormatError`, `value=0` | Agent 有回复，但解析不出约定的 JSON（计入平均分，作为惩罚） |
| `label=Skipped`（无 value） | trace 里没有 Agent 输出，或该需求不在黄金集里（例如非黄金集的普通流量） |
| `errorCode` / `errorMessage` | 配置错误（如 `ADLC_SCORE_FIELD` 取值非法）或未预期的异常 |

**单 Lambda、多评估器**：同一个 Lambda 可以注册成多个评估器，按评估器名后缀决定打哪些项。名字依次从事件的 `evaluatorName`、`evaluatorId`、`evaluatorArn` 读取：取 ARN 最后一段，去掉 id 末尾的 `-XXXXXXXXXX`，先按后缀匹配，再按 `_important_` 这类片段匹配。每次调用会在 Lambda 日志里打印一行 JSON（`"adlc":"label_eval"`，含事件键、名字、id、级别、span 数、实际打分项），方便排查。

| 评估器名后缀 | 打哪些项 | value | 控制台平均分 |
|------|------|------|------|
| `_important` / `_urgent` / `_decision` / `_level` | 只打这一项 | 0 或 1 | = 该项准确率，可直接对比 70% |
| 其它（如 `_label_accuracy`） | 三项 | 0~1 | = 三项平均准确率 |

环境变量 `ADLC_SCORE_FIELD` 可以覆盖按名字推断的结果。

| 环境变量 | 默认 | 说明 |
|------|------|------|
| `ADLC_SCORE_FIELD` | 按名字推断，否则 `all` | `all` / `important` / `urgent` / `decision` / `level` |
| `ADLC_LABEL_SCHEME` | `binary` | `level` = 分级模式，打分项为 优先级 + 处理建议 |
| `ADLC_LEVEL_TOLERANCE` | `0` | 分级模式允许相差几级 |
| `ADLC_GOLDEN_PATH` | 同目录 `golden_set.json` | 一般不用改 |

**本地批量打分**（第 7 章用）。输入可以是 Agent 的输出文件，也可以是导出的 spans。报告按三大支柱输出（第 0 节）：
- 结论（✅ 通过 / ❌ 不通过 / ⚠️ 不完整）和原因；
- 红线：误拒条数、话术红线条数和清单；
- 门禁：三项准确率 vs 目标（`TARGET_ACCURACY`，默认 0.70），拒绝话术通过率；
- 观测：整条通过率、五维、用例类型、能力目录命中率、话术分项、各类别召回率（按原始输出统计，防止“全判否”的假高分）；
- 判错清单（红线条目标“整条判错”）；
- Agent 输出分布，即场景交付物：自动拒绝列表 + 重要/紧急四象限。

本地打分不调用评审模型：话术红线和话术门禁要用 `--reject-eval results/eval-<run_id>.json` 读取 `31-run-eval.sh` 已有的评审结果，否则这两项显示“未评估”、结论为“不完整”。

```bash
python3 evaluators/adlc_label_accuracy/batch_score.py --results results/v0.jsonl --report-md results/v0-score.md --out-json results/v0-score.json
python3 evaluators/adlc_label_accuracy/batch_score.py --results results/run-A-v1-<时间>.jsonl --reject-eval results/eval-A-v1-<时间>.json
python3 evaluators/adlc_label_accuracy/batch_score.py --spans results/v0-spans.json
python3 evaluators/adlc_label_accuracy/batch_score.py --results results/all-60.jsonl --distribution-only
```

## 4. `adlc_reject_quality`：拒绝理由与话术质量（代码型 LLM 评审）

- **打包**：
  - 把 [`adlc_reject_quality/`](adlc_reject_quality/) 整个目录 zip 到 Lambda 根目录，handler 为 `lambda_function.handler`；
  - 依赖标准库 + boto3（Lambda 运行时自带）；
  - 超时建议 120 秒。
  - **执行角色需要的权限**：`bedrock:InvokeModel`、`bedrock:InvokeModelWithResponseStream`（Converse 由这两个授权），只作用于实验允许的模型（Claude Sonnet 5.5、Claude Haiku 5.5、Amazon Nova 2 Lite 的 `us.` 推理配置文件及其路由到的基础模型，见 `labkit/models.py`；参考 [`execution-role-policy.json`](adlc_reject_quality/execution-role-policy.json)）。
- **评审模型**（读取顺序）：
  1. 环境变量 `JUDGE_MODEL_ID`；
  2. `config/local.env`；
  3. `config/lab.env`；
  4. 默认 `us.anthropic.claude-sonnet-5-5`。

  主模型出现 `AccessDenied` / `Validation` / `ResourceNotFound` 时，自动改用 `FALLBACK_MODEL_ID`（默认 `us.amazon.nova-2-lite-v1:0`）。限流时有界退避重试（最多 4 次，单次读超时 50 秒）。区域读取顺序：`JUDGE_REGION` → `AWS_REGION` → `us-west-2`。

  其它限制：
  - 评审输出上限 `JUDGE_MAX_TOKENS`，默认 3000。
  - 要求输出紧凑 JSON，列表放最后并限制长度。万一被截断，仍按已输出的维度分数计分，并在 explanation 里注明。
  - 单次调用最多评审 2 条拒绝（批量输出时），避免超过 Lambda 超时。
  - 每次调用打印一行 `"adlc":"reject_eval"` 日志。

  > 实测：Claude Sonnet 5.5 不接受 `temperature` 参数（`ValidationException: temperature is deprecated`）。评审客户端遇到这个错误会自动去掉该参数，并记住这个模型。
- **评分**：只评 `decision=拒绝` 的 trace。评审维度共五个，每项 1~5 分：
  - A 理由专业性
  - B 理解与礼貌
  - C 原因说明
  - D 替代方案
  - E 风险控制

  value 由五项分数换算：value = 各项 (分数−1)/4 的平均。另有三条规则：
  - 每次都附上**能力目录摘录**作为事实依据：与“需求原文 + 拒绝理由”最相关的 5 项能力，每项写出编号、名称、状态、说明、限制、替代方案和**菜单路径**（`how_to`，摘录里写作“用法”）；拒绝理由或话术里把某项能力的菜单路径（整条，或其中连续的两级以上）当作一条独立的路径原样写出时，这项能力也放进摘录。评分标准里写明了怎么读“编造功能或配置路径”（`rubric.PATH_RULE`，原生配置同样包含）：与所说功能在能力目录里的路径一致、或只写其中连续几级的不算编造；把别的功能的路径安上去，插入、追加、替换或删去任何一级，或目录里没有的路径，算红线。评审是大模型，偶尔会漏判这类改写过的路径。代码见 `lambda_function.capability_hints`。已经部署的 Lambda 如果是摘录还没有菜单路径的旧版本，重新运行 `./30-create-evaluators.sh`（会更新函数代码和原生评估器配置）后才生效；
  - 有黄金集时，再把该条的拒绝要点、话术要点一起交给评审模型，判断要点是否覆盖；
  - **红线一票否决**：出现任一红线问题（编造功能或配置路径、承诺上线时间、泄露其他客户或内部信息、话术为空或与理由矛盾、拒绝了不应自动拒绝的需求），label 为 `Fail`，value 最高 **0.2**（`rubric.RED_LINE_VALUE`，即“不合格”档，是最低的不及格档；不用 0.0，因为原生配置里 0.0 留给“不适用”）；explanation 里写“红线：…。（红线一票否决，整条不通过）”，汇总时据此统计红线；
  - 黄金集标为“不可自动拒绝”的需求被拒绝时，自动加上红线“拒绝了不应自动拒绝的需求（黄金集标注）”，同样 value ≤ 0.2。

  value ≥ 0.7 且无红线时 label 为 `Pass`，否则为 `Fail`。代码型评审的 value 是连续值（五项都 1 分时为 0.0），“不适用”另用 label 表示、不带 value，所以这里的 0.0 不是“不适用”，统计时请按 label 区分。评分标准全文见 [`rubric.py`](adlc_reject_quality/rubric.py)。
- **非拒绝**：返回 `label=不适用`，不带 value。`FormatError` / `Skipped` 的含义同上。评审调用失败时返回 `errorCode=ADLC_REJECT_JUDGE_FAILED`。

## 5. 原生 LLM-as-a-judge 配置（可挂在线评估）

[`llm_judge_config/adlc_reject_quality.json`](llm_judge_config/adlc_reject_quality.json) 可以直接作为 `create-evaluator --evaluator-config`（或 boto3 `create_evaluator(evaluatorConfig=…)`）的内容使用，级别为 `TRACE`。

- **模型占位**：`modelId` 是占位符 `__JUDGE_MODEL_ID__`，部署脚本用 `config/lab.env` 的 `JUDGE_MODEL_ID` 替换。
- **占位符**：只用 `{context}` 和 `{assistant_turn}`，不用 `{expected_response}`，因此可以挂到在线评估配置。
- **事实依据**：没有单独的能力目录摘录，评审对照 `{context}` 里 Agent 自己的工具调用结果。V1 的 `search_capabilities` 返回菜单路径 `how_to`，所以 V1 照抄的路径它能核实；V0 的 `lookup_capability` 只返回功能名称。
- **输出格式**：不写输出格式说明，由服务自动追加。
- **不传 `temperature`**：Sonnet 5.5 不接受该参数。
- **评分档位**：1.0 优秀 / 0.8 良好 / 0.6 一般 / 0.4 较差（无红线）/ 0.2 不合格（**触发任一红线一律给这一档**，与代码型评审一致）/ **0.0 不适用**（处理建议不是拒绝）。
  - 档位值互不重复，避免服务按分值反查档位时混淆。
  - 对比页面和统计时请按 label 排除“不适用”。注意 AgentCore 控制台给出的服务端平均分包含这些 0.0，不代表话术质量，只能看按 label 过滤后的结果。
- **重新生成**：配置由 `rubric.py` 生成。改了评分标准后运行 `python3 evaluators/sync_shared.py`。

## 6. 本地验证

```bash
python3 evaluators/sync_shared.py --check                      # 黄金集 / 适配层副本 / 网页版视图（能力目录、mock 数据）是否最新
python3 evaluators/run_local.py adlc_label_accuracy evaluators/sample_events/label_pass_G01.json
python3 evaluators/run_local.py adlc_label_accuracy evaluators/sample_events/label_criticalfail_G03.json
python3 evaluators/run_local.py adlc_reject_quality evaluators/sample_events/reject_G04.json   # 真实调用 Bedrock
python3 -m pytest -q evaluators/tests data/tests              # 需要 pytest；设 ADLC_SKIP_BEDROCK=1 跳过真实调用
```

**黄金集修改后**：
1. 运行 `python3 data/tools/golden_tool.py freeze`。它会自动执行 `sync_shared.py`，更新 `*/golden_set.json`。
2. **重新部署两个 Lambda**，新黄金集才生效。

## 7. 方法与边界

- 标签准确率是确定性的程序打分（0 token），直接对应客户的量化目标（门禁）；误拒和话术红线是红线，一票否决。
- 拒绝话术是主观质量，用 LLM 评审，并且有两套实现：
  - **代码型**：能读取黄金集要点和能力目录，信息更全，适合离线和按需评估；
  - **原生配置型**：可以挂到在线评估，在 CloudWatch 控制台查看。
- 两者都不替代内置评估器（如 `Builtin.Correctness`、`Builtin.Helpfulness`），可以一起用。
- 黄金集只有 30 条，单次准确率的波动约 ±8 个百分点（1 条 = 3.3%）。V0 → V1 的对比请看各项与判错清单，不要只看总分。
