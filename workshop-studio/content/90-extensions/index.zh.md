---
title: "扩展（可选）"
weight: 100
---

主线第 0–8 章做完、**第 9 章清理之前**还有时间时做（扩展要用到已部署的 Runtime 和已注册的评估器）。每个扩展都是独立的，挑和你最相关的做；扩展里的命令同样在实验 EC2 的 `~/adlc-lab` 里运行。

| 扩展 | 做什么 | 用时 |
|---|---|---|
| [1 自托管 Agent 埋点接入](/90-extensions/e1-self-hosted) | 同一个 Agent 不放在 Runtime 上、直接在 EC2 上运行，用 OTel + ADOT 埋点后接入同一套评估 | 约 40 分钟 |
| [2 已有日志接入](/90-extensions/e2-existing-logs) | 没法改代码的 Agent：把已有的调用日志转换成 span 导入 CloudWatch，再检查能不能评估 | 约 20 分钟 |
| [3 自带 Agent 接入指南](/90-extensions/e3-own-agent) | 会后把你们自己的 Agent 接入同一套评估（参考文档） | 会后阅读 |
| [4 用自己的场景画鱼骨图](/90-extensions/e4-own-fishbone) | 用你们自己组的 Agent 场景重做定义环 | 约 30 分钟 |
| [5 标签方案切换](/90-extensions/e5-label-scheme) | 一条命令把“是 / 否”切换成 P0/P1/P2，看影响链上每一环怎么变 | 约 20 分钟 |
| [6 按讨论意见重新生成模拟数据](/90-extensions/e6-mock-data) | 按讨论模板调整 60 条模拟需求和黄金集 | 约 20 分钟 |

::children
