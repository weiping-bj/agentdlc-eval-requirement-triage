---
title: "2.5 讨论：如果业务方要 P0/P1/P2 分级"
weight: 50
---

> **你在这里**：**① 定义** → ② 构建 → ③ 评估 → ④ 发布 → ⑤ 观测 → ⑥ 回流 · 圆心：黄金标准

**用时**：约 5 分钟（小组讨论，不用敲命令）

## 讨论题 {#s-2-5-question}

**如果业务方说：“是 / 否太粗了，我们要 P0 / P1 / P2 分级。”哪些东西要跟着变？**

先自己想 2 分钟，再对照下面的影响链。

## 影响链 {#s-2-5-chain}

改一个标准，下游每一环都要跟着动；漏掉任何一环，分数就会悄悄失真：

| 环节 | 要变什么 | 本实验里对应的文件或命令 |
|---|---|---|
| **标准** | 三项标签换成“优先级 + 处理建议”；P0/P1/P2 各自的口径；“怎么算对”（完全一致，还是相差一级也算对）；70% 目标要重新定义 | `data/golden/LABELING_GUIDE.md` 第 6 节；`config/local.env` 的 `LABEL_SCHEME`、`LEVEL_TOLERANCE` |
| **黄金集** | 每条样本都要有期望优先级，重新校验、冻结 | `golden_tool.py fill-levels`、`golden_tool.py --scheme level freeze` |
| **评估器** | 标签评估器按分级口径打分，多一个“优先级”单项评估器 | 评估器 Lambda 的环境变量 `ADLC_LABEL_SCHEME`，新增 `label_level` |
| **提示词** | Agent 必须输出 `level` 字段（现在是 `null`） | `agent/adlc_agent/prompts/level-scheme.md`，Runtime 环境变量 `ADLC_LABEL_SCHEME` |
| **门禁** | 发布门禁多一项“优先级准确率”，在线评估也要挂上新的评估器 | 三大支柱的门禁；在线评估配置 |

这也是一个“修评估体系”的例子：尺子换了，切换前后的分数不能直接比，要在新口径下重新跑一次 V0 基线。

本实验把整条链做成了**一条命令**（`./ext-switch-label-scheme.sh level`）。主线先保持“是 / 否”；想亲手试一次，见扩展[标签方案切换](/90-extensions/e5-label-scheme)。

## 检查一下：定义环有没有对齐黄金标准？ {#s-2-check}

**定义环的产出就是圆心。** 现在你能指出这三样东西吗：业务方签过字的判据表（`criteria.md`）、每条红线判据都有能检验它的黄金样本、一个冻结过的黄金集版本（`data/golden/MANIFEST.json` 里的 sha256）？少了任何一样，后面每一环的分数都没有参照物。

下一章：[3 构建：让 Agent 可评估](/03-build)
