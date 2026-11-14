---
title: "扩展 3 自带 Agent 接入指南"
weight: 30
---

> **你在这里**：① 定义 → **② 构建** → **③ 评估** → ④ 发布 → ⑤ 观测 → ⑥ 回流 · 圆心：黄金标准

**这个扩展做什么**：主线和扩展 1、2 用的都是实验自带的 mock agent。这份指南说明怎样把你们自己的 Agent 接到同一套评估流程（黄金集 + 评估器 + 对比页），有两种方式：改几行代码、用 ADOT 埋点（**埋点接入**，[扩展 1](/90-extensions/e1-self-hosted) 用 mock agent 走过一遍），或者只提供 Agent 的调用日志（**日志接入**，[扩展 2](/90-extensions/e2-existing-logs) 用样例日志走过一遍）。

**什么时候做**：会后自助，本页可以单独阅读。现场不接入你们自己的 Agent。

**用时**：通读约 15 分钟；实际接入的时间取决于你们的 Agent。

:::alert{header="验证状态与资源名" type="info"}
每一步标题下标了验证状态：

- **已实测**：在真实账号里按本页步骤跑通过（2026-11-05 / 11-06，us-west-2）；
- **按代码核对**：对照实验代码确认，没有单独实测；
- **未验证**：来自 AWS 文档或推断，请以你们的实际结果为准。

本页的资源名用实验 EC2 上的个人后缀 `ws`（例如 `adlc-ws-local-agent`）；在你们自己的账号里，换成你设置的后缀。
:::

## E3.1 开始前：准备环境和数据 {#e3-1}

*验证状态：已实测*

准备好运行脚本的机器、账号级设置和评估器，后面每一步都依赖它们。

