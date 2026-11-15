---
title: "4 评估：V0 基线"
weight: 40
---

> **你在这里**：① 定义 → ② 构建 → **③ 评估** → ④ 发布 → ⑤ 观测 → ⑥ 回流 · 圆心：黄金标准

:image[你在这里：③ 评估]{src="/static/images/flywheel-evaluate.svg" width=220 height=220}

**用时**：约 28 分钟

评估环**取用圆心**：用第 2 章冻结的黄金集和一组评估器给 V0 打分，按三大支柱（红线 · 门禁 · 观测）读出结论，再把失败按五维归因。V0 本来就设计得偏弱，第一次不通过是正常的：有了分数，才知道改哪里、改完有没有用。

| 步骤 | 做什么 | 用时 |
|---|---|---|
| 4.1 | 认识评估器 | 6 分钟 |
| 4.2 | 部署并注册评估器 | 3 分钟 |
| 4.3 | 开启在线评估 | 2 分钟 |
| 4.4 | 让 V0 跑一遍黄金集（等待时读 4.6） | 3 分钟 |
| 4.5 | 按需评估，生成结果页 | 6 分钟（含等待） |
| 4.6 | 读懂结论：红线 · 门禁 · 观测 | 4 分钟 |
| 4.7 | 失败归因：该修 Agent 还是该修评估体系 | 4 分钟 |

## 4.1 认识评估器 {#s-4-1}

评估器是“打分规则”：读一个 session 的 trace（需求原文、工具调用、Agent 的最终输出），返回分数、标签（`Pass` / `Fail` 等）和一段解释。同一组评估器有三种运行方式：

| 方式 | 怎么跑 | 能不能带标准答案 | 本实验 |
|---|---|---|---|
| **按需评估**（on-demand） | 你指定 session 和评估器，同步返回每条的分数和解释 | 能：黄金集答案作为参考输入一起传入 | 发布前（4.5、5.4）给黄金集打分，出发布结论 |
| **在线评估**（online） | 服务按采样规则自动给新 session 打分，分数写入 CloudWatch | 不能 | 上线后（4.3 开启，6.2 查看）持续观测 |
| **批量评估**（batch） | 提交一个异步作业，服务自己从 CloudWatch 找 session 打分，返回各评估器的平均分，每条的明细写进 CloudWatch Logs | 能：随作业附上标准答案 | 不用 |

本实验不用批量评估：只有 30–31 个 session，按需评估每次调用给一个 session 的一个评估器打分、同步返回分数和解释（每条依次调用各个评估器），`./43-run-eval.sh` 边评边打印，评完直接存成一个文件，再**按条合并**出结论（一条需求出现红线，它在每个门禁里都按错计，4.6），供 4.7 归因和第 5 章的对比页使用。批量评估是异步作业：提交后要等作业完成，再去 CloudWatch Logs 取每条的明细；它适合不想自己逐条调用的场合，例如定期抽查一段时间的线上流量。

实验准备好了 6 个自定义评估器（代码在 `evaluators/`，有单元测试），再加上 AgentCore 的 2 个内置评估器，级别都是 TRACE（每条 trace 打一次分）；分级口径多一个 `label_level`，见扩展[标签方案切换](/90-extensions/e5-label-scheme)。注册名都带你的前缀 `adlc_ws_`：

| 注册名 | 类型 | 按需 | 在线 | 读黄金集的什么 | 在结论里的角色 | 打什么分 |
|---|---|---|---|---|---|---|
| `label_important` | 代码型（Lambda） | 是 | 是 | 期望标签、可否自动拒绝 | 门禁；误拒时也返回 `CriticalFail`（红线） | 只看“是否重要”，每条对 = 1、错 = 0，按条平均就是这一项的准确率 |
| `label_urgent` | 代码型（Lambda） | 是 | 是 | 期望标签、可否自动拒绝 | 门禁；误拒时也返回 `CriticalFail`（红线） | 只看“是否紧急” |
| `label_decision` | 代码型（Lambda） | 是 | 是 | 期望标签、可接受答案、可否自动拒绝 | 门禁；误拒时也返回 `CriticalFail`（红线） | 只看“处理建议”（模糊样例允许多个可接受答案） |
| `label_accuracy` | 代码型（Lambda） | 是 | 是 | 期望标签、可接受答案、可否自动拒绝 | 红线（误拒）；观测（格式错误、整条通过率、五维准确率） | 三项标签与黄金集一致的比例 0–1；`Pass` = 三项全对；误拒了“不可自动拒绝”的需求返回 `CriticalFail`，整条判错 |
| `reject_quality` | 代码型（Lambda 内调用评审模型） | 是 | 是 | 拒绝要点、话术要点、可否自动拒绝；另读能力目录 | 红线（话术红线）；门禁（拒绝话术通过率）；观测（五个分项和均分） | 拒绝理由与客户话术质量 0–1（理由专业性 / 理解与礼貌 / 原因说明 / 替代方案 / 风险控制），≥ 0.7 且无红线为 `Pass`；出现编造功能或配置路径、承诺上线时间、泄露其他客户信息等红线：`Fail`；Agent 没判“拒绝”时返回“不适用” |
| `reject_quality_llm` | LLM 评审（AgentCore 原生） | 是 | 否 | 不读（对照 trace 里 Agent 自己的工具结果） | 观测 | 同一套评审标准，用 AgentCore 自带的 LLM-as-a-judge 实现 |
| `Builtin.Correctness` | 内置 | 是 | 否 | 参考输入里的期望标签 | 观测 | 对照参考答案判断回答是否正确 |
| `Builtin.Helpfulness` | 内置 | 是 | 是 | 不读 | 观测 | 回答对用户是否有帮助 |

