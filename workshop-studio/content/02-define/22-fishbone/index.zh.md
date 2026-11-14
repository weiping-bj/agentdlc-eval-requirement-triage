---
title: "2.2 画鱼骨图：什么会挡住上线"
weight: 20
---

> **你在这里**：**① 定义** → ② 构建 → ③ 评估 → ④ 发布 → ⑤ 观测 → ⑥ 回流 · 圆心：黄金标准

**用时**：约 15 分钟

业务方说的“好”，通常是好几类诉求混在一起。鱼骨图把它拆到五个方面（认知、质量、责任、成本、性能，再加一个“其他”），只记录**业务方自己确认过的障碍**，不写方案。这一步由 Claude Code 当主持人（`adlc-fishbone` skill，快速版），你们按角色卡扮演业务方。

## 开始访谈 {#s-2-2-start}

每人在自己的 EC2 上做；同组的同学可以围在一起商量怎么回答。在 `~/adlc-lab` 里启动 Claude Code：

```bash
claude
```

进入后输入（把组名换成你们的）：

```
用 adlc-fishbone 画鱼骨图：快速版，实验场景，我们是第 3 组
```

Claude Code 第一次运行命令前会问你是否允许；这一步只会在 `outputs/fishbone/` 下建目录、生成文件，选允许即可。

:image[Claude Code 里 adlc-fishbone 的开场：主持人说明规则，只问一个问题（组名），并确认用快速版和实验场景]{src="/static/images/02-fishbone-start.png" width=900}
*图 2-1 鱼骨图访谈的开场：一次只问一个问题*

## 怎么回答 {#s-2-2-answer}

- **一次只答一个问题**。主持人会把你们的原话改写成便签读给你们听：准确就说“对”，不准确就指出哪里不对。
- **只用角色卡里的事实**。卡里没写的，说“不知道”或“没有”；不要现编数字。
- **不谈方案**。被问到“怎么解决”时可以说“这个先不谈”；你们提了方案（比如“加个人工复核就行”），主持人会把它放进待讨论清单，再追问“没有这个办法时，最容易出什么错”。
- 快速版先问红线：**责任 → 质量 → 认知 → 成本 → 性能 → 其他**。每个方面开门问一次，最多追问一次。

每条障碍只放在一个方面，按障碍本身归类，而不是按它是在哪个问题下说出来的。最容易放错的两种：

| 障碍 | 归到 | 为什么 |
|---|---|---|
| “一条反馈写了四个诉求，常常漏掉后面几个” | **质量** | 输出不完整（遗漏），哪怕是在问速度时说的 |
| “整批跑到一半超时，会上只拿到半份清单” | **性能** | 没跑完：延迟、吞吐、稳定性才是性能 |

主持人会在最后读回时按这个规则核对，挪过方面的便签会用一句业务话说明。

## 出图与预览 {#s-2-2-render}

全部方面问完后，主持人会把所有便签一次读回，问“可以按这个出图吗？”。你们明确说“可以”之后，它会：

1. 写 `outputs/fishbone/<组名>-<日期>/fishbone-data.json`（以它为准）和 `qa-record.md`（脱敏后的问答摘要）；
2. 用 `.claude/skills/adlc-fishbone/scripts/generate_fishbone.py` 生成 `fishbone.drawio` 和 `validation-report.txt`（13 项检查全部 PASS 才算完成）；
3. 运行 `./tools/publish-define-pages.sh outputs/fishbone/<目录>`，把预览网页上传到你的私有 S3 桶，并打印控制台链接。

在已登录控制台的笔记本浏览器里打开打印的链接，点右上角 **打开**，就能看到你们的鱼骨图。

:image[S3 控制台里打开的鱼骨图预览：五个方面加“其他”，每张便签都是业务方确认过的障碍]{src="/static/images/02-fishbone-preview.png" width=900}
*图 2-2 鱼骨图预览（网页在你的私有桶里，只有登录这个账号控制台的人能打开）*

**预期输出**（发布命令的结尾，目录名和链接是你自己的）：

```
  ✅ 鱼骨图预览 → outputs/fishbone/第3组-2026-11-14/fishbone.html
  ✅ 鱼骨图 drawio → outputs/fishbone/第3组-2026-11-14/fishbone.drawio
  🪣 已创建 S3 桶 adlc-ws-<account>-us-west-2
☁️  已上传到私有桶 s3://adlc-ws-<account>-us-west-2/reports/define/第3组-2026-11-14/fishbone.html
…
在笔记本浏览器打开（需已登录这个实验账号的控制台）：网页点右上角「打开」；drawio 点「下载」，再用 app.diagrams.net 打开（桶是私有的，链接不能给组外的人用）：
  鱼骨图预览       https://us-west-2.console.aws.amazon.com/s3/object/adlc-ws-<account>-us-west-2?region=us-west-2&prefix=reports/define/%E7%AC%AC3%E7%BB%84-2026-11-14/fishbone.html
  鱼骨图 drawio  https://us-west-2.console.aws.amazon.com/s3/object/adlc-ws-<account>-us-west-2?region=us-west-2&prefix=reports/define/%E7%AC%AC3%E7%BB%84-2026-11-14/fishbone.drawio
```

::::expand{header="时间不够、或者想自己改一条便签"}
- 访谈中途断了：直接说“继续”，主持人会接着问；每做完一个方面它都会把摘要写进 `qa-record.md`。
- 不要手改 `fishbone.drawio`。要改便签，告诉 Claude Code 改哪条，它会改 `fishbone-data.json`、读回确认后重新生成。
- 想用你们自己组的 Agent 场景：见扩展[用自己的场景画鱼骨图](/90-extensions/e4-own-fishbone)，主线先用实验场景。
::::

下一步：[2.3 障碍 → 判据表](/02-define/23-criteria)
