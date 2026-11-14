---
title: "3 构建：让 Agent 可评估"
weight: 30
---

> **你在这里**：① 定义 → **② 构建** → ③ 评估 → ④ 发布 → ⑤ 观测 → ⑥ 回流 · 圆心：黄金标准

:image[你在这里：② 构建]{src="/static/images/flywheel-build.svg" width=220 height=220}

**用时**：约 15 分钟

构建环**遵循圆心**：搭出来的 Agent 不只要能跑，还要**能被评估**。也就是说，第 2 章判据要检查的每一个东西（三项标签、拒绝理由、客户话术），都必须能在 Agent 的 trace 里找到。这一章把 V0 部署到 AgentCore Runtime，调用一次，然后在控制台里亲眼看到这些字段。

## 3.1 认识 Agent，以及为什么 trace 里要带着结论 {#s-3-1}

| 文件 | 作用 |
|---|---|
| `agent/main.py` | AgentCore Runtime 入口：收到 `{"feedback": {...}}`，返回判断结果 JSON |
| `agent/adlc_agent/core.py` | 用 Strands Agents 构建 Agent（模型、提示词、工具），解析输出，并把结论写进 trace |
| `agent/adlc_agent/prompts/v0.md` | V0 的系统提示词（第 2.1 步看过） |
| `agent/adlc_agent/tools.py` | 工具：V0 只有 `lookup_capability`；V1 参考版有 `search_capabilities` + `find_similar_requests` |

`core.py` 的 `triage()` 在每次调用外面包一层名为 `adlc.triage` 的 span，并在上面写三个属性：

- `adlc.feedback_id`：这是哪一条需求（评估器用它在黄金集里找标准答案）；
- `adlc.agent_version`：哪个版本的 Agent 给出的结论；
- `adlc.triage_output`：**最终的判断 JSON**（重要 / 紧急 / 建议 / 理由 / 话术）。

为什么要专门放一个 span：评估器读的是 CloudWatch 里的 trace，不是 Agent 的返回值。模型的原始消息可能很长、可能被截断，也可能根本不记录；把最终结论放在一个固定名字、固定属性的 span 上，评估器就永远知道去哪里取。一条需求 = 一个 session = 一个 trace，这是“可评估”的最小约定。

## 3.2 部署 V0 到 AgentCore Runtime {#s-3-2}

AgentCore Runtime 支持直接部署代码包，不需要 Docker。一条命令完成打包、上传、建 Runtime 并等待就绪：

```bash
./30-deploy-runtime.sh
```

**预期输出**（约 1–2 分钟；Runtime ID 末尾的随机字符每个人不同）：

```
▶ 部署 adlc_ws_agent · Agent 版本 v0 · 模型 us.anthropic.claude-sonnet-5-5 · 区域 us-west-2
  ⬇️  下载 linux/arm64 依赖（Python 3.12，首次约 1-2 分钟）…
  📦 打包完成：build/runtime/agent-20261114T134335Z.zip（5564 个文件，39.8 MB）
  ⬆️  已上传 s3://adlc-ws-<account>-us-west-2/runtime/agent-20261114T134335Z.zip
  🔐 执行角色 adlc-ws-runtime-role-us-west-2
  🆕 创建 Runtime adlc_ws_agent-VL2KJhC5Tp
  ⏳ Runtime 状态 CREATING
  ⏳ Runtime 状态 READY
✅ Runtime 就绪：adlc_ws_agent-VL2KJhC5Tp（版本 1，Agent v0）
   下一步：./30-deploy-runtime.sh invoke   （调用一次确认可用）
```

::::expand{header="脚本做了哪些事"}
1. 用 `uv` 下载 linux/arm64 版依赖（Runtime 运行在 ARM 上），和 `agent/` 代码、能力目录、上个月的需求反馈一起打成 zip（第 2.4 步改过数据的话，新数据就在这里被打进包）；
2. 上传到你的私有桶 `s3://adlc-ws-<账号>-<区域>/runtime/`；
3. 使用执行角色 `adlc-ws-runtime-role-<区域>`（调用 Bedrock 模型、写 CloudWatch）：它和评估器、在线评估用的角色一样，由 CloudFormation 在准备环境时预先创建，实验 EC2 本身不能创建或修改 IAM 角色；
4. 创建 Runtime `adlc_ws_agent`，入口是 `opentelemetry-instrument main.py`（自动埋点），环境变量 `AGENT_VERSION=v0`，等待状态变成 READY。

