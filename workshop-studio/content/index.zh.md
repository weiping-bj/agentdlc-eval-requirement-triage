---
title: "AgentDLC 动手实验：需求价值打分 Agent"
weight: 0
---

> 占位页：完整操作手册正在迁移到 Workshop Studio，迁移完成前以讲师提供的手册为准。

本实验用 Amazon Bedrock AgentCore Runtime 与 AgentCore Evaluations 评估并优化一个需求分诊 Agent（所有数据均为合成数据）。

## 你的实验环境（已自动准备好）

- 一台实验 EC2（Amazon Linux 2023），只能通过 SSM Session Manager 登录，不开放任何入站端口。
- 实验代码已解压到 `/home/ec2-user/adlc-lab`，工具（uv + Python 3.12、Node.js、Claude Code、AWS CLI）和 `.venv` 已安装好。
- 本账号本区域的 CloudWatch Transaction Search 已开启（环境准备的必需步骤；`./01-check-env.sh` 会显示状态）。

## 登录实验 EC2

1. 在本页的事件信息中找到 **SessionManagerUrl**，在已登录的 AWS 控制台中打开它，点击 **Connect / 连接**。
2. 命令行就绪后，先切换到实验用户（会自动进入实验目录并激活 `.venv`）：

   ```bash
   sudo su - ec2-user
   ```

3. 检查环境：

   ```bash
   ./01-check-env.sh
   ```

> 以后每次重新连接，都先执行 `sudo su - ec2-user`。
