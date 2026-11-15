---
title: "1 进入实验环境"
weight: 10
---

> **你在这里**：准备 · 还没进入飞轮（① 定义 → ② 构建 → ③ 评估 → ④ 发布 → ⑤ 观测 → ⑥ 回流 · 圆心：黄金标准）

**用时**：约 5 分钟

实验环境已经由 CloudFormation 准备好：一台只能用 Session Manager 登录的 EC2，实验材料在 `~/adlc-lab`，CloudWatch Transaction Search 已开启。这一章只做三件事：登录、切换用户、检查环境。

## 1.1 打开控制台，找到登录链接 {#s-1-1}

1. 在 Workshop Studio 的活动页面点 **Open AWS console**（打开 AWS 控制台），确认右上角的区域是讲师指定的区域（默认 **美国西部（俄勒冈）us-west-2**）。后面所有控制台操作都在这个区域里做；看不到自己的资源时，先检查右上角的区域。
2. 回到活动页面，在页面下方的 **Event Outputs** 里找到 `SessionManagerUrl`（登录链接）和 `FirstCommand`（登录后的第一条命令）。

:image[实验堆栈 adlc-lab 的输出（共 6 项，截图里是前 5 项）：FirstCommand、InstanceId、LabDirectory、RoleName、SessionManagerUrl]{src="/static/images/01-event-outputs.png" width=900}

*图 1-1 实验堆栈的输出：SessionManagerUrl 就是登录链接（活动页面的 Event Outputs 里是同样的内容）*

## 1.2 用 Session Manager 登录，切换到实验用户 {#s-1-2}

::::expand{header="从 EC2 控制台连接（不用登录链接时）"}
:image[EC2 控制台的实例列表：选中实验实例 adlc-ws，右上角的 Connect 按钮被红框标出]{src="/static/images/ec2-console-connect.png" width=900}

*图 1-2 EC2 → 实例：选中 adlc-ws，点 Connect*

:image[Connect to instance 页面：选中 SSM Session Manager，Ping status 为 Online，右下角 Connect 按钮]{src="/static/images/ec2-console-SSM.png" width=900}

*图 1-3 选 SSM Session Manager，再点右下角 Connect*
::::

1. 在已登录控制台的浏览器里打开 `SessionManagerUrl`，点 **Connect**（连接）。也可以在 **EC2** → **实例** 里选中这台实验实例 → **连接** → **Session Manager** → **连接**（见上面的展开块）。
2. 浏览器新标签页里出现命令行后，你的身份是 `ssm-user`。先切换到实验用户 `ec2-user`：

```bash
sudo su - ec2-user
```

**预期输出**（自动进入 `~/adlc-lab`，Python 虚拟环境已激活）：

```
Last login: Sat Nov 14 13:38:53 UTC 2026
(.venv) [ec2-user@ip-10-12-0-245 adlc-lab]$
```

:::alert{header="以后每次重新连接" type="warning"}
Session Manager 的会话空闲一段时间会断开。重新连接后，**第一件事**都是 `sudo su - ec2-user`；否则你在 `ssm-user` 下，找不到实验目录，脚本也会报错。脚本跑到一半断开了（例如跑黄金集或评估时），重连后重跑同一条命令即可：没跑完的运行不会被登记，评估用的总是最后一次跑完的运行。
:::

## 1.3 检查环境 {#s-1-3}

```bash
./10-check-env.sh
```

它依次检查 Python 依赖、配置与身份、Bedrock 模型能否调通、Transaction Search 是否开启。

**预期输出**：

```
① Python 与依赖
  ✅ Python 3.12.16
  ✅ strands-agents 1.65.0
  ✅ bedrock-agentcore 1.26.0
  ✅ aws-opentelemetry-distro 0.22.0
  ✅ boto3 1.44.3
② 配置与身份
  ✅ 区域 us-west-2 · 个人后缀 ws · 资源名前缀 adlc-ws
  ✅ 身份 arn:aws:sts::<account>:assumed-role/adlc-ec2-ws-us-west-2/i-004e…
③ Bedrock 模型
  ✅ Agent 模型 us.anthropic.claude-sonnet-5-5 可用（回复：收到）
④ CloudWatch Transaction Search
  ✅ 已开启（trace 写入 CloudWatch Logs 的 aws/spans）

✅ 环境检查通过。下一步：手册第 2 章（定义：什么是好）
```

::::expand{header="③ 显示模型不可用（AccessDeniedException）怎么办"}
账号还没开通 Claude 模型时，一行命令切换到 Amazon Nova，再重跑检查：

```bash
./tools/use-model.sh nova && ./10-check-env.sh
```

切回 Claude：`./tools/use-model.sh claude`。
::::

## 1.4 确认 Claude Code 能用 {#s-1-4}

第 2 章和第 5 章要用 Claude Code（已经装好，通过 EC2 的实例角色调用 Amazon Bedrock，不需要 API Key）。先确认它能连上：

```bash
claude -p "只回复两个字：收到"
```

**预期输出**：

```
收到
```

需要交互时，在 `~/adlc-lab` 里直接运行 `claude`；退出输入 `/exit`。这条命令报错或长时间没有回应时，请找讲师：1.3 的 `./tools/use-model.sh` 只切换分诊 Agent 和评审用的模型，不影响 Claude Code。

:::alert{header="实验环境里能用的模型" type="info"}
实验账号只开放实验要用的三个 Bedrock 模型：Claude Code 和 Agent 用 **Claude Sonnet 5.5**（Claude Code 的快速模型是 Claude Haiku 5.5），备选是 1.3 里 `./tools/use-model.sh nova` 切换的 Amazon Nova 2 Lite；其它模型调用不了。实验账号也不允许创建 IAM 用户或访问密钥：EC2 上的所有 AWS 调用都用实例角色，不需要配置任何密钥。
:::

## 检查一下 {#s-1-check}

**这一章还没有进入飞轮，但先看一眼圆心：** 实验环境里现在的黄金集是谁定的、有多少条？（第 2.1 步会用 `python3 data/tools/golden_tool.py stats` 看到它；第 2 章结束时，它要变成**你们**签过字、冻结过的那一版。）