黄金集答案通过两条路到达评估器（图 4-1）：冻结时随 Lambda 一起打包（`evaluators/*/golden_set.json`，第 2.4 步同步过去），所以在线评估没有参考输入，也能按需求编号找到答案打分；按需评估时脚本还把这条的期望标签作为参考输入传给每个评估器，标签评估器以它为准，`Builtin.Correctness` 拿它做对照。表里的字段对应黄金集的 `expected`（期望标签）、`acceptable_decisions`（可接受答案）、`can_auto_reject`（可否自动拒绝）、`reject_points` / `reply_points`（拒绝要点 / 话术要点）。

:image[评估器与黄金集关系图：上方是一条黄金样本的字段（期望标签、可接受答案、可否自动拒绝、拒绝和话术要点）、能力目录、答案到达评估器的两条路；中间每个评估器一行，左边是它读的字段，中间是按需和在线是否使用，右边是它进红线、门禁还是观测；下方是三大支柱和结论规则]{src="/static/images/eval-golden-map.svg" width=900 height=694}

*图 4-1 评估器怎么用黄金集：读哪些字段、按需还是在线、结果进结论的哪一支柱*

::::expand{header="为什么是这几个评估器：自定义和内置各管什么"}
- **三项标签：代码型评估器（Lambda）。** 业务方的目标就是三项判断各自的准确率，对错和黄金集一比就知道，不需要模型：用代码打分，不花 token，每次结果一样。每项单独注册一个评估器，每条打 0 或 1 分，按条平均就是这一项的准确率；`label_accuracy` 把三项合起来看整条。四个标签评估器遇到误拒都返回 `CriticalFail`。
- **拒绝话术：在代码型评估器里调用评审模型（`reject_quality`）。** 话术写得好不好是主观质量，要模型来评；放在自己的 Lambda 里，是因为评审时要带上这条的拒绝要点、话术要点和能力目录摘录：只有拿着能力目录，才认得出话术里“编造的功能或配置路径”这类红线。
- **原生 LLM 评审（`reject_quality_llm`）。** 同一套评审标准，用 AgentCore 自带的 LLM-as-a-judge 配置实现，不用写代码；它不读黄金集，只对照 trace 里 Agent 自己查到的工具结果。本实验只把它放在观测里。
- **内置评估器（`Builtin.*`）。** 开箱即用，打的是通用质量（回答是否正确、是否有帮助），不知道本实验“重要 / 紧急 / 处理建议”的业务口径，所以只作观测；业务方签字的判据，要靠自定义评估器来检查。
::::

评估器说明：`evaluators/README.md`；评审标准：`evaluators/adlc_reject_quality/rubric.py`；标注口径：`data/golden/LABELING_GUIDE.md`。

## 4.2 部署并注册评估器 {#s-4-2}

```bash
./40-create-evaluators.sh
```

**预期输出**（约 1 分钟；评估器 ID 末尾的 10 位随机字符每个人不同）：

```
▶ 部署评估器 Lambda 并注册到 AgentCore Evaluations（区域 us-west-2）
  🆕 已创建 Lambda adlc-ws-eval-label
  🆕 已创建 Lambda adlc-ws-eval-label-important
  🆕 已创建 Lambda adlc-ws-eval-label-urgent
  🆕 已创建 Lambda adlc-ws-eval-label-decision
  🆕 已创建 Lambda adlc-ws-eval-reject
  🆕 已注册评估器 adlc_ws_label_accuracy → adlc_ws_label_accuracy-bphPva3uwQ
  🆕 已注册评估器 adlc_ws_label_important → adlc_ws_label_important-uAi7di3yQ7
  🆕 已注册评估器 adlc_ws_label_urgent → adlc_ws_label_urgent-3rr1478wYL
  🆕 已注册评估器 adlc_ws_label_decision → adlc_ws_label_decision-eRc0HzGLd8
  🆕 已注册评估器 adlc_ws_reject_quality → adlc_ws_reject_quality-AyUlw5DoxI
  🆕 已注册评估器 adlc_ws_reject_quality_llm → adlc_ws_reject_quality_llm-uP6ENoG3b5
✅ 评估器就绪：
   adlc_ws_label_accuracy                   adlc_ws_label_accuracy-bphPva3uwQ
   adlc_ws_label_important                  adlc_ws_label_important-uAi7di3yQ7
   adlc_ws_label_urgent                     adlc_ws_label_urgent-3rr1478wYL
   adlc_ws_label_decision                   adlc_ws_label_decision-eRc0HzGLd8
   adlc_ws_reject_quality                   adlc_ws_reject_quality-AyUlw5DoxI
   adlc_ws_reject_quality_llm               adlc_ws_reject_quality_llm-uP6ENoG3b5
   下一步：./41-online-eval.sh enable   （开启在线评估，让分数显示在 CloudWatch 控制台）
```

