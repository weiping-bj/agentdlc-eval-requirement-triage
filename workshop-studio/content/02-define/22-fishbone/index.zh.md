---
title: "2.2 画鱼骨图：什么会挡住上线"
weight: 20
---

> **你在这里**：**① 定义** → ② 构建 → ③ 评估 → ④ 发布 → ⑤ 观测 → ⑥ 回流 · 圆心：黄金标准

**用时**：建议约 25 分钟（以现场讲师安排为准：约 20 分钟时提醒，约 25 分钟时喊时间到）

业务方说的“好”，通常是好几类诉求混在一起。鱼骨图把它拆到五个方面（认知、质量、责任、成本、性能，再加一个“其他”），只记录**业务方自己确认过的障碍**，不写方案。Claude Code 当主持人（`adlc-fishbone` skill），你们按 2.1 的分工扮演业务方。

## 开始访谈 {#s-2-2-start}

操作员在 `~/adlc-lab` 里启动 Claude Code：

```bash
claude
```

进入后输入下面这句，把角色卡作为**背景材料**交给主持人（组名换成你们的）：

```
用 adlc-fishbone 画鱼骨图，背景材料是 data/scenario/role-card.md，我们是第 3 组
```

有了背景材料，主持人可以少问几句场景问题；你们答“不知道”时，它可能会拿背景材料里的内容提示你们，但**只记录你们确认过的话**。Claude Code 第一次写文件或运行命令前会请你确认；它只会在 `outputs/fishbone/` 下建目录、生成文件，同意即可。

:image[Claude Code 里 adlc-fishbone 的开场：主持人说明规则，读取背景材料，只问一个问题]{src="/static/images/02-fishbone-start.png" width=900}
*图 2-1 鱼骨图访谈的开场：一次只问一个问题*

## 怎么回答 {#s-2-2-answer}

- **一次只答一个问题**，小组先商量再打字。主持人每轮会显示进度（问到了哪个方面、已经有几张便签）。
- 主持人把你们的原话改写成便签读回：准确就说“对”，不准确就指出哪里不对。
- **只用角色卡里的事实和你们自己的经验**；说不准就说“不知道”，不要现编数字。
- **不谈方案**。你们提了方案（比如“加个人工复核就行”），主持人会把它放进待讨论清单，再追问“没有这个办法时，最容易出什么错”。

每条障碍只放在一个方面，按障碍本身归类，而不是按它是在哪个问题下说出来的。最容易放错的两种：

| 障碍 | 归到 | 为什么 |
|---|---|---|
| “一条反馈写了四个诉求，常常漏掉后面几个” | **质量** | 输出不完整（遗漏），哪怕是在问速度时说的 |
| “整批跑到一半超时，会上只拿到半份清单” | **性能** | 没跑完：延迟、吞吐、稳定性才是性能 |

## 时间到：收尾出图 {#s-2-2-wrap}

讲师喊时间到（或者你们觉得够了），操作员输入：

```
时间到，后面不答了
```

主持人会停止提问，把还没问到的方面标成“没问到”，把已确认的便签一次读回；你们确认后，它生成文件，并在最后列出**输出目录的完整路径**和文件清单，例如：

```
/home/ec2-user/adlc-lab/outputs/fishbone/第3组-2026-11-14/
  fishbone-data.json   鱼骨图数据（以它为准）
  fishbone.drawio      鱼骨图
  qa-record.md         问答记录（已脱敏）
  validation-report.txt  13 项检查结果
```

**记下这个路径**，下面两条命令都要用它（实验的个人后缀已经由 CloudFormation 设置好，不用另外配置）。

## 发布预览，在笔记本上看鱼骨图 {#s-2-2-publish}

EC2 不能开端口，所以把预览网页发到你自己的私有 S3 桶。在 Claude Code 里输入 `/exit` 退出（或另开一个 Session Manager 窗口，先 `sudo su - ec2-user`），运行（把路径换成 Claude Code 打印的那个）：

```bash
./tools/publish-define-pages.sh /home/ec2-user/adlc-lab/outputs/fishbone/第3组-2026-11-14
```

**预期输出**（结尾；链接是你自己的）：

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

在已登录控制台的笔记本浏览器里打开打印的“鱼骨图预览”链接，点右上角 **打开**。`fishbone.drawio` 点 **下载** 后可以用 app.diagrams.net 打开。桶始终禁止公开访问，链接不要发给组外的人。

:image[S3 控制台里打开的鱼骨图预览：五个方面加“其他”，每张便签都是业务方确认过的障碍]{src="/static/images/02-fishbone-preview.png" width=900}
*图 2-2 鱼骨图预览（网页在你的私有桶里，只有登录这个账号控制台的人能打开）*

::::expand{header="中途断了、或者想改一条便签"}
- 访谈中途断了：重新运行 `claude`，说“继续画鱼骨图，目录是 <上面的路径>”；问答摘要已经按方面写进 `qa-record.md`。
- 不要手改 `fishbone.drawio`。要改便签，告诉 Claude Code 改哪条，它会改 `fishbone-data.json`、读回确认后重新生成；然后重跑上面的发布命令（同名对象被覆盖，链接不变）。
- 想用你们自己组的 Agent 场景：见扩展[用自己的场景画鱼骨图](/90-extensions/e4-own-fishbone)，主线先用实验场景。
::::

下一步：[2.3 障碍 → 判据表](/02-define/23-criteria)