- **账号**：一个专用的实验账号，不要用生产账号（脚本会创建 IAM 角色、Lambda、AgentCore 资源）。
- **运行脚本的机器**：活动期间可以直接用本次的实验 EC2（后缀 `ws`、Transaction Search、评估器都已就绪）。活动结束后实验账号会被回收；要在你们自己的实验账号里接着做，先向讲师了解怎样获取实验材料（就是实验 EC2 上 `~/adlc-lab` 这一套）。在自己电脑上使用时，在材料的根目录建虚拟环境并安装依赖：`uv venv --python 3.12 .venv && uv pip install -r requirements.txt`（见 `requirements.txt` 第一行），再在 `config/local.env` 里写 `AWS_PROFILE`。下面所有 `./…sh` 命令都在这个根目录执行。
- **个人后缀**：只在自己的账号里需要设置，运行 `./tools/set-suffix.sh <后缀>`，资源名都由它派生。实验 EC2 上已经设为 `ws`，不要改。
- **Transaction Search**：实验账号已经开好（`./10-check-env.sh` 会显示状态）；自己的账号里运行 `./11-enable-observability.sh`。不开它，span 进不了 CloudWatch 的 `aws/spans`，评估服务就找不到数据。注意这是账号 + 区域级设置：它把这个区域所有 X-Ray trace 改为写入 CloudWatch Logs，并把 span 索引比例设为 100%，会产生 CloudWatch 费用；已经开着就不用再运行。只有是本实验开启的情况下，`./99-cleanup.sh --all --account-level` 才会还原它（[第 9 章](/09-cleanup)）。
- **黄金集和评估器**：黄金集按[第 2.4 步](/02-define/24-golden)冻结；评估器用 `./40-create-evaluators.sh` 部署和注册（[第 4.2 步](/04-evaluate#s-4-2)）。

**让你们的 Agent 处理哪些数据**：黄金集的需求在 `data/golden/golden_set.jsonl`，每行的 `input` 字段就是 Agent 的输入（字段说明见 `data/schema/feedback_item.schema.json`），需求编号在 `feedback_id`。“优化前”和“优化后”都用同样的这批需求，分数才可比。

**Agent 要输出什么**：每条需求输出一个 JSON，字段见 `evaluators/README.md` 第 1 节（JSON Schema 见 `data/schema/agent_output.schema.json`）：

```
{"feedback_id": "FB-2610-001", "important": "是", "urgent": "否", "decision": "迭代",
 "level": null, "reasoning": "…", "reject_reason": "", "customer_reply": ""}
```

`important` / `urgent` 取 `是` / `否`，`decision` 取 `迭代` / `拒绝` / `待评估`；判“拒绝”时 `reject_reason` 和 `customer_reply` 必填；`level` 只在 P0/P1/P2 分级口径下才填。标签准确率评估器按这些字段打分，解析不出时记 `FormatError`。

## E3.2 选择接入方式：埋点接入，还是日志接入 {#e3-2}

两种方式最后都用同一组评估器、同一个对比页，区别只在数据怎么进 CloudWatch。

| | 埋点接入（改代码，ADOT） | 日志接入（只提供日志） |
|---|---|---|
| 你们要做的事 | 安装 ADOT、设置环境变量，在每次处理请求的入口加约 15 行代码 | 把每次 Agent 调用导出成一行 JSON（JSONL 文件） |
| CloudWatch 里的数据 | Agent 运行时产生的真实 trace（含框架自己的 span、工具调用、token 用量） | 转换脚本按日志生成的 trace，时间重排到导入时刻 |
| 在线评估（控制台自动打分） | 支持，但服务名必须用实验默认名（E3.4） | 不支持 |
| 按需评估 / 对比页 | 支持，要先手动登记一次运行（E3.8–E3.9） | 支持，导入时自动登记 |
| 适合 | 能改 Agent 代码，想上线后持续评估 | 改不了代码，或想先快速看一次分数 |
| 步骤 | [E3.3](/90-extensions/e3-own-agent#e3-3)–E3.10（E3.6 的在线评估可选） | [E3.11](/90-extensions/e3-own-agent#e3-11)–E3.14 |

两种方式的优化与再评估都看 [E3.15](/90-extensions/e3-own-agent#e3-15)，已知限制看 [E3.16](/90-extensions/e3-own-agent#e3-16)。

## E3.3 埋点接入 ① 安装 ADOT（AWS OpenTelemetry 发行版） {#e3-3}

*验证状态：未验证*

在你们 Agent 的 Python 环境里装上负责采集和发送 trace 的组件：

```bash
pip install "aws-opentelemetry-distro>=0.18" boto3
pip install "strands-agents[otel]"   # 只有 Strands Agent 需要
```

装好后有 `opentelemetry-instrument` 命令，Agent 要用它启动（E3.7）。LangGraph / LangChain 等框架的埋点包和支持情况见 AgentCore 文档 [Telemetry setup and delivery](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/supported-frameworks-telemetry.html)。

:::alert{header="实测范围" type="info"}
实测时用的是实验自带的 `.venv`（依赖见 `agent/requirements.txt`，Strands + ADOT），没有单独实测上面这两条 pip 命令（版本下限按 AgentCore 文档；实验自己写的是 `>=0.12`）。E3.5 的代码只用 OpenTelemetry 的 Python 接口，所以这种方式面向 Python Agent。评估还需要框架自己产生的 GenAI span（Agent 调用、模型输入输出）：Strands 已实测；LangChain / LangGraph、OpenInference、CrewAI 的埋点在检查器的支持列表里（`labkit/check_telemetry.py` 的 `SUPPORTED_SCOPES`），没有实测；不用这些框架的 Agent 见 E3.16。
:::

## E3.4 埋点接入 ② 配置环境变量：用实验默认的服务名 {#e3-4}

*验证状态：已实测*

告诉 ADOT 把数据发到哪个区域、哪个日志组、服务叫什么名字：

```bash
source ./00-config.sh && source telemetry/otel_env.sh
```

**预期输出**：

```
📡 OTel 已配置：service.name=adlc-ws-local-agent · 日志组 /aws/bedrock-agentcore/adlc-ws-local-agent · 区域 us-west-2
```

| 环境变量（`telemetry/otel_env.sh`） | 作用 |
|---|---|
| `AGENT_OBSERVABILITY_ENABLED=true` | ADOT 的 GenAI 观测默认设置：100% 采样，并把 baggage 里的 `session.id` 写到每个 span 上 |
| `OTEL_PYTHON_DISTRO=aws_distro`、`OTEL_PYTHON_CONFIGURATOR=aws_configurator` | 使用 AWS 发行版的采集与发送配置（SigV4 签名） |
| `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`、`OTEL_TRACES_EXPORTER=otlp` | span 发到 X-Ray 的 OTLP 端点，进入 CloudWatch Logs 的 `aws/spans`（Transaction Search） |
| `OTEL_LOGS_EXPORTER=otlp`、`OTEL_EXPORTER_OTLP_LOGS_HEADERS` | 模型输入 / 输出内容作为日志事件，写到上面的日志组（日志流 `runtime-logs`） |
| `OTEL_RESOURCE_ATTRIBUTES` | `service.name`（控制台里的 Agent 名）和 `aws.log.group.names`（关联的日志组） |

:::alert{header="不要改服务名和日志组" type="warning"}
在线评估（`./41-online-eval.sh enable --mode local`）只看 `LOCAL_SERVICE_NAME` 和 `LOCAL_LOG_GROUP` 这两个值（`labkit/online.py`）。它们由 `labkit/config.py` 的 `derive()` 按前缀和后缀算出来，在 `config/local.env` 或环境变量里设置它们不生效（会被覆盖）。如果给 `otel_env.sh` 传自己的名字（`source telemetry/otel_env.sh <服务名> <日志组>`），trace 照样进 CloudWatch、按需评估也能用，但在线评估一条都不会打分。实测：同一个在线评估配置、同一段时间里，默认名的 session 3/3 被打分，自定义名的 0/5。
:::

**代价**（如实说明）：控制台里你们的 Agent 显示为 `adlc-ws-local-agent`。如果用同一个后缀也跑过 mock agent（扩展 1），两者的 session 会在同一个 Agent 名下。区分方法：mock agent 的 `session.id` 形如 `adlc-ws-v0-FB-2610-001-<32 位十六进制>`，E3.5 的代码生成的形如 `adlc-<uuid>`。想完全分开，可以在你们自己的账号里换一个新后缀：在自己电脑上（没有实验权限边界）运行 `./tools/set-suffix.sh <新后缀>`，再重新注册评估器（`./40-create-evaluators.sh`）（按代码核对）。实验 EC2 上的后缀和它的 CloudFormation 堆栈绑定（权限边界按后缀命名，换了后缀，脚本会报“找不到实验权限边界”），不要改。

- 在你们自己的 shell 脚本里使用时，写成 `source telemetry/otel_env.sh "$LOCAL_SERVICE_NAME" "$LOCAL_LOG_GROUP"`（效果和不带参数一样）。在脚本里不带参数 `source` 时，脚本自己的 `$1` / `$2` 会被当成服务名和日志组（实测遇到过）。
- Agent 不在这台机器上运行（容器、你们自己的服务器）：把上面这些环境变量按输出里的值设到运行 Agent 的地方，再设好 `AWS_REGION` 和能写 X-Ray、CloudWatch Logs 的 AWS 凭证。需要的 IAM 权限见 AgentCore 文档 “Add observability to agents hosted outside AgentCore Runtime”（未验证）。

## E3.5 埋点接入 ③ 改代码：每条需求一个 session，最终输出写到 span 上 {#e3-5}

*验证状态：已实测*

评估服务按 session 取数据，评估器要能读到 Agent 的最终输出；下面这段代码把两件事都做了。把它放在每次处理一条需求的入口（HTTP 接口的处理函数、队列的消费函数、批处理循环里的每一条），不要放在程序启动时。把 `my_agent(item)` 换成你们原来的 Agent 调用：它要返回一个 dict，字段见 E3.1（返回的是 JSON 文本时先 `json.loads`）。

```python
import json
import uuid

from opentelemetry import baggage, context, trace

tracer = trace.get_tracer("adlc.triage")


def handle_one(item: dict) -> dict:
    """处理一条需求：一条需求 = 一个 session = 一个 trace。在每次处理请求的入口调用。"""
    session_id = f"adlc-{uuid.uuid4()}"  # 每条需求一个新的唯一 ID
    token = context.attach(baggage.set_baggage("session.id", session_id))  # 之后产生的 span 都带上它
    try:
        with tracer.start_as_current_span("adlc.triage") as span:
            span.set_attribute("session.id", session_id)
            fid = item.get("id") or item.get("feedback_id")  # 需求编号：按你们的字段名改
            if fid:
                span.set_attribute("adlc.feedback_id", fid)
            result = my_agent(item)  # 换成你们原来的 Agent 调用；返回 dict，字段见 evaluators/README.md 第 1 节
            span.set_attribute("adlc.triage_output", json.dumps(result, ensure_ascii=False, default=str))
            return result
    finally:
        context.detach(token)
```

同一段代码在 `telemetry/snippets/a34_session_wrapper.py`（实验里的测试会运行它，并检查我们的评估器能读出结果）。

| 这段代码做了什么 | 为什么需要 |
|---|---|
| 每条需求生成新的 `session.id`，放进 OTel baggage | ADOT（`AGENT_OBSERVABILITY_ENABLED`）把它写到之后产生的每个 span 上；评估服务按 session 取数据，一条需求 = 一个 session = 一个 trace |
| 包一个名为 `adlc.triage` 的 span | 每条需求有一个可识别的根 span：按需评估、遥测检查和 E3.9 的登记查询都靠它（或 `invoke_agent` span）找到 trace（`labkit/spans.py`） |
| 写 `adlc.feedback_id` 和 `adlc.triage_output` | 需求编号用来对黄金集；最终输出 JSON 是评估器首选的读取位置（`evaluators/README.md` 第 2 节），不依赖框架有没有记录消息正文 |

- 处理函数是 async 的（FastAPI 等）：把 `def handle_one` 改成 `async def`，把 `my_agent(item)` 改成 `await my_agent(item)`，其余不变。
- 如果 Agent 把活交给自己开的线程或进程（线程池、multiprocessing 等），那里面的 span 可能拿不到这个上下文，`session.id` 会丢：尽量在同一个线程里调用 Agent，或者把 context 显式传过去（未验证）。

实测方式：一个独立的 Strands Agent（不经过实验的批量脚本）原样套用这段代码，跑 5 条黄金集需求；登记后 8 个评估器全部出分，遥测检查 🟩 通过（E3.10）。

## E3.6 埋点接入 ④ 先建日志组，再开启在线评估（控制台自动打分） {#e3-6}

*验证状态：已实测*

日志组要先存在；在线评估要在 Agent 运行之前开启，开启之前产生的 session 不会被打分。

```bash
.venv/bin/python -m labkit.loggroup
./41-online-eval.sh enable --mode local
```

**预期输出**（配置 ID 末尾的随机字符每个人不同）：

```
  🆕 创建日志组 /aws/bedrock-agentcore/adlc-ws-local-agent
…
✅ 已创建在线评估配置 adlc_ws_online_local（adlc_ws_online_local-…），采样 100%，session 空闲 2 分钟后打分
   之后每个新 session 都会自动评分，结果写入 CloudWatch：
   CloudWatch 控制台 → GenAI Observability → Bedrock AgentCore → 你的 Agent → Evaluations
```

日志组已经存在时显示 `✔ 日志组已存在 …`；在线评估配置已经创建过（例如做过扩展 1）时显示 `✅ 在线评估配置 adlc_ws_online_local 已启用（…）`。不带参数时，`labkit/loggroup.py` 创建的就是 E3.4 的默认日志组（保留 7 天）。

- 为什么先建日志组：模型输入 / 输出事件要写进去；而且日志组不存在时，`./41-online-eval.sh enable --mode local` 会失败（见 E3.16）。
- 前提：评估器已注册（`./40-create-evaluators.sh`）。只想做按需评估（E3.8–E3.10）时可以不开在线评估，但日志组照样要建。
- 先开启、再跑 Agent：只有开启之后产生的 session 会被打分；开启前的、以及改配置之前的旧 session 不会补打（实测）。
- 在线评估没有“标准答案”输入：标签准确率评估器按需求编号到随 Lambda 打包的黄金集里找答案；不在黄金集里的需求返回 `Skipped`，不计分。

## E3.7 埋点接入 ⑤ 运行 Agent，到 CloudWatch 查看 trace 和分数 {#e3-7}

*验证状态：已实测*

Agent 要在 `opentelemetry-instrument` 下启动，ADOT 才会工作：

```bash
opentelemetry-instrument python your_agent.py
```

- 在 E3.4 的同一个 shell 里执行（环境变量要在）。用实验的虚拟环境时写成 `.venv/bin/opentelemetry-instrument .venv/bin/python your_agent.py`。让它处理黄金集的全部需求（E3.1），记下开始时间（E3.9 要用）。
- 数据怎么走：span → ADOT → X-Ray OTLP 端点 → CloudWatch Logs 的 `aws/spans`（Transaction Search）；模型输入 / 输出事件 → E3.6 的日志组。进入 CloudWatch 有 1–3 分钟延迟。

然后在控制台确认数据到了：

1. **CloudWatch** 控制台（区域与实验一致）→ **GenAI Observability** → **Bedrock AgentCore** → **Agents**：找到 `adlc-ws-local-agent`（不在 Runtime 上的 Agent 按 `service.name` 列出）。
2. 点进去 → **Sessions**：每条需求一个 session（`adlc-<uuid>`）→ **Traces**：根是 `adlc.triage`，下面是框架自己的 span（Strands 是 `invoke_agent` → `chat` / `execute_tool …`）。
3. **Evaluations** 页签：session 空闲 2 分钟后才开始打分，所以跑完约 5–10 分钟分数才陆续出现；也可以看 CloudWatch 指标命名空间 `Bedrock-AgentCore/Evaluations`（维度 `service.name`）。
4. （可选）**CloudWatch** → **Application Signals** → **Transaction search**：筛选 `service.name = adlc-ws-local-agent` 看原始 span。控制台菜单名称可能随版本略有不同。

## E3.8 埋点接入 ⑥ 按需评估之前：先弄清“运行记录” {#e3-8}

*验证状态：已实测*

按需评估（`./43-run-eval.sh`）和遥测检查（`./ext-check-telemetry.sh`）不扫描 CloudWatch，只评估登记过的运行。一条“运行记录”由两部分组成：

- **运行文件** `results/run-<run_id>.jsonl`：每个 session 一行，把需求编号（`feedback_id`）和 `session_id` 对应起来。
- **登记项**：`.lab-state/runs.json`（一个 JSON 数组）里的一个对象，记录 run_id、运行文件路径、开始时间、日志组等。

实验的脚本只登记自己产生的运行：mock agent 的批量运行（`labkit/batch.py`）和日志接入的导入（`labkit/convert_logs.py`）。你们的 Agent 产生的 session 不会自动登记，要按 E3.9 手动登记。

:::alert{header="不带 --run 时，评估的不一定是你们的运行" type="error"}
`./43-run-eval.sh` 和 `./ext-check-telemetry.sh` 不带参数时取 `runs.json` 的最后一条（`labkit/evaluate.py` 的 `pick_run()`）。实验 EC2 上主线跑过很多次 mock agent，它会悄悄评估那次 mock 运行，还可能显示 ✅ 通过，看起来像是你们的 Agent 通过了（实测）。从没登记过任何运行时，`./43-run-eval.sh` 报 `❌ 还没有任何运行记录`，`./ext-check-telemetry.sh` 报 `❌ 没有运行记录`。所以：先登记，之后一律带 `--run <run_id>`，并核对输出第一行 `▶ 评估 run <run_id>：N 个 session` 里的 run_id 和条数。
:::

`results/` 和 `.lab-state/` 只在运行脚本的这台机器上（都在 `.gitignore` 里），所以登记要在你运行 `./43-run-eval.sh` 的同一台机器上做。

## E3.9 埋点接入 ⑦ 手动登记一次运行（4 步） {#e3-9}

*验证状态：已实测*

把你们这次运行的 session 写成运行文件并登记，之后 `./43-run-eval.sh`、`./51-compare.sh`、`./ext-check-telemetry.sh` 都能用它。

**第 1 步：在 aws/spans 里找到这次运行的 session。** **CloudWatch** → **Logs Insights**，日志组选 `aws/spans`，时间范围覆盖这次运行（例如开始前 5 分钟到结束后 5 分钟），运行：

```
fields @timestamp, attributes.session.id as sid, attributes.adlc.feedback_id as fid,
       attributes.adlc.triage_output as out, traceId
| filter name = "adlc.triage" and resource.attributes.service.name = "adlc-ws-local-agent"
| sort @timestamp asc
| limit 1000
```

每行是一条需求：`sid` 是 session_id，`fid` 是需求编号，`out` 是最终输出 JSON。同一 session 只取一行。同一时间段也跑过 mock agent 时，去掉 `sid` 形如 `adlc-ws-v0-…` / `adlc-ws-v1-…` 的行（那是 mock agent 的）。记下第一行的时间（第 3 步要用）。`@timestamp` 可能按浏览器所在时区显示（例如北京时间比 UTC 早 8 小时），第 3 步要换算成 UTC。

**第 2 步：写运行文件 `results/run-<run_id>.jsonl`**（UTF-8，每个 session 一行）。run_id 只用字母、数字和 `-`，建议写成 `own-<版本>-<开始时间>`，例如 `own-v0-20261106T113000Z`：

```
{"run_id": "own-v0-20261106T113000Z", "version": "v0", "mode": "own", "feedback_id": "FB-2610-001", "session_id": "adlc-0d3a2c0b-ca5f-456e-86b8-1745339607fd", "result": {"feedback_id": "FB-2610-001", "important": "是", "urgent": "否", "decision": "迭代", …}}
{"run_id": "own-v0-20261106T113000Z", "version": "v0", "mode": "own", "feedback_id": "FB-2610-002", "session_id": "adlc-9be4edb8-…", "result": {…}}
```

| 字段 | 必填 | 用途（`labkit/evaluate.py`） |
|---|---|---|
| `session_id` | 是 | 按它到 CloudWatch 查这个 session 的 span |
| `feedback_id` | 是 | 黄金集里的需求编号：评估时据此把标准答案作为参考输入传给评估器 |
| `result` | 建议 | Agent 的输出 JSON（第 1 步的 `out` 解析后的对象）；对比页的“实际”标签从这里取。没有就写 `null` |
| `error` | 否 | 这条处理失败时写错误文字：这条没有输出，在门禁里按错计 |
| `run_id`、`version`、`mode` | 否 | 只作说明；脚本用的是第 3 步登记项里的值 |

**第 3 步：在 `.lab-state/runs.json` 末尾加一个登记项。** 先备份：`cp .lab-state/runs.json .lab-state/runs.json.bak`；文件不存在时新建，内容写成 `[ {…} ]`。

```
{"run_id": "own-v0-20261106T113000Z",
 "file": "results/run-own-v0-20261106T113000Z.jsonl",
 "created": "20261106T113000Z",
 "version": "v0",
 "mode": "own",
 "log_group": "/aws/bedrock-agentcore/adlc-ws-local-agent",
 "service_name": "adlc-ws-local-agent",
 "source": "aws/spans service.name=adlc-ws-local-agent",
 "count": 30, "model": "-", "agentRuntimeArn": null}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `run_id` | 是 | 和第 2 步一致；`--run` 用的就是它 |
| `file` | 是 | 第 2 步的运行文件，路径相对实验根目录 |
| `created` | 是 | UTC 时间，格式 `YYYYMMDDTHHMMSSZ`。写第 1 步第一行的时间（换算成 UTC）或更早：`./43-run-eval.sh` 从它往前 10 分钟开始查 span，`./ext-check-telemetry.sh` 往前 60 分钟。写早了只是多查一段时间；写得比第一条请求晚 10 分钟以上就查不到（`./43-run-eval.sh` 报 `no spans found`）。拿不准时区就再往前写 1 小时以上 |
| `version` | 是 | `v0`（优化前）、`v1`（优化后）……评估结果和对比页用它显示 V0 / V1 |
| `mode` | 是 | 写 `own`（对比页的“运行位置”显示“自带 Agent（手动登记的运行）”）。不要用 `local` / `runtime` / `imported`，那是实验脚本自己的运行类型 |
| `log_group` | 建议 | E3.4 的日志组，模型输入 / 输出事件在这里。不写时只查 `aws/spans`，可能取不到输入输出内容（按代码核对） |
| 其余字段 | 否 | 只作说明 |

**第 4 步：检查文件格式。**

```bash
python3 -m json.tool .lab-state/runs.json > /dev/null && echo runs.json OK
python3 -c "import json,sys; [json.loads(l) for l in open(sys.argv[1], encoding='utf-8') if l.strip()]; print('jsonl OK')" results/run-own-v0-20261106T113000Z.jsonl
```

把第二条命令里的文件名换成你的。第 2、3 步也可以交给 Claude Code：把第 1 步的查询结果和上面两张字段表给它，让它写运行文件、在 `runs.json` 末尾追加登记项，写完同样用第 4 步检查。

实测：按同样字段登记的 5 个 session，按需评估、遥测检查和对比页都能直接使用。当时第 1 步的查询是用 Logs Insights API 运行的（没有在控制台里点），服务名用的是一个自定义名，所以查询和登记项里的服务名、日志组与上面不同，其余相同。

## E3.10 埋点接入 ⑧ 自检并按需评估 {#e3-10}

*验证状态：已实测*

先确认数据满足 Evaluations 的要求，再对照黄金集打分：

```bash
./ext-check-telemetry.sh --run own-v0-20261106T113000Z
./43-run-eval.sh --run own-v0-20261106T113000Z
```

**预期输出**（遥测检查，节选；实测是独立的 Strands Agent 加 E3.5 的代码、5 个 session，数字以你看到的为准）：

```
▶ 检查 run own-v0-20261106T113000Z（own）的 5 个 session · 日志组 /aws/bedrock-agentcore/adlc-ws-local-agent
  · adlc-0d3a2c0b-ca5f-456e-86b8-1745339607fd  span=10 文档=17
  …

检查项（✅ 全部 session 满足 · ⚠️ 部分满足 · ❌ 都不满足）
  ✅ [必需] 能在 CloudWatch（aws/spans）按 session.id 找到 span  (5/5)
  ✅ [必需] 每个 span 都带 session.id  (5/5)
  ✅ [必需] span 来自受支持的框架埋点（如 Strands: strands.telemetry.tracer）  (5/5)
  ✅ [必需] 有 Agent 调用 span（gen_ai.operation.name = invoke_agent）  (5/5)
  ✅ [必需] 能取到用户输入内容  (5/5)
  ✅ [必需] 能取到 Agent 最终输出内容  (5/5)
  …
  埋点来源 scope：['opentelemetry.instrumentation.botocore.bedrock-runtime', 'adlc.triage', 'strands.telemetry.tracer']   service.name：['adlc-ws-local-agent']
  ✅ 试评估（Builtin.Helpfulness）成功：Very Helpful 0.83

🟩 结论：通过 —— 数据满足 AgentCore Evaluations 的要求。可以按手册第 4 章评估。
```

**预期输出**（按需评估的结构；分数取决于你们的 Agent）：

```
▶ 评估 run own-v0-20261106T113000Z：5 个 session × 8 个评估器
  …
═══ 结果（…s）· 三大支柱：红线 0 条 + 门禁各 ≥ 70% 才通过 ═══
  红线（一票否决，要求 0 条）：…
  门禁（各 ≥ 70%）：
    是否重要准确率：…
    是否紧急准确率：…
    处理建议准确率：…
    拒绝话术通过率：…
  观测（只看趋势，不影响结论）：…
  ✅ 通过：无红线，门禁全部达标 · 详细结果：results/eval-own-v0-20261106T113000Z.json
```

最后一行是 `✅ 通过`、`❌ 不通过` 或 `⚠️ 不完整（不能判通过）` 加原因：红线（误拒、话术红线）0 条且门禁全部达标才通过，读法见[第 4.6 步](/04-evaluate#s-4-6)。

- 检查结论（🟩 通过 / 🟨 缺可选项 / 🟧 缺 1 项必需 / 🟥 差距大）的读法见[扩展 2 第 E2.7 步](/90-extensions/e2-existing-logs#e2-7)。最常见的 ❌ 是数据还没到（等 2–3 分钟再查），或者账号没开 Transaction Search。
- 核对 `service.name` 是 `adlc-ws-local-agent`（实测那次用的是自定义名，这里按默认名写）。
- “span 来自受支持的框架埋点”“有 Agent 调用 span”“能取到用户输入内容”看的是框架自己的 span：上面是 Strands 的结果，其它框架以你们的检查结果为准（E3.16）。结论是 🟥 时，脚本会建议“回到主线”，那是针对实验的 mock agent；自带 Agent 按 ❌ 项和 E3.16 补埋点。
- 失败用例归因：`./44-failures.sh results/eval-<run_id>.json`，读法见[第 4.7 步](/04-evaluate#s-4-7)。

## E3.11 日志接入 ① 日志需要哪些字段 {#e3-11}

*验证状态：已实测*

转换脚本把每行日志变成一个 session / 一条 trace；缺必填字段的行不能导入。格式是 JSONL（UTF-8）：每次 Agent 调用一行。完整说明见 `telemetry/LOG_FORMAT.md`，样例见 `telemetry/samples/customer-agent-logs.jsonl`（扩展 2 用的就是它）。

| 字段 | 必填 | 说明 |
|---|---|---|
| `session_id` | 是 | 会话 ID，每条需求一个 |
| `timestamp` | 是 | 调用时间，ISO 8601（例如 `2026-10-15T09:30:00+08:00`）或毫秒时间戳 |
| `user_input` | 是 | 发给 Agent 的完整输入（需求反馈原文） |
| `agent_output` | 是 | Agent 的最终输出：最好就是 E3.1 的 JSON（文本或对象都可以）；不是的话标签准确率评估器记 `FormatError`，只剩内置评估器可用 |
| `feedback_id` | 建议 | 黄金集里的需求编号；缺失时从输出 JSON 的 `feedback_id` 取 |
| `model`、`latency_ms`、`agent_name` | 否 | 模型名、耗时、Agent 名 |
| `tool_calls` | 否 | `[{"name","input","output","status"}]`；没有时工具类评估器不可用 |

:::alert{header="字段名不一样怎么办" type="info"}
把一两行原始日志和 `telemetry/LOG_FORMAT.md` 一起交给 Claude Code，让它写一个把你们的日志转换成这种 JSONL 的脚本。给黄金集的每条需求各导出一行（每行带 `feedback_id`），才能和黄金集对上。日志里有真实客户数据时，先确认公司允许，只放在运行脚本的机器上，用完删除。
:::

## E3.12 日志接入 ② 校验字段，再转换并导入 CloudWatch {#e3-12}

*验证状态：已实测*

导入前发现缺字段的问题；然后把日志转成 OTel trace 发到 CloudWatch，并自动登记成一次运行：

```bash
./ext-convert-logs.sh check <你的日志.jsonl>
./ext-convert-logs.sh send  <你的日志.jsonl>
```

脚本 `ext-convert-logs.sh` 调用 `labkit/convert_logs.py`；`check` 只读文件，不发送任何数据。

- **check**：逐项列出字段覆盖率，以及“输出可解析为三项判断 JSON”的条数。出现 `✅ 字段检查通过` 才继续；如果后面还有一行 `⚠️ 不到一半的输出能解析成三项判断`，先调整 `agent_output` 的格式，否则标签准确率评估器会大量记 `FormatError`。有 `❌ 第 N 行缺少必填字段`、`不是合法 JSON` 或 `timestamp 无法解析` 时先修正：缺必填字段或不是合法 JSON 的行，`send` 会直接跳过。
- **send**：创建日志组 `/aws/bedrock-agentcore/adlc-ws-imported-logs`，以服务名 `adlc-ws-imported-logs` 发送；每行变成 `invoke_agent <agent_name>` → `chat`（每个工具调用再加一个 `execute_tool`）的 trace；输出能解析成三项判断 JSON 时，写在根 span 的 `adlc.triage_output` 上。
- **时间**：按原先后顺序重排到最近几分钟内（每条间隔 10 秒），因为 CloudWatch 不接收 14 天以前的 span；也不会早于日志组的创建时间（CloudWatch Logs Insights 查不到比日志组更早的记录），所以第一次发送时记录从日志组建好后几秒开始排。行数多时后面的 session 会排到接下来的几分钟里，`send` 结尾会多一行 `ℹ️` 提示要等多久（按代码核对，实测只跑了 5 行样例）。
- **session_id**：改写为 `adlc-ws-imp-<发送时间>-<原 session_id>`，同一文件重复发送也不会混在一起。

**预期输出**（`send` 的结尾；实测是样例日志 5 行，条数和时间以你看到的为准）：

```
✅ 已发送 5 个 session（run_id = imported-20261106T151120Z）。约 2-3 分钟后可在 CloudWatch 查到。
   下一步：./ext-check-telemetry.sh   （检查数据能否被 Evaluations 使用）
```

记下这一行的 run_id（`imported-<发送时间>`），后面都用它。

## E3.13 日志接入 ③ 遥测检查与按需评估 {#e3-13}

*验证状态：已实测*

`send` 之后等 2–3 分钟；`send` 结尾有 `ℹ️` 等待提示时，等到它说的时间之后再运行：

```bash
./ext-check-telemetry.sh --run imported-<发送时间>
./43-run-eval.sh --run imported-<发送时间>
```

- 导入的 trace 没有 token 用量；没有 `tool_calls` 的行也没有工具调用 span，所以检查结论通常是 🟨（必需项都满足，可以评估）。实测样例日志（第一次发送，日志组刚创建）：必需项 5/5，工具调用 2/5，token 用量 0/5，试评估成功（完整输出见[扩展 2 第 E2.6 步](/90-extensions/e2-existing-logs#e2-6)）。
- 导入的服务名不在 E3.6 的在线评估范围内：日志接入只做按需评估，控制台 **Evaluations** 页签不会有这些 session 的分数。
- 刚 `send` 完时，导入的就是最后一条登记的运行，不带 `--run` 也能评到它；但之后再跑别的运行就不是了，所以照样建议带上 `--run`。

## E3.14 日志接入 ④ 前后对比：一定要指定 --before / --after {#e3-14}

*验证状态：按代码核对*

导入的运行没有 V0 / V1 版本号，对比脚本没法自己配对：

```bash
./51-compare.sh --before imported-<优化前的发送时间> --after imported-<优化后的发送时间>
```

- 原因：导入的运行版本统一记为 `imported`。`./51-compare.sh` 不带参数时，“优化后”只从版本号形如 `v1`、`v1-ref`、`v2` 且不是导入的评估里挑，“优化前”找同一运行位置的 `v0`；都找不到时退回“最后两次评估”（`labkit/compare.py` 的 `pick_default()`）。所以不带参数时，可能拿 mock agent 的评估来配对，或者前后顺序不对。
- `--before` / `--after` 要写已经用 `./43-run-eval.sh` 评估过的 run_id（或 `results/eval-….json` 路径）。两边版本相同，对比页显示为“IMPORTED（前）”和“IMPORTED（后）”。
- 埋点接入手动登记的运行同理：`./51-compare.sh --before own-v0-… --after own-v1-…`。
- 对比页生成后会上传到你的私有 S3 桶并打印控制台链接（和[第 5.5 步](/05-optimize#s-5-5)一样）。

## E3.15 优化与再评估（两种方式通用） {#e3-15}

*验证状态：按代码核对*

按失败用例改你们的 Agent，再用同一份黄金集、同一组评估器证明改进有效。

**① 先做基线（优化前）**：用现在的 Agent 处理黄金集的全部需求，按埋点接入（E3.7 → E3.9 → E3.10，`version` 写 `v0`）或日志接入（E3.12 → E3.13）得到一次评估，记下 run_id。

**② 找改哪里**：`./44-failures.sh results/eval-<基线 run_id>.json`（读法见[第 4.7 步](/04-evaluate#s-4-7)），再按[第 5.1 步](/05-optimize#s-5-1)的方法，把 mock agent 里“改哪里”换成你们 Agent 里对应的地方：

| mock agent 里改的地方 | 你们 Agent 里对应的地方 |
|---|---|
| 提示词（`agent/adlc_agent/prompts/` 下的判断口径、红线、话术要求） | 系统提示词 |
| 工具输出 / 新增工具（`agent/adlc_agent/tools.py`） | 工具或函数：能力查询要返回支持状态、限制、替代方案；考虑加一个“查相似需求”的工具 |
| 数据（`data/capability_catalog.json`） | 知识库或能力数据源 |
| 模型（`AGENT_MODEL_ID`） | 模型配置 |

**③ 让 Claude Code 帮忙改**：思路和[第 5.2 步](/05-optimize#s-5-2)一样，只是对象换成你们的 Agent。可以从下面这段提示词开始，把尖括号里的内容换成你们的：

```
1. 运行 ./44-failures.sh results/eval-<基线 run_id>.json，并读取这个文件：先列出红线用例：label_accuracy 为 CriticalFail 的，以及 reject_quality 的 explanation 里有“红线：”的；再列出 label_accuracy 不是 Pass 的用例（feedback_id、期望 vs 实际、explanation），以及 reject_quality 不是 Pass 的用例。
2. 对每个失败用例，到 data/golden/golden_set.jsonl 里找同一 feedback_id 的 rationale（标注理由）和 dimensions（五维），归纳出 3-5 条主要失败原因。逐条判断：Agent 拿到了足够的信息和规则却没用对，或者工具给它的信息不够 → 修 Agent，这一轮改；Agent 按它拿到的信息做得没错，是黄金集标注或判据本身有问题 → 修评估体系，这一轮不改，只记下来。
3. 阅读 <你们的系统提示词文件>、<你们的工具代码> 和 data/golden/LABELING_GUIDE.md（标注口径以它为准）。
4. 在现有系统提示词的基础上写出新版本，另存为新文件；针对第 2 步“修 Agent”的原因修改判断口径、红线和话术要求；保持输出 JSON 格式完全不变（字段：feedback_id, important, urgent, decision, level, reasoning, reject_reason, customer_reply）。
5. 不要修改 data/golden/、evaluators/、labkit/ 下的任何文件，也不要修改原来的系统提示词文件。
6. 把改动说明写进 <你们的目录>/changes.md：第一行写“依据 results/eval-<基线 run_id>.json”；再用一两句话说明评估发现了什么；然后写一张 Markdown 表格，表头为 | 失败原因（评估发现） | 修改（文件） | 预期影响的指标 |，每个“修 Agent”的原因一行；有“修评估体系”的原因时，最后另起一节逐条列出；最后在终端用中文输出同样的总结。
```

改动说明写在你们自己的目录里即可（对比页只显示 mock agent 的改动说明）。把你们的代码放到运行 Claude Code 的机器上之前，先确认公司允许，只放需要的文件，用完删除。

**④ 不要改黄金集和评估器**，只改你们的 Agent，否则前后分数不可比。

**⑤ 发布新版本**：按你们平时的方式发布到测试 / 预发环境（不要直接上生产）。`./50-switch-version.sh` 和 `./30-deploy-runtime.sh` 只作用于 mock agent，与你们的 Agent 无关。

**⑥ 再评估**：同样的需求再跑一次。埋点接入登记时 `version` 写 `v1`；日志接入再导出、`check`、`send`。然后 `./43-run-eval.sh --run <新 run_id>`。

**⑦ 对比**：`./51-compare.sh --before <基线 run_id> --after <新 run_id>`（E3.14）。埋点接入开了在线评估时，也可以在控制台里把时间范围分别设成两次运行的时间段，对照平均分。

## E3.16 已知限制 {#e3-16}

这些情况会让评估查不到数据，或给出误导性的结果，接入前先看一遍。

| 情况 | 影响与对策 | 依据 |
|---|---|---|
| 没有加 E3.5 代码的 Agent（例如直接用 LangGraph 的默认埋点） | 按需评估和遥测检查只认 `adlc.triage` span，或 `gen_ai.operation.name = invoke_agent` / 名字以 `invoke_agent` 开头的 span（`labkit/spans.py`）。框架没有产生这种根 span 时，即使 span 已经进了 CloudWatch，`./43-run-eval.sh` 也会报 `no spans found`，`./ext-check-telemetry.sh` 等待后在“有 Agent 调用 span”上打 ❌；E3.9 的登记查询也按 `adlc.triage` 找 session。对策是加 E3.5 的代码。LangGraph 还要写 `adlc.triage_output`，否则评估器可能解析不了它序列化的图状态 | 按代码核对 |
| 不用 Strands / LangChain / OpenInference / CrewAI 的 Agent（例如直接调用模型 API 的 Python 程序） | E3.5 的代码只补上 session、根 span 和最终输出；没有框架的 GenAI span 时，`./ext-check-telemetry.sh` 在“受支持的框架埋点”“有 Agent 调用 span”“能取到用户输入内容”上打 ❌，结论 🟥，不做试评估。按需评估里我们的自定义评估器（标签准确率、话术质量）仍可从 `adlc.triage_output` 读输出；内置评估器（Helpfulness、Correctness）需要输入输出内容，可能报错。对策：用受支持框架的埋点包，或按 OTel GenAI 语义约定自己补 `invoke_agent` span 和输入输出事件 | 按代码核对 |
| 自定义服务名 / 日志组 | 在线评估一条都不打分（实测 0/5）；在 `config/local.env` 或环境变量里设 `LOCAL_SERVICE_NAME` / `LOCAL_LOG_GROUP` 也无效。用 E3.4 的默认名 | 已实测 |
| 改了在线评估配置之后 | 不会给改配置之前产生的 session 补打分（实测：改配置后新跑的 3/3 被打分，之前的 0/5 一直没有分数）。改完要重新跑一遍 Agent | 已实测 |
| 日志组不存在时开启在线评估 | `./41-online-eval.sh enable --mode local` 会先打印 7 行 `⏳ 等待 IAM 权限生效后重试`（不是权限问题），最后报 `ValidationException … One or more specified log groups do not exist`。先运行 E3.6 的 `.venv/bin/python -m labkit.loggroup` | 已实测 |
| 不带 `--run` 运行 `./43-run-eval.sh` / `./ext-check-telemetry.sh` | 评估或检查的是最后一条登记的运行，可能是 mock agent 那次，并显示它的结论（例如 ✅ 通过），见 E3.8 | 已实测 |
| 日志接入 | trace 是按日志合成的，时间重排到导入时刻，没有 token 用量；不进在线评估；版本统一为 `imported`，对比必须指定 `--before` / `--after` | 按代码核对 |
| 一个 trace 里处理多条需求（批量模式） | 评估器能解析 `{"results": […]}`，但控制台的“平均分 = 准确率”只在一条需求一个 trace 时成立（`evaluators/README.md` 第 1 节）。按 E3.5 一条需求一个 session | 按代码核对 |
| Agent 在线程池 / 子进程里干活 | 子线程、子进程里的 span 可能拿不到 `session.id`，一条需求的 trace 会断开。在同一线程里调用 Agent，或显式传递 OTel context | 未验证 |
| 清理 | `./99-cleanup.sh` 按前缀删除实验资源：日志组 `/aws/bedrock-agentcore/adlc-ws-…`（包括 E3.6 的日志组）、在线评估配置和评估器 `adlc_ws_…`；你们 Agent 自己的资源不受影响。用了不带这个前缀的自定义日志组时，清理脚本会漏删它 | 按代码核对 |

## 检查一下 {#e3-check}

**你们的 Agent 优化前后两次评估，用的是不是同一份黄金集、同一组评估器、同一批输入？如果改 Agent 的同时也改了黄金集或评估器，对比页上的分数变化还能说明 Agent 变好了吗？**（对照 E3.15 的第 ④ 条想一想。）