确认注册结果：

```bash
./40-create-evaluators.sh --list
```

**预期输出**：

```
adlc_ws_label_accuracy                       adlc_ws_label_accuracy-bphPva3uwQ  TRACE  ACTIVE
adlc_ws_label_decision                       adlc_ws_label_decision-eRc0HzGLd8  TRACE  ACTIVE
adlc_ws_label_important                      adlc_ws_label_important-uAi7di3yQ7  TRACE  ACTIVE
adlc_ws_label_urgent                         adlc_ws_label_urgent-3rr1478wYL  TRACE  ACTIVE
adlc_ws_reject_quality                       adlc_ws_reject_quality-AyUlw5DoxI  TRACE  ACTIVE
adlc_ws_reject_quality_llm                   adlc_ws_reject_quality_llm-uP6ENoG3b5  TRACE  ACTIVE
共 6 个自定义评估器（另有 Builtin.* 内置评估器）
```

每行依次是评估器名、评估器 ID、级别、状态。控制台查看：**Amazon Bedrock AgentCore** → 左侧 **Evaluations** → **Evaluators**。

:::alert{header="评估器被在线评估挂上之后会被锁定" type="info"}
评估器一旦挂到在线评估配置上，就不能再修改。以后黄金集改了要重跑 `./40-create-evaluators.sh` 时，脚本会自动处理：没变化的评估器保持不动；只是黄金集变了时，只更新 Lambda 里的代码和黄金集；评估器配置本身变了且被锁定时，脚本会先临时删除在线评估配置，重建评估器，再恢复在线评估。
:::

## 4.3 开启在线评估 {#s-4-3}

两种评估的分工（4.1 的第一张表）：**按需评估**（4.5）在发布前用黄金集的标准答案打分，给出发布结论；**在线评估**在上线后自动给真实流量打分，没有标准答案也能打一部分分，用来持续观测（第 6.2 步）。在线评估只给**之后产生的**新 session 打分，所以一定要先开启，再跑 4.4：

```bash
./41-online-eval.sh enable
```

**预期输出**：

```
✅ 已创建在线评估配置 adlc_ws_online_runtime（adlc_ws_online_runtime-5bA2M22yGv），采样 100%，session 空闲 2 分钟后打分
   之后每个新 session 都会自动评分，结果写入 CloudWatch：
   CloudWatch 控制台 → GenAI Observability → Bedrock AgentCore → 你的 Agent → Evaluations
```

数据源是 `aws/spans` 加 Runtime 的日志组，服务名 `adlc_ws_agent.DEFAULT`；采样 100%，session 空闲 2 分钟后开始打分；挂载 `label_accuracy` / `label_important` / `label_urgent` / `label_decision` / `reject_quality` 和 `Builtin.Helpfulness`：门禁和红线评估器与按需评估相同，只少了两个只作观测的（`reject_quality_llm`、`Builtin.Correctness`）。偶尔会先出现一两行“⏳ 等待 IAM 权限生效后重试”，脚本会自动重试。查看状态：`./41-online-eval.sh status`。

## 4.4 让 V0 跑一遍黄金集 {#s-4-4}

每条需求一个 session，trace 进入 CloudWatch，作为评估的原始数据：

```bash
./42-run-golden.sh
```

**预期输出**（约 2 分钟；条数是你冻结的黄金集条数）：

```
▶ 运行 31 条需求 · AgentCore Runtime · 版本 v0 · 模型 us.anthropic.claude-sonnet-5-5 · 并发 4
  [ 1/31] FB-2610-001       13.2s  重要=是 紧急=否 建议=待评估
  [ 2/31] FB-2610-002       12.8s  重要=是 紧急=否 建议=待评估
  [ 3/31] FB-2610-003       12.6s  重要=是 紧急=是 建议=待评估
  [ 4/31] FB-2610-005       15.5s  重要=是 紧急=否 建议=待评估
  [ 5/31] FB-2610-007       14.7s  重要=是 紧急=否 建议=待评估
  …
  [ 9/31] FB-2610-014        8.7s  重要=None 紧急=None 建议=None
  [29/31] FB-2610-041       16.7s  重要=是 紧急=否 建议=拒绝
  [31/31] FB-2610-048       10.2s  重要=否 紧急=否 建议=迭代
✅ 完成：31 条，用时 107s，失败 0 条 → results/run-runtime-v0-20261114T134711Z.jsonl
   run_id = runtime-v0-20261114T134711Z
```

