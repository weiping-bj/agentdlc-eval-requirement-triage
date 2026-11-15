---
title: "5 优化：第一轮迭代"
weight: 50
---

> **你在这里**：① 定义 → **② 构建** → **③ 评估** → ④ 发布 → ⑤ 观测 → ⑥ 回流 · 圆心：黄金标准（修 Agent 回路）

:image[你在这里：② 构建 + ③ 评估，修 Agent 回路]{src="/static/images/flywheel-optimize.svg" width=220 height=220}

**用时**：约 30 分钟

第 4 章的归因说明 V0 的问题在 Agent 本身，所以这一章走**修 Agent** 回路：让 Claude Code 读评估结果，写出**你们自己的 V1**，重新部署，用**同一份黄金集、同一组评估器**再评估，最后生成前后对比页。时间不够时，直接用实验准备好的 **V1 参考版**。

| 步骤 | 做什么 | 用时 |
|---|---|---|
| 5.1 | 看一眼常见的优化方法 | 2 分钟 |
| 5.2 | Claude Code 分析评估结果，写出你的 V1 | 6 分钟 |
| 5.3 | 切换版本并重新部署 | 3 分钟 |
| 5.4 | 再跑黄金集并评估（有红线时再切一次 V1 参考版） | 13 分钟（含等待） |
| 5.5 | 前后对比页 | 6 分钟 |

:::alert{header="优化 Agent 时，不要动尺子" type="warning"}
这一章只改提示词、工具（或模型）。不要改黄金集、能力目录和评估器，否则 V0 和 V1 的分数不能比。如果你觉得是黄金集标错了，那是“修评估体系”回路：记下来，回到第 2 章处理，并重新跑 V0 基线。
:::

## 5.1 常见的优化方法 {#s-5-1}

| 失败现象（来自 4.7） | 五维 | 改哪里 |
|---|---|---|
| 分不清“已支持 / 真实缺口 / 明确不做” | 认知 | 工具：让能力查询返回支持状态、限制、替代方案和菜单路径（`agent/adlc_agent/tools.py`） |
| 单客户定制当成通用需求 | 认知 | 工具：统计上个月有几个不同客户提过 |
| “重要 / 紧急”标准不一致，被客户措辞或施压带偏 | 质量、责任 | 提示词：写明判断口径；“紧急”必须有影响正在发生或 30 天内硬性节点的事实 |
| 数据丢失 / 安全 / 合规 / 合同承诺类被拒绝 | 责任 | 提示词：“不可自动拒绝”清单，遇到只能判迭代或待评估 |
| 话术空泛、没有替代方案，或写了能力目录里没有的路径 | 质量、责任 | 提示词：话术三段式（理解诉求 → 说明原因 → 替代方案），平台事实只用工具返回的内容 |
| 太慢、太贵、偶发超时 | 成本、性能 | 模型：本实验只能在 Claude Sonnet 5.5 和 Amazon Nova 2 Lite 之间换。只换 Agent 的模型：在 `config/local.env` 写一行 `AGENT_MODEL_ID=us.amazon.nova-2-lite-v1:0` 再重新部署；`./tools/use-model.sh nova` 会连评审模型一起换（下一次 `./40-create-evaluators.sh` 生效），那就是换了尺子 |

## 5.2 用 Claude Code 写出你的 V1 {#s-5-2}

提示词已经放在实验目录里（`data/prompts/06-write-v1-from-eval.md`），不用复制粘贴。在 `~/adlc-lab` 里运行：

```bash
claude "$(cat data/prompts/06-write-v1-from-eval.md)"
```

它会依次：运行 `./44-failures.sh` 并读最新的评估结果 → 对照黄金集的标注理由归纳 3–5 条失败原因，并逐条判断是“修 Agent”还是“修评估体系” → 在 `v0.md` 的基础上写出 `agent/adlc_agent/prompts/v1.md` → 在 `tools.py` 的 `TOOLSETS` 里加 `"v1"`（只从已有工具里选）→ 把“失败原因 → 修改 → 预期影响的指标”写进 `agent/adlc_agent/prompts/v1.changes.md`。它不会打开 V1 参考版。

