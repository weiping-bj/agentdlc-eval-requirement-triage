---
title: "扩展 1 自托管 Agent 埋点接入"
weight: 10
---

> **你在这里**：① 定义 → **② 构建** → **③ 评估** → ④ 发布 → ⑤ 观测 → ⑥ 回流 · 圆心：黄金标准

**这个扩展做什么**：主线（AgentCore Runtime）里，trace 由 Runtime 自动发到 CloudWatch。很多 Agent 并不在 Runtime 上，而是跑在本地机房、自己的容器或其他云上。这个扩展把**同一个 mock agent** 直接运行在实验 EC2 上，模拟这种自托管的 Agent：自己用 OpenTelemetry（OTel）和 ADOT（AWS Distro for OpenTelemetry）埋点，把 trace 发到 CloudWatch，然后用**同一份黄金集、同一组评估器**完成遥测检查、在线评估、按需评估和分诊报告。

**什么时候做**：可选，做完主线（第 3–6 章）之后、[第 9 章](/09-cleanup)清理之前。前提是评估器已经注册（[第 4.2 步](/04-evaluate#s-4-2)）。本页的命令都在实验 EC2 的 `~/adlc-lab` 目录里执行（新开的 Session Manager 会话先 `sudo su - ec2-user`）。现场不接入你们自己的 Agent；会后要接入，按[扩展 3 自带 Agent 接入指南](/90-extensions/e3-own-agent)操作。

**用时**：约 40 分钟；只做到 E1.7（埋点、发送、检查）约 20 分钟。

:::alert{header="本页统一用 V0" type="info"}
mock agent 在 EC2 上运行时，版本默认取 `config/local.env` 里的 `AGENT_VERSION`；做过[第 5.3 步](/05-optimize#s-5-3)的话，它已经是 `v1` 或 `v1-ref`。为了和[第 4 章](/04-evaluate)里 Runtime 上的 V0 对比，本页的运行命令都加了 `--version v0`：只影响这一次运行，不改配置。E1.12 的分诊报告例外，它用当前版本，和第 6.3 步一致。
:::

## E1.1 先不带遥测跑一次 Agent {#e1-1}

先确认 Agent 本身能正常工作，排除和埋点无关的问题。

```bash
./ext-local-agent.sh smoke --version v0
```

脚本 `ext-local-agent.sh` 调用 `labkit/batch.py`：在这台 EC2 上直接调用 mock agent（不经过 Runtime），结果写到 `results/`。

**预期输出**（每条需求约 10–20 秒，数字以你看到的为准）：

```
▶ 运行 1 条需求 · EC2 上自托管 · 版本 v0 · 模型 us.anthropic.claude-sonnet-5-5 · 并发 1
  [ 1/1] FB-2610-001       11.9s  重要=是 紧急=否 建议=迭代
✅ 完成：1 条，用时 12s，失败 0 条 → results/run-local-smoke-215617.jsonl
   run_id = local-smoke-215617
```

run_id 里的数字是当时的时间（时分秒）。想看 Agent 的完整输出：

```bash
ls -t results/run-local-smoke-*.jsonl | head -1 | xargs tail -1 | python3 -m json.tool --no-ensure-ascii
```

这次冒烟运行也会登记到 `.lab-state/runs.json`，但它没有遥测；所以后面检查和评估时都用 `--run` 指定要看的那次运行。

## E1.2 确认 ADOT 已安装 {#e1-2}

ADOT 负责在 Agent 进程里采集 span 并发送出去。实验环境已经装好（依赖见 `agent/requirements.txt`），确认一下即可：

```bash
.venv/bin/python -m pip show aws-opentelemetry-distro strands-agents | grep -E '^(Name|Version)'
```

**预期输出**（版本号以你看到的为准）：

```
Name: aws-opentelemetry-distro
Version: 0.x.x
Name: strands-agents
Version: 1.x.x
```

给自己的 Agent 安装 ADOT 的方法见[扩展 3 第 E3.3 步](/90-extensions/e3-own-agent#e3-3)。

## E1.3 配置 OTel 环境变量（发往 CloudWatch） {#e1-3}

环境变量告诉 ADOT 把数据发到哪个区域、哪个日志组、服务叫什么名字。它们都写在 `telemetry/otel_env.sh` 里，执行一次看看效果：

```bash
source ./00-config.sh && source telemetry/otel_env.sh
```

**预期输出**：

```
📡 OTel 已配置：service.name=adlc-ws-local-agent · 日志组 /aws/bedrock-agentcore/adlc-ws-local-agent · 区域 us-west-2
```

这些变量只在当前 shell 里生效；后面的脚本启动时会自己再设置一次，所以这一步只是看一眼。

| 环境变量 | 作用 |
|---|---|
| `AGENT_OBSERVABILITY_ENABLED=true` | 打开 ADOT 的 GenAI 观测默认设置：100% 采样，并把 baggage 里的 `session.id` 写到每个 span 上 |
| `OTEL_PYTHON_DISTRO=aws_distro`、`OTEL_PYTHON_CONFIGURATOR=aws_configurator` | 使用 AWS 发行版的采集与发送配置（SigV4 签名，用 EC2 的实例角色） |
| `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf` | 发送协议 |
| `OTEL_TRACES_EXPORTER=otlp` | span 发到 X-Ray 的 OTLP 端点，进入 CloudWatch Logs 的 `aws/spans`（Transaction Search） |
| `OTEL_LOGS_EXPORTER=otlp` | 模型输入 / 输出内容作为日志事件发送 |
| `OTEL_RESOURCE_ATTRIBUTES` | `service.name` 是控制台里显示的 Agent 名；`aws.log.group.names` 是关联的日志组 |
| `OTEL_EXPORTER_OTLP_LOGS_HEADERS` | 模型输入 / 输出事件写到哪个日志组、哪个日志流（`runtime-logs`） |

:::alert{header="用默认名，不要改" type="warning"}
`service.name` 和日志组就是控制台里显示的 Agent 名，也是 E1.8 在线评估筛选数据的条件（`labkit/online.py` 用的是 `LOCAL_SERVICE_NAME` / `LOCAL_LOG_GROUP`，由 `labkit/config.py` 按前缀和后缀算出）。日志组要先存在：`./ext-local-agent.sh trace` 和 `./42-run-golden.sh --mode local` 启动前会先创建它（`labkit/loggroup.py`）。自带 Agent 为什么也不要改名，见[扩展 3 第 E3.4 步](/90-extensions/e3-own-agent#e3-4)。
:::

## E1.4 读关键代码：session.id、根 span、最终输出 {#e1-4}

评估器对数据有三点要求：① 每个 span 带 `session.id`；② 一条需求 = 一个 session = 一个 trace；③ 能拿到 Agent 的最终输出（JSON）。下面三段代码说明 mock agent 怎样满足它们。这一步只看不改，也不需要运行。

**① 先设环境变量，再用 `opentelemetry-instrument` 启动。** `ext-local-agent.sh` 先建日志组、设置 E1.3 的环境变量，再用 `opentelemetry-instrument` 启动批量脚本，ADOT 就在这个进程里工作：

```
"${_ADLC_PY}" -m labkit.loggroup "$LOCAL_LOG_GROUP"
source telemetry/otel_env.sh "$LOCAL_SERVICE_NAME" "$LOCAL_LOG_GROUP"
…
exec "$OTEL_BIN" "${_ADLC_PY}" -m labkit.batch --mode local --limit "$N" "$@"
```

**② 每条需求一个新的 session.id，放进 OTel baggage。** `labkit/batch.py` 的 `_local_one()`：

```
token = context.attach(baggage.set_baggage("session.id", sid))
try:
    return triage(items.item_of(row), version=version, model_id=model, session_id=sid)
finally:
    context.detach(token)
```

`sid` 形如 `adlc-ws-v0-FB-2610-001-<32 位十六进制>`（`_session_id()`）。`AGENT_OBSERVABILITY_ENABLED=true` 让 ADOT 把 baggage 里的 `session.id` 写到之后产生的每个 span 上；评估服务按 session 取数据。

**③ 外层包一个 adlc.triage span，把最终输出 JSON 写在上面。** `agent/adlc_agent/core.py` 的 `triage()`：

```
with tracer.start_as_current_span("adlc.triage") as span:
    span.set_attribute("session.id", session_id)
    span.set_attribute("adlc.agent_version", version)
    if fid:
        span.set_attribute("adlc.feedback_id", fid)
    …
    out = parse_output(text)
    …
    span.set_attribute("adlc.triage_output", json.dumps(out, ensure_ascii=False))
```

这个 span 是每条需求的根 span，评估脚本靠它找到 trace（`labkit/spans.py`）。`adlc.triage_output` 是评估器首选的读取位置（`evaluators/README.md` 第 2 节），不依赖 `chat` span 有没有记录消息正文；`adlc.feedback_id` 用来和黄金集对上。

Strands 框架自己会产生 `invoke_agent` / `chat` / `execute_tool` span，上面三段代码补上了其余部分。你们自己的 Agent 要加的是同样三点（约 15 行，与框架无关），见[扩展 3 第 E3.5 步](/90-extensions/e3-own-agent#e3-5)。

:::alert{header="想看上下文" type="info"}
在 `~/adlc-lab` 里运行 `claude`，让 Claude Code 讲解 `labkit/batch.py` 的 `_local_one()` 和 `agent/adlc_agent/core.py` 的 `triage()`，比如“这两个函数怎样保证一条需求只有一个 session”。只让它讲，不要让它改。
:::

## E1.5 带遥测发送 3 条需求 {#e1-5}

先发少量数据确认链路通了；完整黄金集在 E1.9 跑（要先开启在线评估）。

```bash
./ext-local-agent.sh trace 3 --version v0
```

**预期输出**（时间和 run_id 以你看到的为准）：

```
  🆕 创建日志组 /aws/bedrock-agentcore/adlc-ws-local-agent
📡 OTel 已配置：service.name=adlc-ws-local-agent · 日志组 /aws/bedrock-agentcore/adlc-ws-local-agent · 区域 us-west-2
… WARNING [opentelemetry.instrumentation.logging] … Skipping installation of LoggingHandler …
… WARNING [opentelemetry.instrumentation.instrumentor] … Attempting to instrument while already instrumented
… INFO [strands.telemetry.metrics] … Creating Strands MetricsClient
▶ 运行 3 条需求 · EC2 上自托管 · 版本 v0 · 模型 us.anthropic.claude-sonnet-5-5 · 并发 4
  [ 1/3] FB-2610-001       17.3s  重要=是 紧急=否 建议=迭代
  [ 2/3] FB-2610-002       24.2s  重要=是 紧急=否 建议=待评估
  [ 3/3] FB-2610-003       11.1s  重要=是 紧急=是 建议=迭代
✅ 完成：3 条，用时 25s，失败 0 条 → results/run-local-v0-20261105T135653Z.jsonl
   run_id = local-v0-20261105T135653Z
```

中间几行 `WARNING` / `INFO` 是 OpenTelemetry 和 Strands 自己的提示，属于正常现象；最后出现 `✅ 完成` 就说明数据已经发出。再次运行时第一行是 `✔ 日志组已存在 …`。记下最后一行的 run_id（`local-v0-…`），E1.7 要用。

:::alert{header="CloudWatch 有 1–3 分钟延迟" type="warning"}
trace 进入 CloudWatch 要 1–3 分钟。等两三分钟再做 E1.6 和 E1.7。
:::

## E1.6 在 CloudWatch 查看 trace {#e1-6}

1. 打开 **CloudWatch** 控制台（右上角区域与实验一致）→ 左侧 **GenAI Observability** → **Bedrock AgentCore**。
2. 在 **Agents** 列表里找到 `adlc-ws-local-agent`。不在 Runtime 上的 Agent 也会列出来，按 `service.name` 显示；主线的 `adlc_ws_agent` 也在这个列表里。
3. 点进去 → **Sessions**：3 个 session，名字形如 `adlc-ws-v0-FB-2610-001-…` → 点任一 session → **Traces**：看调用树。
4. （可选）**CloudWatch** → **Application Signals** → **Transaction search**：筛选 `service.name = adlc-ws-local-agent`，看原始 span。

**预期结果**：3 个 session，每个 1 个 trace。实测的 trace 结构（span 名）是 `adlc.triage` → `invoke_agent adlc_triage_v0` → `execute_event_loop_cycle` → `chat` / `execute_tool lookup_capability`，另有一个 `chat us.anthropic.claude-sonnet-5-5` 是 Bedrock 调用本身。模型的输入和输出内容在日志组 `/aws/bedrock-agentcore/adlc-ws-local-agent` 里，晚 1–3 分钟出现。控制台菜单名称可能随版本略有不同。

## E1.7 遥测检查：数据能否被 Evaluations 使用 {#e1-7}

```bash
./ext-check-telemetry.sh --run local-v0-<时间>
```

`<时间>` 换成 E1.5 最后一行 run_id 里的时间；不加 `--run` 时检查 `.lab-state/runs.json` 里最后登记的那次运行。脚本 `ext-check-telemetry.sh` 调用 `labkit/check_telemetry.py`：逐项检查 session、trace、模型输入输出、工具调用等是否齐全，最后真的调用一次 Evaluations（`Builtin.Helpfulness`）做试评估，证明服务能读懂这些数据。

**预期输出**（节选，数字以你看到的为准）：

```
▶ 检查 run local-v0-20261105T135653Z（local）的 3 个 session · 日志组 /aws/bedrock-agentcore/adlc-ws-local-agent
  · adlc-ws-v0-FB-2610-001-…  span=… 文档=…
  …

检查项（✅ 全部 session 满足 · ⚠️ 部分满足 · ❌ 都不满足）
  ✅ [必需] 能在 CloudWatch（aws/spans）按 session.id 找到 span  (3/3)
  ✅ [必需] 每个 span 都带 session.id  (3/3)
  ✅ [必需] span 来自受支持的框架埋点（如 Strands: strands.telemetry.tracer）  (3/3)
  ✅ [必需] 有 Agent 调用 span（gen_ai.operation.name = invoke_agent）  (3/3)
  ✅ [必需] 能取到用户输入内容  (3/3)
  ✅ [必需] 能取到 Agent 最终输出内容  (3/3)
  ✅ [可选] 有工具调用 span（execute_tool）→ 工具类评估器可用  (3/3)
  ✅ [可选] 记录了模型 ID（gen_ai.request.model）  (3/3)
  ✅ [可选] 记录了 token 用量 → 成本分析可用  (3/3)
  ✅ [可选] 带需求编号 / 判断结果属性 → 标签准确率评估器最稳  (3/3)
  ✅ [可选] 带 service.name → 在线评估 / 控制台按 Agent 聚合  (3/3)
  埋点来源 scope：['opentelemetry.instrumentation.botocore.bedrock-runtime', 'adlc.triage', 'strands.telemetry.tracer']   service.name：['adlc-ws-local-agent']
  ✅ 试评估（Builtin.Helpfulness）成功：Very Helpful 0.83

🟩 结论：通过 —— 数据满足 AgentCore Evaluations 的要求。可以按手册第 4 章评估。
```

检查最多取 5 个 session（`--sessions N` 可改，`--no-probe` 跳过试评估）。数据刚发出时会先打印几行 `⏳ 等待数据在 CloudWatch 中可查询…`，脚本最多自动等 3 分钟。结论是 🟩 就继续；其它结论的含义和处理办法见[扩展 2 第 E2.7 步](/90-extensions/e2-existing-logs#e2-7)。mock agent 正常不会缺必需项：出现 ❌ 时多半是数据还没到，等 2–3 分钟再运行一次。

## E1.8 开启自托管 Agent 的在线评估 {#e1-8}

[第 4.3 步](/04-evaluate#s-4-3)开启的在线评估（配置 `adlc_ws_online_runtime`）只看 Runtime 上的 Agent。自托管的 Agent 要单独开一个，数据源是 `aws/spans` 加日志组 `/aws/bedrock-agentcore/adlc-ws-local-agent`，服务名 `adlc-ws-local-agent`：

```bash
./41-online-eval.sh enable --mode local
```

脚本调用 `labkit/online.py`：采样 100%，session 空闲 2 分钟后开始打分；挂的评估器和第 4.3 步完全相同（`label_accuracy` / `label_important` / `label_urgent` / `label_decision` / `reject_quality` 加 `Builtin.Helpfulness`；P0/P1/P2 分级口径下再加 `label_level`）。

**预期输出**（配置 ID 末尾的随机字符每个人不同）：

```
✅ 已创建在线评估配置 adlc_ws_online_local（adlc_ws_online_local-…），采样 100%，session 空闲 2 分钟后打分
   之后每个新 session 都会自动评分，结果写入 CloudWatch：
   CloudWatch 控制台 → GenAI Observability → Bedrock AgentCore → 你的 Agent → Evaluations
```

前面可能先出现一两行 `⏳ 等待 IAM 权限生效后重试（1/7）`，脚本会自动重试。已经创建过时显示 `✅ 在线评估配置 adlc_ws_online_local 已启用（…）`。查看两个配置的状态：

```bash
./41-online-eval.sh status
```

**预期输出**（ID 和状态以你看到的为准）：

```
AgentCore Runtime: adlc_ws_online_runtime adlc_ws_online_runtime-… status=… execution=ENABLED
EC2 上自托管的 mock agent: adlc_ws_online_local adlc_ws_online_local-… status=… execution=ENABLED
```

:::alert{header="先开启，再跑黄金集" type="warning"}
在线评估只给开启之后产生的新 session 打分，所以一定要先完成这一步，再做 E1.9。日志组也要先存在：E1.5 已经建好；没做 E1.5 时先运行 `.venv/bin/python -m labkit.loggroup`，否则开启时会重试 7 次后报 `One or more specified log groups do not exist`。
:::

## E1.9 跑黄金集（自托管） {#e1-9}

```bash
./42-run-golden.sh --mode local --version v0
```

和[第 4.4 步](/04-evaluate#s-4-4)一样调用 `labkit/batch.py`（并发 4，每条需求一个 session），只是 Agent 在这台 EC2 上运行、trace 由 ADOT 发送。结果写到 `results/run-local-v0-<时间>.jsonl`，并登记到 `.lab-state/runs.json`。

**预期输出**（约 2 分钟；条数是你们在第 2.4 步冻结后的黄金集条数，数字以你看到的为准）：

```
  ✔ 日志组已存在 /aws/bedrock-agentcore/adlc-ws-local-agent
📡 OTel 已配置：service.name=adlc-ws-local-agent · 日志组 /aws/bedrock-agentcore/adlc-ws-local-agent · 区域 us-west-2
… WARNING [opentelemetry.instrumentation.logging] … Skipping installation of LoggingHandler …
▶ 运行 30 条需求 · EC2 上自托管 · 版本 v0 · 模型 us.anthropic.claude-sonnet-5-5 · 并发 4
  [ 1/30] FB-2610-001       13.3s  重要=是 紧急=否 建议=待评估
  [ 2/30] FB-2610-002       17.5s  重要=是 紧急=否 建议=待评估
  …
✅ 完成：30 条，用时 115s，失败 0 条 → results/run-local-v0-20261105T142551Z.jsonl
   run_id = local-v0-20261105T142551Z
```

V0 的典型表现和在 Runtime 上一样：几乎都判“重要”，处理建议大量是“待评估”。个别 `❌`（超时、限流）不影响后续，评估时会跳过这些条目；失败太多就再跑一次。

## E1.10 按需评估，和 Runtime 上的 V0 对比 {#e1-10}

跑完 E1.9 后先等 2–3 分钟（trace 写入 CloudWatch 有延迟；脚本找不到 trace 时也会自己等待再查）：

```bash
./43-run-eval.sh --run local-v0-<时间>
```

这和[第 4.5 步](/04-evaluate#s-4-5)是同一个脚本、同一组评估器、同一份黄金集答案（作为参考输入传给评估器）；唯一的区别是 trace 来自 ADOT，而不是 Runtime。

**预期输出**（节选，数字以你看到的为准）：

```
▶ 评估 run local-v0-20261105T142551Z：30 个 session × 8 个评估器
  评估器：label_accuracy, label_important, label_urgent, label_decision, reject_quality, reject_quality_llm, Builtin.Correctness, Builtin.Helpfulness
  [ 1/30] FB-2610-001    三项=Pass     重要=Pass  紧急=Pass  建议=Pass  话术=不适用
  [ 2/30] FB-2610-002    三项=Fail     重要=Fail  紧急=Pass  建议=Fail  话术=不适用
  …

═══ 结果（…s）· 三大支柱：红线 0 条 + 门禁各 ≥ 70% 才通过 ═══
  红线（一票否决，要求 0 条）：1 条 ❌  误拒 0 · 话术红线 1
    ⛔ FB-2610-041（G29）话术红线：…
  门禁（各 ≥ 70%）：
    是否重要准确率：67% ❌
    是否紧急准确率：80% ✅
    处理建议准确率：40% ❌
    拒绝话术通过率：67%（2/3 条 Pass） ❌
  观测（只看趋势，不影响结论）：整条通过率 23% · 格式错误 1 条 · 话术均分 0.57（3 条）
    …
  ❌ 不通过：红线 1 条（一票否决）；门禁未达标：是否重要准确率、处理建议准确率、拒绝话术通过率 · 详细结果：results/eval-local-v0-20261105T142551Z.json
```

把它和你在第 4.5 步得到的 Runtime V0 结果放在一起看：同一个 V0、同一份黄金集、同一组评估器，结论通常一致（都不通过）；各项准确率差几个百分点，是大模型每次运行的正常波动。结果的读法见[第 4.6 步](/04-evaluate#s-4-6)；失败归因照[第 4.7 步](/04-evaluate#s-4-7)：

```bash
./44-failures.sh results/eval-local-v0-<时间>.json
```

**（可选）把两次 V0 放进同一张对比页：**

```bash
./51-compare.sh --before runtime-v0-<时间> --after local-v0-<时间>
```

`runtime-v0-<时间>` 是第 4.4 步的 run_id（也可以在 `.lab-state/runs.json` 里查）。两边版本相同，对比页显示为“V0（前）”和“V0（后）”，“运行位置”一行分别是 AgentCore Runtime 和 EC2 上自托管的 mock agent。这里的“修复 / 退步”只是两次运行之间的波动，不是优化效果。对比页生成后同样会上传到你的私有 S3 桶并打印控制台链接（和[第 5.5 步](/05-optimize#s-5-5)一样）。

## E1.11 在控制台查看评估结果 {#e1-11}

1. **CloudWatch** → **GenAI Observability** → **Bedrock AgentCore** → 点 `adlc-ws-local-agent`。
2. **Sessions** / **Traces**：E1.9 的新 session；点开任一 trace 看 Agent 的推理、工具调用和输出。
3. **Evaluations** 页签：E1.8 在线评估的结果。session 空闲 2 分钟后才开始打分，所以 E1.9 跑完约 5–10 分钟分数才陆续出现；能看到每个评估器的平均分、分布，以及每个 session / trace 的得分与解释。E1.5 那 3 条是开启在线评估之前发的，不会有分数。
4. （可选）**CloudWatch** → **Metrics** → 命名空间 `Bedrock-AgentCore/Evaluations`：维度是 `service.name` 和 `onlineEvaluationConfigId`，`service.name` 选 `adlc-ws-local-agent`。

**预期结果**：页面和主线里看 Runtime 上的 Agent 时一样，只是 Agent 名不同。在线评估不传黄金集参考输入，标签准确率评估器用随 Lambda 打包的黄金集按需求编号对答案，所以 `label_important` / `label_urgent` / `label_decision` 的平均分与 E1.10 基本一致。控制台菜单名称可能随版本略有不同。

## E1.12 （可选）用自托管的 Agent 生成分诊报告 {#e1-12}

```bash
./60-triage-report.sh --mode local
```

这和[第 6.3 步](/06-release-observe#s-6-3)是同一个脚本（`labkit/report.py` 生成报告，`labkit/publish.py` 上传）：用**当前版本**（`config/local.env` 的 `AGENT_VERSION`，做过第 5.3 步就是 V1 或 V1 参考版）在这台 EC2 上处理 `data/feedback_last_month.jsonl` 里的上月需求（带遥测，并发 6），生成报告并上传到你的私有 S3 桶。

**预期输出**（约 2–3 分钟，版本、条数和时间以你看到的为准）：

```
  ✔ 日志组已存在 /aws/bedrock-agentcore/adlc-ws-local-agent
📡 OTel 已配置：service.name=adlc-ws-local-agent · 日志组 /aws/bedrock-agentcore/adlc-ws-local-agent · 区域 us-west-2
…
▶ 运行 60 条需求 · EC2 上自托管 · 版本 v1-ref · 模型 us.anthropic.claude-sonnet-5-5 · 并发 6
  [ 1/60] FB-2610-001       11.2s  重要=是 紧急=否 建议=迭代
  …
✅ 完成：60 条，用时 142s，失败 0 条 → results/run-local-v1-ref-20261105T145251Z.jsonl
   run_id = local-v1-ref-20261105T145251Z
▶ 分诊报告：local-v1-ref-20261105T145251Z · 60 条
✅ 分诊报告已生成（本地文件，可直接用浏览器打开）：
results/triage-report-local-v1-ref-20261105T145251Z.html
☁️  已上传到私有桶 s3://adlc-ws-<account>-us-west-2/reports/triage-report-local-v1-ref-20261105T145251Z.html
👉 在笔记本浏览器打开（需已登录控制台），点右上角「打开」查看：
   https://us-west-2.console.aws.amazon.com/s3/object/…
```

在已登录控制台的浏览器里打开脚本打印的链接，在 S3 对象页面点右上角 **打开**（Open）。报告的“运行位置”一行显示“EC2 上自托管的 mock agent（ADOT 埋点）”，其余版式和第 6.3 步的报告相同。这批需求大部分不在黄金集里：E1.8 的在线评估照样给话术质量和 Helpfulness 打分，标签准确率评估器对不在黄金集里的条目返回 `Skipped`，不计分。

:::alert{header="清理" type="info"}
不用单独清理：[第 9 章](/09-cleanup)的 `./99-cleanup.sh` 按前缀删除你的资源，包括在线评估配置 `adlc_ws_online_local` 和日志组 `/aws/bedrock-agentcore/adlc-ws-local-agent`。只想停掉自托管的在线评估，运行 `./41-online-eval.sh disable --mode local`。
:::

## 检查一下 {#e1-check}

**自托管的 Agent 和 Runtime 上的 Agent 用的是同一份黄金集和同一组评估器吗？为什么这一点决定了分数能不能比？**（对照 E1.8 和第 4.3 步挂的评估器、E1.10 和第 4.5 步的命令想一想：如果换运行位置的同时也换了黄金集的版本，E1.10 那张对比页上的差异还能说明什么？）

下一个扩展：[扩展 2 已有日志接入](/90-extensions/e2-existing-logs)