V0 的典型表现：处理建议几乎全是“待评估”——这正是它的缺陷。个别条目出现 `❌`（例如限流、超时）不影响后续：脚本会对同一条自动重试 2 次，仍失败的条目在门禁里按错计。条数是你冻结的黄金集条数：操作员是 31，其他同学是自带的 30。

## 4.5 按需评估 {#s-4-5}

脚本从 CloudWatch 取每个 session 的 trace，交给 AgentCore Evaluations 的 Evaluate API 逐个评估器打分（把黄金集答案作为参考输入一起传入），结果存到 `results/eval-<run_id>.json`。

:::alert{header="先等 2–3 分钟" type="warning"}
trace 写进 CloudWatch 有延迟。跑完 4.4 后先等 2–3 分钟再评估；脚本自己也会重试，找不到 trace 时会等待再查。
:::

```bash
./43-run-eval.sh
```

**预期输出**（约 2–3 分钟；开头每条一行，结尾按三大支柱汇总）：

```
▶ 评估 run runtime-v0-20261114T134711Z：31 个 session × 8 个评估器
  评估器：label_accuracy, label_important, label_urgent, label_decision, reject_quality, reject_quality_llm, Builtin.Correctness, Builtin.Helpfulness
  [ 1/31] FB-2610-001    三项=Fail     重要=Pass  紧急=Pass  建议=Fail  话术=不适用
  [ 2/31] FB-2610-003    三项=Fail     重要=Pass  紧急=Pass  建议=Fail  话术=不适用
  [ 3/31] FB-2610-002    三项=Fail     重要=Fail  紧急=Pass  建议=Fail  话术=不适用
  …
  [31/31] FB-2610-041    三项=Fail     重要=Fail  紧急=Pass  建议=Pass  话术=Fail
═══ 结果（154s）· 三大支柱：红线 0 条 + 门禁各 ≥ 70% 才通过 ═══
  红线（一票否决，要求 0 条）：1 条 ❌  误拒 0 · 话术红线 1
    ⛔ FB-2610-041（G29）话术红线：话术称可经API/连接器受控跨租户访问，目录无此能力
  门禁（各 ≥ 70%）：
    是否重要准确率：68% ❌
    是否紧急准确率：81% ✅
    处理建议准确率：39% ❌
    拒绝话术通过率：50%（1/2 条 Pass） ❌
  观测（只看趋势，不影响结论）：整条通过率 19% · 格式错误 1 条 · 话术均分 0.45（2 条）
    label_accuracy: 平均 0.65（31 条）
    reject_quality: 平均 0.30（3 条）
    reject_quality_llm: 平均 0.40（2 条）
    Builtin.Correctness: 平均 0.10（31 条）
    Builtin.Helpfulness: 平均 0.83（31 条）
  ❌ 不通过：红线 1 条（一票否决）；门禁未达标：是否重要准确率、处理建议准确率、拒绝话术通过率 · 详细结果：results/eval-runtime-v0-20261114T134711Z.json
```

大模型每次运行有几个百分点的波动，你的数字和上面略有不同是正常的。“话术=不适用”表示这条 Agent 没有判“拒绝”，话术评估器不打分。

`FB-2610-014`（要求删除审计日志）在默认模型下没有给出约定的 JSON（输出是空的，被模型的内容过滤拦下），所以记为 1 条“格式错误”（FormatError）：在门禁里它三项都按错计，`reject_quality` 也给它记了 0 分（所以那一行是 3 条），而“话术均分 / 拒绝话术通过率”只统计 Agent 真的判了“拒绝”的 2 条；在 4.7 的归因里它单独列在“其他未通过”。

**结果页：逐条看 Agent 输出、工具调用和评估器的解释。** 终端里每条只有一行。把这次评估生成一张网页，在浏览器里逐条看：

```bash
./45-results-page.sh
```

**预期输出**（结尾）：

```
▶ 结果页：V0 runtime-v0-20261114T134711Z
✅ 结果页已生成（本地文件，可直接用浏览器打开）：
results/results-page-runtime-v0-20261114T134711Z.html
☁️  已上传到私有桶 s3://adlc-ws-<account>-us-west-2/reports/results-page-runtime-v0-20261114T134711Z.html
👉 在笔记本浏览器打开（需已登录控制台），点右上角「打开」查看：
   https://us-west-2.console.aws.amazon.com/s3/object/adlc-ws-<account>-us-west-2?region=us-west-2&prefix=reports/results-page-runtime-v0-20261114T134711Z.html
```

“本地文件”指 EC2 上的文件：在已登录控制台的笔记本浏览器打开最后打印的链接，点右上角 **打开**。页面从上到下是：这个版本的结论（红线 · 门禁 · 通过率，和上面终端里的数一致）、版本趋势（第 5 章评估过 V1 之后才有）、全部需求的清单。清单可以按“判错 / 含红线”、按字段（例如“处理建议错”）、按五维筛选；点一行，右侧打开这条需求的详情：需求原文、黄金集答案、Agent 的输出（三项标签、理由、拒绝理由、客户话术）、这条 trace 里的工具调用（调了哪个工具、传了什么、返回了什么），以及每个评估器的结果、分数和解释原文。4.7 归因时就用它。

