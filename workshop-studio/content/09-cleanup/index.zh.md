---
title: "9 清理"
weight: 90
---

> **你在这里**：收尾（① 定义 → ② 构建 → ③ 评估 → ④ 发布 → ⑤ 观测 → ⑥ 回流 · 圆心：黄金标准）

**用时**：约 5 分钟

Workshop Studio 的活动账号在活动结束后会被整体回收。为了不让在线评估、Runtime 这些资源在活动剩余的时间里继续产生调用，做完实验后先把你创建的实验资源删掉。脚本只删除带你个人前缀 `adlc-ws-` / `adlc_ws_` 的资源。

:::alert{header="想保留素材？" type="warning"}
先截好对比页、分诊报告和控制台的图，再清理：清理会删除你的私有 S3 桶，里面的网页也会一起删除。
:::

## 9.1 预览并运行清理 {#s-9-1}

先预览会删什么（不会真正删除）：

```bash
./99-cleanup.sh --dry-run
```

**预期输出**（节选；预览模式的最后会把这些资源列为“残留”并提示仍有资源，这是正常的）：

```
▶ 清理范围：我的资源 adlc-ws-* · 区域 us-west-2
  [dry-run] 停用在线评估 adlc_ws_online_runtime
  [dry-run] 🗑  删除在线评估 adlc_ws_online_runtime
  [dry-run] 删除评估器 adlc_ws_label_accuracy
  [dry-run] 删除 Lambda adlc-ws-eval-label-urgent
  [dry-run] 删除 Runtime adlc_ws_agent (adlc_ws_agent-VL2KJhC5Tp)
  [dry-run] 清空并删除 S3 桶 adlc-ws-<account>-us-west-2
  [dry-run] 删除 IAM 角色 adlc-ws-runtime-role-us-west-2
  …
```

确认无误后执行：

```bash
./99-cleanup.sh
```

按依赖顺序删除：在线评估 → 评估器 → Lambda → Runtime → S3 桶 → 日志组 → IAM 角色；可以重复运行。

**预期输出**（节选）：

```
▶ 清理范围：我的资源 adlc-ws-* · 区域 us-west-2
  🗑  停用在线评估 adlc_ws_online_runtime
  🗑  删除在线评估 adlc_ws_online_runtime
  🗑  删除评估器 adlc_ws_label_accuracy
  🗑  删除评估器 adlc_ws_label_decision
  …
  🗑  删除 Runtime adlc_ws_agent (adlc_ws_agent-VL2KJhC5Tp)
  🗑  清空并删除 S3 桶 adlc-ws-<account>-us-west-2
  🗑  删除 IAM 角色 adlc-ws-runtime-role-us-west-2

▶ 残留检查（us-west-2，前缀 adlc_ws_* / adlc-ws-*）
  ✅ 在线评估配置: 0
  ✅ 自定义评估器: 0
  ✅ AgentCore Runtime: 0
  ✅ Lambda 函数: 0
  ✅ S3 桶: 0
  ✅ IAM 角色: 0
  ✅ 日志组: 0
✅ 没有残留的实验资源
```

## 9.2 确认没有残留 {#s-9-2}

清理脚本最后会自动检查一遍；之后想再查一次：

```bash
./99-cleanup.sh --verify-only
```

实验 EC2 本身（CloudFormation 堆栈 `adlc-lab`）由 Workshop Studio 管理，活动结束时随账号一起回收，你不需要、也没有权限删除它。Runtime 删除需要 1–2 分钟，偶尔第一次检查还能看到它，稍后再查即可。看到“有 N 类资源无法列出（权限不足）”时，结论不完整，请告诉讲师。

## 检查一下：清理之后，圆心还在吗？ {#s-9-check}

**清理删掉了 Runtime、评估器和 S3 桶，那这次实验定下的黄金标准还在哪里？** 冻结的黄金集、判据表和鱼骨图都在实验 EC2 的 `data/golden/` 和 `outputs/fishbone/` 里，活动结束时会随账号回收；需要带走的，在清理前从 S3 控制台下载预览页和 `fishbone.drawio`。
