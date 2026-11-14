---
title: "4 评估：V0 基线"
weight: 40
---

> **你在这里**：① 定义 → ② 构建 → **③ 评估** → ④ 发布 → ⑤ 观测 → ⑥ 回流 · 圆心：黄金标准

:image[你在这里：③ 评估]{src="/static/images/flywheel-evaluate.svg" width=220 height=220}

**用时**：约 25 分钟

评估环**取用圆心**：用第 2 章冻结的黄金集和一组评估器给 V0 打分，按三大支柱（红线 · 门禁 · 观测）读出结论，再把失败按五维归因。V0 本来就设计得偏弱，第一次不通过是正常的：有了分数，才知道改哪里、改完有没有用。

| 步骤 | 做什么 | 用时 |
|---|---|---|
| 4.1 | 认识评估器 | 4 分钟 |
| 4.2 | 部署并注册评估器 | 3 分钟 |
| 4.3 | 开启在线评估 | 2 分钟 |
| 4.4 | 让 V0 跑一遍黄金集 | 3 分钟 |
| 4.5 | 按需评估 | 5 分钟（含等待） |
| 4.6 | 读懂结论：红线 · 门禁 · 观测 | 4 分钟 |
| 4.7 | 失败归因：该修 Agent 还是该修评估体系 | 4 分钟 |

## 4.1 认识评估器 {#s-4-1}

评估器是“打分规则”。实验准备好了自定义评估器（代码在 `evaluators/`，有单元测试），再加上 AgentCore 的内置评估器。注册名都带你的前缀 `adlc_ws_`：

| 注册名 | 类型 · 级别 | 打什么分 | 在结论里的角色 |
|---|---|---|---|
| `label_accuracy` | 代码型（Lambda）· TRACE | 三项标签（是否重要 / 是否紧急 / 处理建议）与黄金集一致的比例 0–1；`Pass` = 三项全对；误拒了“不可自动拒绝”的需求返回 `CriticalFail`，整条判错 | 红线（误拒）；整条通过率等观测 |
| `label_important` | 代码型 · TRACE | 只看“是否重要”，对 = 1、错 = 0；控制台里的平均分就是这一项的准确率 | 门禁 |
| `label_urgent` | 代码型 · TRACE | 只看“是否紧急” | 门禁 |
| `label_decision` | 代码型 · TRACE | 只看“处理建议”（模糊样例允许多个可接受答案） | 门禁 |
| `reject_quality` | 代码型（Lambda 内调用评审模型）· TRACE | 拒绝理由与客户话术质量 0–1（理由专业性 / 理解与礼貌 / 原因说明 / 替代方案 / 风险控制），≥ 0.7 且无红线为 `Pass`；出现编造功能或配置路径、承诺上线时间、泄露其他客户信息等红线：`Fail`；Agent 没判“拒绝”时返回“不适用” | 红线（话术红线）；门禁（拒绝话术通过率） |
| `reject_quality_llm` | 原生 LLM-as-a-judge · TRACE | 同一套评审标准，用 AgentCore 自带的 LLM 评审实现 | 观测 |
| `Builtin.Correctness` | 内置 · TRACE | 对照黄金集的期望答案判断回答是否正确 | 观测 |
| `Builtin.Helpfulness` | 内置 · TRACE | 回答对用户是否有帮助 | 观测 |

评估器说明：`evaluators/README.md`；评审标准：`evaluators/adlc_reject_quality/rubric.py`；标注口径：`data/golden/LABELING_GUIDE.md`。

黄金集答案随 Lambda 一起打包（`evaluators/*/golden_set.json`，第 2.4 步冻结时同步过去），所以在线评估（没有“标准答案”输入）也能按需求编号找到答案打分。

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

在线评估让 AgentCore Evaluations 自动给**之后产生的**每个新 session 打分，分数显示在 CloudWatch 控制台。所以一定要先开启，再跑 4.4：

```bash
./41-online-eval.sh enable
```

**预期输出**：

```
✅ 已创建在线评估配置 adlc_ws_online_runtime（adlc_ws_online_runtime-5bA2M22yGv），采样 100%，session 空闲 2 分钟后打分
   之后每个新 session 都会自动评分，结果写入 CloudWatch：
   CloudWatch 控制台 → GenAI Observability → Bedrock AgentCore → 你的 Agent → Evaluations
```