## 4.6 读懂结论：红线 · 门禁 · 观测 {#s-4-6}

结论分三层看：先看有没有红线（一票否决），再看门禁是否全部达标，观测指标只用来找原因、看趋势。

| 支柱 | 看哪个数 | 怎么判断 |
|---|---|---|
| **红线**（一票否决） | 误拒：把“不可自动拒绝”的需求判成了拒绝（`CriticalFail`）；话术红线：`reject_quality` 列出的红线（编造功能或配置路径、承诺上线时间、泄露其他客户或内部信息、话术为空或与理由矛盾） | 必须 0 条。出现 1 条，不论准确率多高，结论都是不通过；这条需求整条判错 |
| **门禁**（全部达标） | 是否重要 / 是否紧急 / 处理建议准确率（红线条目、没有输出的条目都按错计）；拒绝话术通过率（判了“拒绝”的条目里 `reject_quality` 为 Pass 的比例） | 每项都 ≥ 70% 才达标 |
| **观测**（只看趋势） | 整条通过率、五维准确率、样例类型、话术分项、格式错误率、`reject_quality_llm`、`Builtin.Correctness` / `Builtin.Helpfulness` | 不影响结论，用来归因（4.7）和对比优化前后（第 5 章） |

这三层就是第 2.3 步判据表里的三档。但要分清**签字的判据**和**本实验自动执行的规则**：结论固定由上表的 2 类红线 + 4 个门禁决定，阈值是 `config/lab.env` 的 `TARGET_ACCURACY`（70%）。判据表里的其它门禁（成本、性能、待评估占比这类指标读数），以及签字时改过的阈值，**不会自动进结论**：它们要么在第 6 章人工对一眼，要么是将来要接上的评估器。规则代码在 `evaluators/shared/pillars.py`（终端结论、对比页共用）。

**在控制台看同样的分数**：**CloudWatch** → **GenAI Observability** → **Bedrock AgentCore** → `adlc_ws_agent` → **Evaluations** 页签。在线评估在 session 空闲 2 分钟后才开始打分，分数大约在 4.4 跑完后 10–15 分钟才陆续出现。图表和平均分按 **session 的时间**画，但所选时间范围里至少要有一条已经写入的分数（哪个 session 的都算），否则只显示“没有可显示的评估配置”；最稳妥的是从 4.4 开始之前一直选到打分完成之后，最简单的是等 15 分钟后选“过去 1 小时”。能看到每个评估器的平均分、分布，以及每个 session 的得分与解释。平均分卡片是“每根柱子（一个时间段，宽度随所选范围变化，例如 1 或 2 分钟）的平均分”再平均，不按条数加权：哪个时间段只有一两个 session，它们的分数就被放大，所以卡片可能和 4.5 差十几个百分点（实测 V1 参考版“是否重要”：卡片 0.73，5.4 的按需评估是 87%）。发布结论以 4.5 的按需评估为准，控制台用来看分布和持续观测。

:image[Evaluations 页签的 Other 区：adlc_ws_label_urgent 0.9、reject_quality 0.26、label_decision 0.49、label_accuracy 0.69、label_important 0.69，下方是 label_urgent 的柱状图，图例 FormatError / Pass / Fail]{src="/static/images/04-online-eval-tab-Others.png" width=900}

*图 4-2 在线评估结果（V0，31 个 session）：自定义评估器。柱子颜色按控制台图例读，不要按颜色猜 Pass / Fail（这张图里 Pass 是红色、Fail 是绿色）*

:image[Evaluations 页签的 Style 区：Builtin.Helpfulness 0.83，柱状图按 Not Helpful At All / Very Helpful / Above And Beyond 分色]{src="/static/images/04-online-eval-tab-Builtin.png" width=900}

*图 4-3 在线评估结果（V0）：内置评估器 Builtin.Helpfulness（同样按图例读颜色）*

## 4.7 失败归因：该修 Agent，还是该修评估体系 {#s-4-7}

列出判错的需求，带上黄金集给它标的五维（认知 / 质量 / 责任 / 成本 / 性能）和标注理由：

```bash
./44-failures.sh
```

**预期输出**（节选）：

```
▶ 失败用例归因：results/eval-runtime-v0-20261114T134711Z.json
  黄金集：data/golden/golden_set.jsonl
  run runtime-v0-20261114T134711Z · 共 31 条 · 判错 24 条 · 其他未通过 1 条

── 判错的需求（24 条）──
FB-2610-002（G02）  五维：认知、质量  类型：应拒绝
  错（期望→实际）：重要 否→是；建议 拒绝→待评估
  理由：单客户深度定制，无通用性；首页已支持按角色配置布局和网页组件嵌入外部页面…
…
FB-2610-009（G06）  五维：认知  类型：简单
  错（期望→实际）：建议 拒绝→待评估
  理由：查重与合并已支持（CAP-004），客户未配置，应给出配置路径而不是新开发。
…
FB-2610-026（G18）  五维：责任  类型：简单
  错（期望→实际）：紧急 是→否；建议 迭代→待评估
  理由：安全漏洞 + 个人信息泄露风险，影响正在发生，不可拒绝。
…

── 其他未通过（1 条，不计入下面的统计）──
FB-2610-014  FormatError：trace 中有 Agent 回复，但解析不出约定的 JSON（feedback_id…

── 五维统计（判错 24 条；一条有多个维度时每个维度各计一次）──
  认知 12 条 · 质量 9 条 · 责任 9 条 · 成本 4 条 · 性能 4 条
── 错在哪一项 ──
  重要 9 条 · 紧急 4 条 · 建议 17 条
```