Claude Code 修改文件或运行命令前会请你确认；读完它的说明，同意即可（它只会改上面这三个文件）。大约 2–4 分钟。

**预期结果**（终端里的总结，节选；你的原因和措辞会不同）：

```
V1 已写好，`TOOLSETS` 里有 `v1`（检查输出 `['v0', 'v1', 'v1-ref']`）。我还没跑评估，下面的“预期影响”只是预期，没有验证过。

## 失败原因 → 修改 → 预期影响的指标
这几条都属于“修 Agent”。

1. **建议口径偏“待评估”**：17 条建议错，多数是该拒绝或迭代却判了待评估。
   - 修改：`v1.md` 改用标注口径，写清拒绝、迭代、待评估各自的条件，信息够就直接判。
   - 预期影响：处理建议准确率。
2. **工具信息不足**：V0 的工具只返回功能名。
   - 修改：`tools.py` 的 `v1` 用 `search_capabilities` 加 `find_similar_requests`，提示词要求每条先查这两个。
   - 预期影响：处理建议准确率、是否重要准确率（通用性）、拒绝话术通过率。
…（另外 4 条：重要的口径、紧急的事实标准、话术红线、只输出一个 JSON）

## 改动文件
- `agent/adlc_agent/prompts/v1.md`（新增）
- `agent/adlc_agent/prompts/v1.changes.md`（新增）
- `agent/adlc_agent/tools.py`（`TOOLSETS` 加了 `"v1"`）

`data/`、`evaluators/`、`labkit/` 和三个参考文件没有改动。我也没有打开 `v1-ref.md` 和 `v1-ref.changes.md`。

## 下一步
1. `./50-switch-version.sh v1`
2. `./30-deploy-runtime.sh`

部署后重新跑评估，再和 `v1-ref` 对照。
```

输入 `/exit` 退出 Claude Code，自己看一眼改了什么：

```bash
git diff --stat; git status --short agent/
```

:image[Claude Code 写完 V1 后在终端输出的总结：评估发现、失败原因 → 修改 → 预期影响的指标表、这一轮不改的（修评估体系）、下一步]{src="/static/images/05-claude-v1-summary.png" width=900}

*图 5-1 Claude Code 的总结：每个修改都对应一个评估发现*

## 5.3 切换版本并重新部署 {#s-5-3}

::::tabs{variant="container"}
:::tab{id="own" label="用你自己的 V1（推荐）"}
```bash
./50-switch-version.sh v1
./30-deploy-runtime.sh
```
:::
:::tab{id="ref" label="时间不够：用 V1 参考版"}
```bash
./50-switch-version.sh v1-ref
./30-deploy-runtime.sh
```
V1 参考版的修改说明在 `agent/adlc_agent/prompts/v1-ref.changes.md`；和 V0 的差异：`git diff --no-index agent/adlc_agent/prompts/v0.md agent/adlc_agent/prompts/v1-ref.md`。
:::
::::

**预期输出**（以你自己的 V1 为例；重新部署会复用依赖，约 1 分钟，Runtime 版本号加 1）：

```
✅ Agent 版本已切换为 v1
   下一步：./30-deploy-runtime.sh（重新部署 Runtime），然后 ./42-run-golden.sh
▶ 部署 adlc_ws_agent · Agent 版本 v1 · 模型 us.anthropic.claude-sonnet-5-5 · 区域 us-west-2
  ♻️  复用已下载的依赖 build/runtime/deps-e6d59a768c1a
  📦 打包完成：build/runtime/agent-20261114T135532Z.zip（5566 个文件，39.8 MB）
  ⬆️  已上传 s3://adlc-ws-<account>-us-west-2/runtime/agent-20261114T135532Z.zip
  🔐 执行角色 adlc-ws-runtime-role-us-west-2
  🔁 更新已有 Runtime adlc_ws_agent-VL2KJhC5Tp
  ⏳ Runtime 状态 UPDATING
  ⏳ Runtime 状态 READY
✅ Runtime 就绪：adlc_ws_agent-VL2KJhC5Tp（版本 2，Agent v1）
   下一步：./30-deploy-runtime.sh invoke   （调用一次确认可用）
```