以后重新部署（第 5 章）会复用已下载的依赖，并更新同一个 Runtime，版本号加 1。
::::

:::alert{header="如果显示 CREATE_FAILED" type="warning"}
看后面的原因。提到 “binary files that are incompatible with Linux ARM64” 时，删除 `build/runtime/` 后重跑；Runtime 自己的日志在 CloudWatch Logs 日志组 `/aws/bedrock-agentcore/runtimes/<Runtime ID>-DEFAULT`。
:::

## 3.3 调用一次 {#s-3-3}

```bash
./30-deploy-runtime.sh invoke
```

**预期输出**（大模型每次的措辞不同；第一次调用有冷启动，超时就再执行一次）：

```
需求 FB-2610-001：列表页批量修改自定义字段
{
  "feedback_id": "FB-2610-001",
  "important": "是",
  "urgent": "否",
  "decision": "待评估",
  "level": null,
  "reasoning": "客户等级为战略客户，所以判为重要。……能力查询只返回功能名称，看不到批量编辑具体支持哪些字段类型。……",
  "reject_reason": "",
  "customer_reply": ""
}
✅ Runtime 调用成功，用时 12.9s，session = adlc-ws-smoke-13ea8dafe81d408b88e9dd4f6ceed285
```

注意 V0 理由里的“能力查询只返回功能名称”：这正是 V0 工具的缺陷。它看不到“批量编辑只支持标准字段”，只好判“待评估”，而黄金集期望的是“迭代”。第 4 章会把这类错误统计出来。

想换一条需求：`./30-deploy-runtime.sh invoke --one FB-2610-002`。查看部署信息：`./30-deploy-runtime.sh status`。

## 3.4 在控制台看 Runtime 和 trace {#s-3-4}

**Runtime**：控制台顶部搜索 `Bedrock AgentCore` → 左侧 **Runtime** → 列表里的 `adlc_ws_agent`，状态 Ready；点进去能看到版本号、**DEFAULT** 端点、执行角色，以及环境变量 `AGENT_VERSION=v0`。

:image[Amazon Bedrock AgentCore 控制台的 Runtime 详情页：adlc_ws_agent 状态 Ready，版本号，DEFAULT 端点和环境变量]{src="/static/images/03-runtime-console.png" width=900}

*图 3-1 AgentCore Runtime 详情页*

**trace**：调用后等 2–3 分钟，打开 **CloudWatch** → 左侧 **GenAI Observability** → **Bedrock AgentCore** → **Agents** 里的 `adlc_ws_agent` → **Sessions** → 刚才那个 `adlc-ws-smoke-…` 会话 → **Traces**。调用树里有 `adlc.triage` → `invoke_agent` → `chat` 和 `execute_tool lookup_capability`。点 `adlc.triage`，在属性里找到 `adlc.triage_output`：就是 3.3 里看到的那段 JSON。

:image[CloudWatch GenAI Observability 里一个 session 的 trace 调用树，选中 adlc.triage span，属性里能看到 adlc.triage_output]{src="/static/images/03-trace-triage-span.png" width=900}

*图 3-2 trace 调用树：结论写在 adlc.triage 这个 span 上*

Runtime 上的服务名是 `adlc_ws_agent.DEFAULT`；在线评估和指标都按这个名字聚合。控制台菜单名称可能随版本略有不同。

## 检查一下：构建环有没有对齐黄金标准？ {#s-3-check}

**第 2 章的判据要检查“重要 / 紧急 / 建议 / 拒绝话术”。** 你能在 3.4 的 trace 里指出每一项在哪个 span 的哪个属性上吗？如果有一项在 trace 里找不到，评估器就无从打分——那就是构建环还没完成。

下一章：[4 评估：V0 基线](/04-evaluate)
