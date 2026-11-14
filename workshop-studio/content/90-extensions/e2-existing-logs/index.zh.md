---
title: "扩展 2 已有日志接入"
weight: 20
---

> **你在这里**：① 定义 → **② 构建** → **③ 评估** → ④ 发布 → ⑤ 观测 → ⑥ 回流 · 圆心：黄金标准

**这个扩展做什么**：有些 Agent 改不了代码，手里只有它自己的调用日志（不是 OTel 格式）。这个扩展用转换脚本把这种日志变成 AgentCore Evaluations 能读的 trace，导入 CloudWatch；先用遥测检查确认数据够用，再用**同一组评估器、同一份黄金集**打分。现场只用实验自带的样例日志 `telemetry/samples/customer-agent-logs.jsonl`（5 行，合成数据），不影响主线和其它扩展。

**什么时候做**：可选，做完主线之后、[第 9 章](/09-cleanup)清理之前；不依赖[扩展 1](/90-extensions/e1-self-hosted)。前提是评估器已经注册（[第 4.2 步](/04-evaluate#s-4-2)）。本页的命令都在实验 EC2 的 `~/adlc-lab` 目录里执行（新开的 Session Manager 会话先 `sudo su - ec2-user`）。会后要导入你们自己的日志，按[扩展 3 的日志接入](/90-extensions/e3-own-agent#e3-11)操作。

**用时**：约 20 分钟。

## E2.1 转换器需要哪些字段 {#e2-1}

评估至少需要会话 ID、用户输入、模型输出和时间；有工具调用更好。格式是 JSONL（UTF-8）：每次 Agent 调用一行。完整说明见 `telemetry/LOG_FORMAT.md`。

| 字段 | 必填 | 说明 |
|---|---|---|
| `session_id` | 是 | 会话 ID，每条需求一个（评估按 session 取数据） |
| `timestamp` | 是 | 调用时间，ISO 8601（例如 `2026-10-15T09:30:00+08:00`）或毫秒时间戳 |
| `user_input` | 是 | 发给 Agent 的完整输入（需求反馈原文） |
| `agent_output` | 是 | Agent 的最终输出，文本或 JSON 都可以；最好是“三项判断”JSON（键名 `important` / `urgent` / `decision`，或 `是否重要` / `是否紧急` / `处理建议`），否则标签准确率评估器记 `FormatError`，只剩内置评估器可用 |
| `feedback_id` | 建议 | 需求编号，用来和黄金集对上。缺失时转换脚本取输出 JSON 里的 `feedback_id`，评估器还会从 `user_input` 里的 `需求编号: …` 找 |
| `model`、`latency_ms`、`agent_name` | 否 | 模型名、耗时（毫秒）、Agent 名 |
| `tool_calls` | 否 | 工具调用列表 `[{"name","input","output","status"}]`，`status` 为 `success` 或 `error`；没有时工具类评估器不可用，其它评估器不受影响 |

## E2.2 看一眼样例日志 {#e2-2}

样例里每次 Agent 调用是一行 JSON，字段和 E2.1 一致。看第一行：

```bash
python3 -c "import json;print(json.dumps(json.loads(open('telemetry/samples/customer-agent-logs.jsonl',encoding='utf-8').readline()),ensure_ascii=False,indent=2))"
```

**预期输出**（节选）：

```
{
  "session_id": "cust-sess-0001",
  "timestamp": "2026-10-12T09:30:00+08:00",
  "feedback_id": "FB-2610-001",
  "user_input": "需求编号: FB-2610-001\n标题: 列表页批量修改自定义字段\n需求描述: 我们在客户列表页想批量修改自定义字段……",
  "agent_output": "{\"feedback_id\": \"FB-2610-001\", \"important\": \"是\", \"urgent\": \"否\", \"decision\": \"迭代\", ...}",
  "model": "customer-llm-v2",
  "latency_ms": 7800,
  "agent_name": "triage_agent",
  "tool_calls": [
    {
      "name": "search_capabilities",
      "input": {
        "query": "列表页批量编辑自定义字段"
      },
      …
```

5 行样例各有安排，用来演示不同情况：

| 行 | 需求 | 特点 |
|---|---|---|
| 1 | FB-2610-001 | 标准键名的 JSON 输出，带 `agent_name` 和 1 个工具调用 |
| 2 | FB-2610-002 | 输出用中文键名（`是否重要` / `是否紧急` / `处理建议`），转换时自动对应 |
| 3 | FB-2610-003 | 带 2 个工具调用 |
| 4 | FB-2610-005 | 输出是一段纯文字，不是 JSON |
| 5 | FB-2610-007 | 标准键名的 JSON 输出 |

你们的日志字段名不一样时怎么整理成这种格式，见[扩展 3 第 E3.11 步](/90-extensions/e3-own-agent#e3-11)。

## E2.3 先检查日志字段是否齐全 {#e2-3}

在导入前就发现缺字段的问题：

```bash
./ext-convert-logs.sh check telemetry/samples/customer-agent-logs.jsonl
```

脚本 `ext-convert-logs.sh` 调用 `labkit/convert_logs.py`；`check` 只读文件，不发送任何数据。

**预期输出**（样例日志共 5 行）：

```
▶ 检查 telemetry/samples/customer-agent-logs.jsonl：5 行
  字段覆盖率：
    ✅ session_id   必填 5/5
    ✅ timestamp    必填 5/5
    ✅ user_input   必填 5/5
    ✅ agent_output 必填 5/5
    ✅ feedback_id  可选 5/5
    ⚠️  model        可选 4/5
    ⚠️  latency_ms   可选 4/5
    ⚠️  agent_name   可选 1/5
    ⚠️  tool_calls   可选 2/5
  Agent 输出可解析为“三项判断”JSON：4/5（标签准确率评估器需要）
  ℹ️  5 行时间早于 14 天；发送时所有记录都会按原顺序重排到最近几分钟内
✅ 字段检查通过。下一步：./ext-convert-logs.sh send telemetry/samples/customer-agent-logs.jsonl
```

样例里故意放了一行纯文字输出（第 4 行，FB-2610-005），所以是 4/5：导入后标签准确率评估器会给这一条 `FormatError`，正好演示“Agent 输出不规范”时评估能发现问题。

:::alert{header="有问题的行先修正" type="warning"}
缺必填字段（没有会话 ID、用户输入、输出或时间）的行，以及不是合法 JSON 的行，`send` 会直接跳过。`check` 里出现 `❌ 第 N 行缺少必填字段`、`不是合法 JSON` 或 `timestamp 无法解析` 时先修正。如果最后还有一行 `⚠️ 不到一半的输出能解析成三项判断`，先调整 `agent_output` 的格式，否则标签准确率评估器会大量记 `FormatError`。
:::

## E2.4 转换并发送到 CloudWatch {#e2-4}

```bash
./ext-convert-logs.sh send telemetry/samples/customer-agent-logs.jsonl
```

脚本做了三件事：

- 把每行日志变成一组 OTel span：`invoke_agent <agent_name>` → `chat`，每个工具调用再加一个 `execute_tool <工具名>`，并附上输入输出内容记录，格式和 Strands Agent 埋点产生的一致（scope `strands.telemetry.tracer`）。需求编号写在根 span 的 `adlc.feedback_id` 上；输出能解析成三项判断时，写在根 span 的 `adlc.triage_output` 上。
- 把时间重排到最近几分钟内：按原先后顺序，每条间隔 10 秒，因为 CloudWatch 不接收 14 天以前的 span。时间也不会早于日志组的创建时间（CloudWatch Logs Insights 查不到比日志组更早的记录），所以第一次发送时，记录从日志组建好后几秒开始排。行数多时后面的 session 会排到接下来的几分钟里，`send` 结尾会多一行 `ℹ️` 提示要等多久。
- 以服务名 `adlc-ws-imported-logs` 发到 CloudWatch，并登记为一次“运行”（`.lab-state/runs.json`，类型 `imported`），后面的 `./ext-check-telemetry.sh` 和 `./43-run-eval.sh` 可以直接用它。

**预期输出**（第一次发送，日志组刚创建；时间以你看到的为准）：

```
  🆕 创建日志组 /aws/bedrock-agentcore/adlc-ws-imported-logs
📡 OTel 已配置：service.name=adlc-ws-imported-logs · 日志组 /aws/bedrock-agentcore/adlc-ws-imported-logs · 区域 us-west-2
… WARNING [opentelemetry.instrumentation.logging] … Skipping installation of LoggingHandler …
▶ 转换并发送 5 条日志 → service.name=adlc-ws-imported-logs · 日志组 /aws/bedrock-agentcore/adlc-ws-imported-logs
  ✔ adlc-ws-imp-20261106T151120Z-cust-sess-0001  FB-2610-001
  ✔ adlc-ws-imp-20261106T151120Z-cust-sess-0002  FB-2610-002
  ✔ adlc-ws-imp-20261106T151120Z-cust-sess-0003  FB-2610-003
  ✔ adlc-ws-imp-20261106T151120Z-cust-sess-0004  FB-2610-005
  ✔ adlc-ws-imp-20261106T151120Z-cust-sess-0005  FB-2610-007
✅ 已发送 5 个 session（run_id = imported-20261106T151120Z）。约 2-3 分钟后可在 CloudWatch 查到。
   下一步：./ext-check-telemetry.sh   （检查数据能否被 Evaluations 使用）
```

发送一次就够，不需要再发第二次。`WARNING … Skipping installation of LoggingHandler` 是 OpenTelemetry 自己的提示，属于正常现象。每次发送，脚本都会把日志里的 `session_id` 改写为 `adlc-ws-imp-<发送时间>-<原 session_id>`：同一个文件重复发送也不会和上一次混在一起，在 CloudWatch 里按这个前缀就能找到。记下 run_id（`imported-<发送时间>`），后面都用它。

## E2.5 在 CloudWatch 确认导入结果 {#e2-5}

1. 等 2–3 分钟。**CloudWatch** → **GenAI Observability** → **Bedrock AgentCore** → **Agents** 列表里找到 `adlc-ws-imported-logs`。
2. 点进去 → **Sessions**：session 名形如 `adlc-ws-imp-20261106T151120Z-cust-sess-0001`；点开看 trace 里的输入和输出。

**预期结果**：5 个 session，与日志行数一致。每个 trace 的根是 `invoke_agent triage_agent`（第 1 行写了 `agent_name`）或 `invoke_agent customer_agent`（没写时的默认名），下面是 `chat`；带工具调用的 2 行还有 `execute_tool …`。实测用 Logs Insights 查，输入输出内容约 35 秒、span 约 1.5 分钟后可查，所以等 2–3 分钟再到控制台看。控制台菜单名称可能随版本略有不同。

## E2.6 遥测检查：能否被 Evaluations 使用 {#e2-6}

用检查脚本读取 CloudWatch 里的数据，确认导入的 trace 满足 AgentCore Evaluations 的要求：

```bash
./ext-check-telemetry.sh --run imported-<发送时间>
```

脚本 `ext-check-telemetry.sh` 调用 `labkit/check_telemetry.py`，逐项检查 session、trace、模型输入输出、工具调用等是否齐全。刚 `send` 完时，导入的就是 `.lab-state/runs.json` 里最后登记的运行，不带 `--run` 也会检查它；之后跑过别的运行就不是了，所以建议带上 `--run`。

**预期输出**（第一次发送后马上运行；等待时长和试评估分数以你看到的为准）：

```
▶ 检查 run imported-20261106T151120Z（imported）的 5 个 session · 日志组 /aws/bedrock-agentcore/adlc-ws-imported-logs
  ⏳ 等待数据在 CloudWatch 中可查询…（15s）
  ⏳ 等待数据在 CloudWatch 中可查询…（30s）
  …
  ⏳ 等待数据在 CloudWatch 中可查询…（75s）
  · adlc-ws-imp-20261106T151120Z-cust-sess-0001  span=3 文档=6
  · adlc-ws-imp-20261106T151120Z-cust-sess-0002  span=2 文档=4
  · adlc-ws-imp-20261106T151120Z-cust-sess-0003  span=4 文档=8
  · adlc-ws-imp-20261106T151120Z-cust-sess-0004  span=2 文档=4
  · adlc-ws-imp-20261106T151120Z-cust-sess-0005  span=2 文档=4

检查项（✅ 全部 session 满足 · ⚠️ 部分满足 · ❌ 都不满足）
  ✅ [必需] 能在 CloudWatch（aws/spans）按 session.id 找到 span  (5/5)
  ✅ [必需] 每个 span 都带 session.id  (5/5)
  ✅ [必需] span 来自受支持的框架埋点（如 Strands: strands.telemetry.tracer）  (5/5)
  ✅ [必需] 有 Agent 调用 span（gen_ai.operation.name = invoke_agent）  (5/5)
  ✅ [必需] 能取到用户输入内容  (5/5)
  ✅ [必需] 能取到 Agent 最终输出内容  (5/5)
  ⚠️  [可选] 有工具调用 span（execute_tool）→ 工具类评估器可用  (2/5)
  ✅ [可选] 记录了模型 ID（gen_ai.request.model）  (5/5)
  ❌ [可选] 记录了 token 用量 → 成本分析可用  (0/5)
  ✅ [可选] 带需求编号 / 判断结果属性 → 标签准确率评估器最稳  (5/5)
  ✅ [可选] 带 service.name → 在线评估 / 控制台按 Agent 聚合  (5/5)
  埋点来源 scope：['strands.telemetry.tracer']   service.name：['adlc-ws-imported-logs']
  ✅ 试评估（Builtin.Helpfulness）成功：Very Helpful 0.83

🟨 结论：可以评估（必需项都满足），部分可选项缺失会限制个别评估器。可以按手册第 4 章评估。
```

- 样例日志只有 2 行带工具调用、都没有 token 用量，所以结论是 🟨：必需项都满足，可以评估。
- `文档` 是 `span` 的两倍：每个 span 都有一条对应的输入输出内容记录。如果 `文档` 和 `span` 一样多、“能取到用户输入内容”是 0/5，说明内容记录没查到。
- 数据刚发出时检查器会自动等待（每 15 秒查一次，最多 3 分钟）；等完仍显示 `span=0`，再运行一次即可。
- 检查最多取 5 个 session（`--sessions N` 可改，`--no-probe` 跳过试评估）。最后的试评估会真的调用一次 Evaluations，证明服务能读懂这些数据。

## E2.7 读懂检查结果：通过 / 有缺口 / 差距大 {#e2-7}

| 结论 | 含义 | 下一步 |
|---|---|---|
| 🟩 通过 | 必需项和可选项都满足：session、trace、Agent 输入输出、需求编号和判断结果都能读到，试评估成功 | 直接按需评估 |
| 🟨 可以评估 | 必需项都满足，缺可选项（例如没有工具调用 span、没有 token 用量） | 可以评估，标签准确率、话术质量不受影响；有时间再补数据或埋点 |
| 🟧 缺 1 项必需 | 只缺 1 项必需内容（必需项都在、但试评估失败，也算 1 项） | 先等 2–3 分钟重试；仍缺就按 ❌ 项补数据或埋点 |
| 🟥 差距大 | 缺 2 项以上必需内容，或完全找不到 span：评估器无法打分，也不做试评估 | 先按下面的提示排查后重试；自带 Agent 按 ❌ 项补埋点（[扩展 3 第 E3.16 步](/90-extensions/e3-own-agent#e3-16)） |

:::alert{header="排查提示" type="info"}
“找不到 session”最常见的原因是数据还没到：等 2–3 分钟再查。其次是账号没开 Transaction Search：实验环境已经开好，`./10-check-env.sh` 会显示它的状态。结论是 🟧 / 🟥 时，脚本会建议“回到主线”：那是针对实验的 mock agent，主线里它跑在 AgentCore Runtime 上，trace 由 Runtime 自动发送，不受这里的影响。
:::

## E2.8 按需评估导入的运行 {#e2-8}

```bash
./43-run-eval.sh --run imported-<发送时间>
```

这和[第 4.5 步](/04-evaluate#s-4-5)是同一个脚本、同一组评估器：按每条日志的需求编号，把黄金集答案作为参考输入传给评估器。看输出时注意三点：

- 第一行是 `▶ 评估 run imported-<发送时间>：5 个 session × … 个评估器`，先核对 run_id 和条数。
- FB-2610-005 那一行（纯文字输出）的 `三项` 显示 `FormatError`：评估发现了“Agent 输出不规范”，它在观测里计入“格式错误”。
- 只有 5 条，门禁和结论只用来演示链路，不要和第 4 章黄金集全量的分数比。结果的读法见[第 4.6 步](/04-evaluate#s-4-6)。

导入的运行不进在线评估：服务名 `adlc-ws-imported-logs` 不是任何在线评估配置的数据源，控制台 **Evaluations** 页签里不会有这些 session 的分数。失败归因同样可用：`./44-failures.sh results/eval-imported-<发送时间>.json`。两次导入之间做前后对比时，必须用 `--before` / `--after` 指定，见[扩展 3 第 E3.14 步](/90-extensions/e3-own-agent#e3-14)。

:::alert{header="清理" type="info"}
不用单独清理：[第 9 章](/09-cleanup)的 `./99-cleanup.sh` 按前缀删除你的资源，包括日志组 `/aws/bedrock-agentcore/adlc-ws-imported-logs`。
:::

## 检查一下 {#e2-check}

**导入的日志能被同一组评估器对照黄金集打分，靠的是哪个字段把每一行日志和黄金集里的需求对上？如果你们的日志里没有它（输出 JSON 和用户输入里也找不到需求编号），标签准确率会变成什么？**（对照 E2.1 的 `feedback_id` 一行想一想。）

下一个扩展：[扩展 3 自带 Agent 接入指南](/90-extensions/e3-own-agent)