和 4.5 对数时注意：4.5 把格式错误的条目和红线条目都按三项全错算，所以 4.5 每一项的错误数 = 这里这一项的条数 + “其他未通过”的条数 + 红线条目在这一项上原本答对的条数（这次 FB-2610-041 的“紧急”和“建议”原本是对的）。例如处理建议：17 + 1 + 1 = 19 条错，即 12/31 = 39%。

**怎么读**：拿两条举例。

- `FB-2610-009` 只错在“建议”（期望 拒绝，实际 待评估）。黄金集标的是**认知**，理由是“查重与合并已支持（CAP-004），客户未配置”。V0 的工具只查得到功能名称，看不到支持状态和配置方法，所以既没拒绝也没给配置路径：问题在**工具**。
- `FB-2610-026` 错在“紧急”（期望 是，实际 否）和“建议”（期望 迭代，实际 待评估）。黄金集标的是**责任**，理由是“安全漏洞 + 个人信息泄露风险，影响正在发生，不可拒绝”。V0 只看客户措辞，没有“影响正在发生就算紧急”的事实标准：问题在**提示词**。

在 4.5 的结果页里能直接看到这些证据：状态选“判错（含红线）”，点 `FB-2610-009`，详情里“工具调用”一栏是 V0 调用 `lookup_capability` 的关键词和返回（只有几个功能名称，没有支持状态和配置路径），“评估器”一栏是每个评估器的判定和解释原文。

:image[结果页的需求详情：FB-2610-009 的需求原文和黄金集答案，V0 的输出（处理建议 待评估，期望 拒绝），工具调用 lookup_capability 的关键词和只含功能名称的返回，下方是各评估器的结果和解释]{src="/static/images/04-results-page-drawer.png" width=900}

*图 4-4 结果页的需求详情：工具只返回了功能名称，这就是“问题在工具”的证据*

想确认一条失败是稳定的、不是模型偶然的波动，可以把这一条单独再发给 Agent 一次（可选，十几秒；不登记为一次运行，不影响上面的评估结果，在线评估照常会给这个 session 打分）：

```bash
./46-try-one.sh FB-2610-009
```

它打印 Agent 这次的三项判断、理由和话术，并逐项和黄金集比对（✓ / ✗）；也可以 `--text "……"` 发一条你自己写的需求。加 `--eval` 会**再调用一次 Agent**，等 trace 写入 CloudWatch（约 2–3 分钟）后对这个新 session 跑一遍评估器；只想给刚才那次打分、不再调用 Agent，用 `--session <session>`（6 小时内；session 名是输出里 `session = ` 后面那一串）。

| 维度 | 典型失败现象 | 多半是哪里的问题 |
|---|---|---|
| 认知 | 平台已支持的能力被判“迭代”；超出定位的需求没被拒绝；单客户定制被当成通用需求 | 工具拿不到能力状态 / 没查相似需求 |
| 质量 | 拒绝理由空泛、话术没有替代方案；“重要”判断标准不一致；被施压或夹带的指令带偏；多诉求漏掉后面几个 | 提示词缺少判断口径和话术要求 |
| 责任 | 数据丢失、安全、合规、合同承诺类被拒绝（`CriticalFail`）；该判紧急的线上故障没判 | 提示词缺少“不可自动拒绝”规则和紧急的事实标准 |
| 成本 | 价值高但难度大的需求被直接承诺“迭代” | 缺少难度 / 影响面的判断口径 |
| 性能 | 超时、单条耗时过长、token 过多 | 模型选择、工具返回内容过长 |

**动手改之前，先判断走哪条回路**（第 0.3 步的两条回路）：

| 你看到的 | 结论 | 下一步 |
|---|---|---|
| Agent 拿到了足够的信息和规则，却没有用对（或者工具根本没给它这些信息） | Agent 的问题 | **修 Agent**：第 5 章改提示词、工具 |
| Agent 的行为符合它拿到的信息和规则，只是这些信息或规则本身不完整（黄金集标错了、能力目录缺条目、判据没写到） | 评估体系的问题 | **修评估体系**：回到第 2 章补判据、改黄金集或能力目录，冻结后重新部署评估器，再重新跑基线 |
| 同一条，组里的人复核时意见也不一致 | 标准本身没定清楚 | 回到第 2 章和业务方确认口径 |