还没有 `prompts/v1.md` 时，`./50-switch-version.sh v1` 会提示你先做 5.2，或者改用 `v1-ref`。

## 5.4 再跑黄金集并评估 {#s-5-4}

```bash
./42-run-golden.sh
```

等 2–3 分钟后：

```bash
./43-run-eval.sh
```

**预期输出**（实测：我们自己的 V1，结尾）：

```
═══ 结果（188s）· 三大支柱：红线 0 条 + 门禁各 ≥ 70% 才通过 ═══
  红线（一票否决，要求 0 条）：1 条 ❌  误拒 0 · 话术红线 1
    ⛔ FB-2610-004（G30）话术红线：拒绝限额内仍超时失败的线上问题；话术暴露内部能力编号CAP-011
  门禁（各 ≥ 70%）：
    是否重要准确率：90% ✅
    是否紧急准确率：90% ✅
    处理建议准确率：94% ✅
    拒绝话术通过率：88%（14/16 条 Pass） ✅
  观测（只看趋势，不影响结论）：整条通过率 84% · 格式错误 0 条 · 话术均分 0.80（16 条）
    label_accuracy: 平均 0.92（31 条）
    reject_quality: 平均 0.80（16 条）
    reject_quality_llm: 平均 0.80（11 条）
    Builtin.Correctness: 平均 0.58（31 条）
    Builtin.Helpfulness: 平均 0.85（31 条）
  ❌ 不通过：红线 1 条（一票否决） · 详细结果：results/eval-runtime-v1-20261114T135605Z.json
```

这是一个很典型的结果：三项准确率和话术通过率都过了 70%，但有 **1 条话术红线**（话术里写出了能力编号 CAP-011 这类内部信息），一票否决，结论仍是**不通过**。这条需求（FB-2610-004，报表超时，体验优化类）在黄金集里不属于“不可自动拒绝”，所以不算误拒；但 V1 把它判成“拒绝”本身也判错了。两个问题都在 Agent 这边，属于修 Agent。不要靠重跑把红线“跑掉”，按剩余时间二选一：

- **还有 10 分钟以上**：只修这一条，不要重跑 5.2 的命令（那会从 V0 重新写一遍）。运行 `claude`，告诉它：“在 `agent/adlc_agent/prompts/v1.md` 里加一条：客户话术里不得出现 CAP 编号等内部信息；把这条写进 `v1.changes.md`”，`/exit` 后 `./30-deploy-runtime.sh`，再做一遍 5.4（版本还是 v1，不用重新切换）；
- **不到 10 分钟**：切换到 V1 参考版（5.3 的第二个页签），再跑一遍 5.4。

进入第 5 章时如果已经落后 10 分钟以上，直接从 5.3 的“V1 参考版”页签开始，5.2 留到最后有时间再做。

**V1 参考版的实测结果**（结尾）：

```
═══ 结果（162s）· 三大支柱：红线 0 条 + 门禁各 ≥ 70% 才通过 ═══
  红线（一票否决，要求 0 条）：0 条 ✅  误拒 0 · 话术红线 0
  门禁（各 ≥ 70%）：
    是否重要准确率：87% ✅
    是否紧急准确率：94% ✅
    处理建议准确率：97% ✅
    拒绝话术通过率：100%（13/13 条 Pass） ✅
  观测（只看趋势，不影响结论）：整条通过率 81% · 格式错误 0 条 · 话术均分 0.87（13 条）
    label_accuracy: 平均 0.92（31 条）
    reject_quality: 平均 0.87（13 条）
    reject_quality_llm: 平均 0.96（9 条）
    Builtin.Correctness: 平均 0.45（31 条）
    Builtin.Helpfulness: 平均 0.86（31 条）
  ✅ 通过：无红线，门禁全部达标 · 详细结果：results/eval-runtime-v1-ref-20261114T140320Z.json
```

## 5.5 前后对比页 {#s-5-5}

```bash
./51-compare.sh
```