数据源是 `aws/spans` 加 Runtime 的日志组，服务名 `adlc_ws_agent.DEFAULT`；采样 100%，session 空闲 2 分钟后开始打分；挂载 `label_accuracy` / `label_important` / `label_urgent` / `label_decision` / `reject_quality` 和 `Builtin.Helpfulness`。刚建好角色时可能先出现一两行“⏳ 等待 IAM 权限生效后重试”，脚本会自动重试。查看状态：`./41-online-eval.sh status`。

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

V0 的典型表现：处理建议几乎全是“待评估”——这正是它的缺陷。个别条目出现 `❌`（例如限流、超时）不影响后续：脚本会对同一条自动重试 2 次，仍失败的条目评估时按错计。

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

`FB-2610-014`（要求删除审计日志）在默认模型下没有任何输出（被模型的内容过滤拦下），所以算 1 条“格式错误”：在门禁里它三项都按错计，在 4.7 的归因里单独列在“其他未通过”。

## 4.6 读懂结论：红线 · 门禁 · 观测 {#s-4-6}

结论分三层看：先看有没有红线（一票否决），再看门禁是否全部达标，观测指标只用来找原因、看趋势。

| 支柱 | 看哪个数 | 怎么判断 |
|---|---|---|
| **红线**（一票否决） | 误拒：把“不可自动拒绝”的需求判成了拒绝（`CriticalFail`）；话术红线：`reject_quality` 列出的红线（编造功能或配置路径、承诺上线时间、泄露其他客户或内部信息、话术为空或与理由矛盾） | 必须 0 条。出现 1 条，不论准确率多高，结论都是不通过；这条需求整条判错 |
| **门禁**（全部达标） | 是否重要 / 是否紧急 / 处理建议准确率（红线条目、没有输出的条目都按错计）；拒绝话术通过率（判了“拒绝”的条目里 `reject_quality` 为 Pass 的比例） | 每项都 ≥ 70% 才达标 |
| **观测**（只看趋势） | 整条通过率、五维准确率、样例类型、话术分项、格式错误率、`reject_quality_llm`、`Builtin.Correctness` / `Builtin.Helpfulness` | 不影响结论，用来归因（4.7）和对比优化前后（第 5 章） |

这三层就是第 2.3 步判据表里的三档：判据表是业务方签字的标准，这里是它第一次被兑现成分数。规则代码在 `evaluators/shared/pillars.py`（终端结论、对比页共用）。

**在控制台看同样的分数**：**CloudWatch** → **GenAI Observability** → **Bedrock AgentCore** → `adlc_ws_agent` → **Evaluations** 页签。在线评估在 session 空闲 2 分钟后才开始打分，所以 4.4 跑完后约 5–10 分钟才陆续出现；能看到每个评估器的平均分、分布，以及每个 session 的得分与解释。

:image[CloudWatch GenAI Observability 里 adlc_ws_agent 的 Evaluations 页签：各评估器的平均分和分布]{src="/static/images/04-online-eval-tab.png" width=900}

*图 4-1 在线评估结果（V0）：和 4.5 的按需评估用的是同一组评估器*

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

和 4.5 对数时注意：4.5 把格式错误的条目按三项全错算，所以 4.5 每一项的错误数 = 这里的条数 + “其他未通过”的条数。

**怎么读**：拿两条举例。

- `FB-2610-009` 只错在“建议”（期望 拒绝，实际 待评估）。黄金集标的是**认知**，理由是“查重与合并已支持（CAP-004），客户未配置”。V0 的工具只查得到功能名称，看不到支持状态和配置方法，所以既没拒绝也没给配置路径：问题在**工具**。
- `FB-2610-026` 错在“紧急”（期望 是，实际 否）和“建议”（期望 迭代，实际 待评估）。黄金集标的是**责任**，理由是“安全漏洞 + 个人信息泄露风险，影响正在发生，不可拒绝”。V0 只看客户措辞，没有“影响正在发生就算紧急”的事实标准：问题在**提示词**。

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

## 检查一下：评估环有没有对齐黄金标准？ {#s-4-check}

**这次评估用的，是第 2 章冻结的那一版黄金集吗？** 对一下：`data/golden/MANIFEST.json` 的 sha256 与 2.4 冻结时打印的一致，评估的条数与冻结的条数一致；4.6 的三档和判据表的三档一一对应。只要黄金集或评估器在评估之后又改过，这份分数就不能再当基线。

下一章：[5 优化：第一轮迭代](/05-optimize)