V0 的这些失败几乎都属于第一类，所以第 5 章走“修 Agent”回路。

::::expand{header="拆一条失败：FB-2610-041 从黄金样本到 V1（实测记录）"}
这是上面预期输出那次实测（run `runtime-v0-20261114T134711Z`）里唯一的一条红线。按“黄金样本 → Agent 输出 → 评估器 → 结论 → 五维 → V1 改了什么”走一遍；你自己的结果在 `results/eval-<run_id>.json` 里，每条需求的 `agent_output` 是 Agent 的输出，`scores` 下是每个评估器的分数和解释。

**① 黄金样本**（G29，`data/golden/golden_set.jsonl`）

| 字段 | 内容 |
|---|---|
| 需求原文 | 我们集团下面有 3 个独立的租户，希望在 A 租户里直接查询和修改 B、C 租户的客户数据，不想做数据同步。 |
| 客户层级 · 反馈类型 | KA · 新需求 |
| 期望标签 | 重要 否 · 紧急 否 · 处理建议 拒绝；可否自动拒绝：是 |
| 拒绝要点 | 不同租户之间数据严格隔离，不允许跨租户直接查询或修改（CAP-027）；这是平台的安全与合规底线 |
| 话术要点 | 理解集团统一管理客户的诉求；说明租户隔离对数据安全的意义；替代：集团合并到一个租户并使用多组织架构，或用集成平台在租户间同步 |
| 五维 · 类型 | 责任、认知 · 应拒绝 |

**② V0 的输出**：`重要=是 紧急=否 建议=拒绝`（4.4 预期输出里 `[29/31]` 那一行）。处理建议对了，“重要”错了；话术给客户提了一个替代方案。话术原文没有收进手册，下面评审的解释转述了它的问题。

**③ 评估器的判断**（4.5 预期输出里 `[31/31]` 那一行：`三项=Fail 重要=Fail 紧急=Pass 建议=Pass 话术=Fail`）

| 评估器 | 结果 | 原因 |
|---|---|---|
| `label_important` | Fail | 期望 否，实际 是 |
| `label_urgent` · `label_decision` | Pass · Pass | 与黄金集一致 |
| `label_accuracy` | Fail | 三项对两项；不是 `CriticalFail`，因为这条可以自动拒绝，判“拒绝”不算误拒 |
| `reject_quality` | Fail（0.2） | 解释（节选）：“……拒绝话术评审 0.2：理由专业性 4，理解与礼貌 4，原因说明 4，替代方案 3，风险控制 2。红线：话术称可经API/连接器受控跨租户访问，目录无此能力……” |

按五个分项换算，`reject_quality` 本来是 0.6；只要列出红线，分数就封顶 0.2、判 Fail。评审对照的是能力目录摘录（和这条需求最相关的几项能力的状态、限制、替代方案），目录里和它相关的 CAP-027 写明“不允许跨租户直接查询或修改数据”，替代方案是“集团统一使用一个租户 + 多组织；或通过集成平台在租户间同步”。

**④ 进结论**：话术红线 1 条，一票否决，所以 4.5 的结论是不通过，不论准确率多少。按条合并后，这条在三个标签门禁里都按错计（“紧急”“建议”原本是对的，上面对数时说的就是它），在拒绝话术通过率里记 Fail：V0 只判了 2 条拒绝，它没过，通过率就是 50%。

**⑤ 五维归因**：黄金集标的是**责任、认知**。认知：V0 的工具 `lookup_capability` 只返回功能名称，看不到 CAP-027 的限制（这是平台的安全底线，不是功能缺口）和替代方案，话术里就自己补出了一个目录里没有的能力。责任：对客户承诺平台做不到的事，就是话术红线。“重要”判错另有原因：V0 的提示词写着“战略客户、KA 客户提的需求……都算重要”，而这条正是 KA 客户提的。两处都是 Agent 拿到的信息和规则有问题，走**修 Agent**。

**⑥ V1 改了什么**（实测时 Claude Code 写的 V1，总结见 5.2 的预期结果；你的 V1 写在 `v1.changes.md`）

- 工具：用 `search_capabilities` 代替 `lookup_capability`，返回支持状态、限制、替代方案和菜单路径，CAP-027 的限制和替代方案都在里面；
- 提示词：话术里的替代方案只能来自工具返回的内容（实测那次总结的第 5 条，点名了 FB-041；5.2 的预期结果只节选了前两条）；“重要”只看判断口径，客户层级本身不算（第 3 条）。

结果：同一份黄金集、同一组评估器再评估（5.4），这条变成 `重要=否 紧急=否 建议=拒绝`，三项标签和话术都是 Pass；V1 参考版也一样，它的对应修改见 `agent/adlc_agent/prompts/v1-ref.changes.md` 里“`lookup_capability` 换成 `search_capabilities`”“写明判断口径”和“只说能力目录里写明的事实”这三行。
::::

## 常见问题 {#s-4-faq}

::::expand{header="为什么有的分数是 Skipped 或“不适用”？"}
这不是出错，是评估器认为这一条不该由它打分：这些结果不计入平均分，也不进门禁。