默认对比“V0”和“最后评估的那个其它版本”。输出里的“本地文件”指 EC2 上的文件，笔记本上用最后打印的 S3 控制台链接打开。要比别的两次，指定 run_id：`./51-compare.sh --before <run_id> --after <run_id>`（run_id 见每次运行结尾的 `run_id = …`，或 `.lab-state/runs.json`）。

**预期输出**（结尾）：

```
▶ 对比 V0 runtime-v0-20261114T134711Z → V1 runtime-v1-20261114T135605Z（红线 0 条且门禁各 ≥70% 才通过）
  V0：❌ 不通过：红线 1 条（一票否决）；门禁未达标：是否重要准确率、处理建议准确率、拒绝话术通过率
  V1：❌ 不通过：红线 1 条（一票否决）
✅ 对比页已生成（本地文件，可直接用浏览器打开）：
results/compare-runtime-v0-20261114T134711Z-vs-runtime-v1-20261114T135605Z.html
☁️  已上传到私有桶 s3://adlc-ws-<account>-us-west-2/reports/compare-runtime-v0-20261114T134711Z-vs-runtime-v1-20261114T135605Z.html
👉 在笔记本浏览器打开（需已登录控制台），点右上角「打开」查看：
   https://us-west-2.console.aws.amazon.com/s3/object/adlc-ws-<account>-us-west-2?region=us-west-2&prefix=reports/compare-runtime-v0-20261114T134711Z-vs-runtime-v1-20261114T135605Z.html
```

在已登录控制台的笔记本浏览器打开打印的链接，点右上角 **打开**（Open）：

:image[S3 控制台里对比页对象的详情页，Open 按钮被红框标出]{src="/static/images/05-compare-top-open.png" width=900}

*图 5-2 S3 对象页：点 Open 在新标签页打开对比页*

对比页在三大支柱明细前面先回答四个问题：

| 块 | 回答什么 |
|---|---|
| ① 评估器清单 | 用了哪些评估器、各衡量什么、属于红线 / 门禁 / 观测 |
| ② 优化前评估发现的问题 | 错在哪一项、属于哪一维、多半是哪里的问题（就是 4.7 的分析） |
| ③ 改了什么 | 模型、提示词（逐行差异）、工具、能力目录、评估器各改了什么；“失败原因 → 修改 → 预期影响的指标”来自 `v1.changes.md`（参考版是 `v1-ref.changes.md`） |
| ④ 效果验证 | 哪些需求错 → 对、对 → 错，按字段和按五维看 |

:image[对比页顶部（V0 → V1 参考版）：两个版本的结论、红线 1 条到 0 条、各门禁 V0 到 V1 参考版的变化和 70% 门槛，下方是两次运行]{src="/static/images/05-compare-top.png" width=900}

*图 5-3 对比页顶部：两个版本的结论（红线一票否决）和关键指标（截图是 V0 → V1 参考版；你自己的 V1 有红线时，这里显示“不通过”）*

:image[对比页的 ③ 改了什么：提示词差异、工具变化，以及失败原因、修改、预期影响的指标表]{src="/static/images/05-compare-changes.png" width=900}

*图 5-4 ③ 改了什么：每一处修改都能追溯到一个评估发现（截图里改动说明的第一行是参考版早先的写法，以你自己的对比页为准）*

:image[对比页的 ④ 效果验证：错到对、对到错的条数，按字段和按五维的翻转]{src="/static/images/05-compare-flips.png" width=900}

*图 5-5 ④ 效果验证：“对 → 错”的条目要先看原始输出再下结论*

桶禁止公开访问，没有公开链接；只有登录了你这个实验账号控制台的人能看到。

## 检查一下：这一轮迭代有没有对齐黄金标准？ {#s-5-check}

**对比页上 V0 和 V1 的两次评估，用的是同一把尺子吗？** 看对比页 ③ 的“评估器”一行（两次评估器 ID 相同），以及两次评估的条数是否相同。如果这一轮里你改过黄金集或评估器，这张对比页就不成立——那一轮其实走的是“修评估体系”，要先在新尺子下重跑 V0。

下一章：[6 发布与观测](/06-release-observe)