- 标签评估器返回 `Skipped`：trace 里找不到 Agent 的输出，或者这条需求不在黄金集里（例如 6.3 那 60 条里约一半）。没有标准答案，就不打分。
- `reject_quality` 返回“不适用”：Agent 没判“拒绝”，没有话术可评。`reject_quality_llm` 的“不适用”带 0.0 分，汇总时按标签排除，不当作 0 分。
- `FormatError` 不一样：Agent 有回复，但解析不出约定的 JSON，记 0 分，门禁里按错计（4.5 的 FB-2610-014）。
::::

::::expand{header="在线评估的分数为什么晚 10–15 分钟才出现？控制台为什么显示“没有可显示的评估配置”？"}
- 在线评估要等 session **空闲 2 分钟**才认为它结束，之后服务端才开始打分、再把分数写入 CloudWatch。实测一批 session 跑完后，分数大约 10–15 分钟才陆续出现。按需评估没有这段等待：4.4 跑完等 2–3 分钟（trace 写入 CloudWatch）就能评。
- 控制台右上角的时间范围要**包含分数写入的时间**：范围里一条已写入的分数都没有时，Evaluations 页签只显示“没有可显示的评估配置”（No evaluation configurations to display），即使范围里有 session。而图表和平均分是按 **session 的时间**画的。最简单的做法是等 15 分钟后选“过去 1 小时”；要单看某一次运行，就从那次运行开始前一直选到它的分数写完之后（6.2）。
::::

::::expand{header="为什么在线评估的分数和按需评估的不一样？"}
两边的答案都来自同一份冻结的黄金集（在线用打包进 Lambda 的那份，按需用参考输入；冻结后没有重跑 `./40-create-evaluators.sh` 时两份才会不一致），差别来自别处：

1. **平均的方法不同**：控制台的平均分卡片是各时间段平均分的再平均，不按条数加权（4.6）；按需评估按条计算准确率。
2. **按条合并只在结论里做**：控制台显示的是每个评估器自己的原始分数。例如 FB-2610-041 出了话术红线，在 4.5 的门禁里三项都按错计，在控制台里它的 `label_decision` 仍是 Pass。
3. **评审模型每次都重新评**：`reject_quality`、`Builtin.Helpfulness` 在线上和按需评估里各评一次，两次结果可以不同。
4. **时间范围里混进了别的运行**：V0 和 V1 的 session 在同一个范围里时，看到的是混合平均（6.2）。

发布结论以按需评估（4.5、5.4）为准，控制台用来看分布和趋势。
::::

::::expand{header="红线否决的到底是什么？"}
否决的是**整次评估的结论**：只要有 1 条红线，不论准确率多高，结论都是“不通过”，这个版本不能发布（6.1）。同时这条需求**整条判错**：三项标签在门禁里都按错计，话术记 Fail。红线只有两类：误拒（把黄金集标为“不可自动拒绝”的需求判成拒绝，标签评估器返回 `CriticalFail`）和话术红线（`reject_quality` 的解释里写着“红线：…”）。观测指标不改变结论，但整条通过率、五维准确率里这条也按错计。不要靠重跑把红线“跑掉”：改 Agent，再用同一把尺子评估（5.4）。
::::

::::expand{header="“门禁 ≥ 70%”是怎么算的？"}
- 每个门禁各自达到 70% 才算达标（`config/lab.env` 的 `TARGET_ACCURACY`，正好 70% 也算），有一个不达标，结论就是不通过。
- 三个标签门禁的分母是有标签结果的条目，通常就是黄金集的全部条数：Agent 调用失败、格式错误和红线条目都按错计；评估器返回 `Skipped` 的条目不计入；有 session 评估失败时，结论是“不完整”。31 条时 1 条约 3 个百分点，至少要对 22 条（71%）。
- 拒绝话术通过率的分母只是 Agent 判了“拒绝”、且被评审过的条目（V0 只有 2 条，没过 1 条就是 50%）。Agent 判了拒绝却没有评审结果时，结论不能判通过（一条评审结果都没有时，这个门禁显示“未评估”）；Agent 一条都没判拒绝时，这个门禁是“不适用”，不挡结论。
- 70% 来自业务方对三项判断准确率的目标（0.1），拒绝话术通过率沿用同一个阈值；签字时改过的阈值和判据表里的其它门禁不会自动进结论，见 4.6。
::::

## 检查一下：评估环有没有对齐黄金标准？ {#s-4-check}

**这次评估用的，是第 2 章冻结的那一版黄金集吗？** 对一下：`grep sha256 data/golden/MANIFEST.json` 和 2.4 冻结时打印的一致（其他同学对的是自带那一版），评估的条数与冻结的条数一致；再说出判据表里哪几条判据是由 4.6 的 2 类红线 + 4 个门禁自动检查的、哪几条没有。只要黄金集或评估器在评估之后又改过，这份分数就不能再当基线。

下一章：[5 优化：第一轮迭代](/05-optimize)
