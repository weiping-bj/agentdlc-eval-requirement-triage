---
title: "扩展 5 标签方案切换（是/否 → P0/P1/P2）"
weight: 50
---

> **你在这里**：**① 定义** → **② 构建** → **③ 评估** → **④ 发布** → ⑤ 观测 → ⑥ 回流 · 圆心：黄金标准（修评估体系回路）

**用时**：约 20 分钟（切换约 5 分钟，再跑一次黄金集和评估约 10 分钟）

第 2.5 步讨论过：业务方要 P0/P1/P2 分级时，标准 → 黄金集 → 评估器 → 提示词 → 门禁一整条链都要跟着变。这个扩展用**一条命令**走完整条链，让你看到每一环具体改了什么。这是“修评估体系”：切换之后，分数要在新口径下重新算基线，不能和切换前直接比。

## E5.1 先预览 {#e5-1}

```bash
./ext-switch-label-scheme.sh level --dry-run
```

优先级怎么算对有两种：`--tolerance 0`（默认，完全一致才算对）或 `--tolerance 1`（相差不超过一级也算对）。

**预期输出**（实验里已经部署过 Runtime、注册过评估器、开启过在线评估时，共 5 步；黄金集每条都已有优先级，所以不需要补）：

```
▶ 切换标签方案 → P0/P1/P2 分级（5 步，只预览）
  [1/5] 标准：config/local.env 写入 LABEL_SCHEME=level、LEVEL_TOLERANCE=0
  [2/5] 黄金集：按 level 口径校验并冻结（同步进评估器目录）
        $ python3 data/tools/golden_tool.py --scheme level freeze
  [3/5] 评估器：重新部署 Lambda 并注册（分级口径多一个 label_level）
        $ ./40-create-evaluators.sh
  [4/5] 提示词：重新部署 Runtime（环境变量 ADLC_LABEL_SCHEME 决定是否要求输出 level）
        $ ./30-deploy-runtime.sh
  [5/5] 门禁：用新的评估器列表重新启用在线评估（runtime）
        $ ./41-online-eval.sh enable
（预览，什么都没改）
```

| 步骤 | 影响链上的哪一环 | 具体改什么 |
|---|---|---|
| 1 | 标准 | `config/local.env` 写入 `LABEL_SCHEME=level`、`LEVEL_TOLERANCE`；之后每个脚本都按分级口径运行 |
| 2 | 黄金集 | 缺优先级的样本按“重要且紧急 = P0、重要不紧急 = P1、其它 = P2”补上（实验自带的样本都已标好），再按分级口径校验、冻结 |
| 3 | 评估器 | 标签评估器 Lambda 的环境变量 `ADLC_LABEL_SCHEME=level`；新增一个只看优先级的评估器 `label_level` |
| 4 | 提示词 | Runtime 的环境变量 `ADLC_LABEL_SCHEME=level`：Agent 的系统提示词后面会追加 `agent/adlc_agent/prompts/level-scheme.md`，要求 `level` 字段必须填 P0/P1/P2 |
| 5 | 门禁 | 在线评估配置挂上 `label_level`；按需评估的门禁多一项“优先级准确率” |

## E5.2 切换 {#e5-2}

新口径下要重新跑一次 **V0 基线**（尺子换了，旧基线作废）。先把版本切回 V0（只改配置），再运行切换命令（它会按当前配置重新部署 Runtime，这样一次部署就够了）：

```bash
./50-switch-version.sh v0
./ext-switch-label-scheme.sh level
```

**预期输出**（结尾）：

```
TODO-LIVE-E5
```

每一步都可以重复运行；中途某一步失败，改好后重新运行同一条命令即可。

## E5.3 在新口径下重新评估 {#e5-3}

```bash
./42-run-golden.sh
```

等 2–3 分钟：

```bash
./43-run-eval.sh
```

结论里的门禁多了一行“优先级准确率”。这一次的分数就是**新口径下的 V0 基线**；和切换前的分数放在一起比没有意义（尺子换了）。之后要优化，就按第 5 章在新口径下再走一遍。

## E5.4 切回“是 / 否” {#e5-4}

```bash
./ext-switch-label-scheme.sh binary
```

切回之后同样要重新跑一次黄金集和评估，才有“是 / 否”口径下的当前分数。

如果讨论给的 P0/P1/P2 口径和默认推导不同，交给 Claude Code：`claude "$(cat data/prompts/05-switch-label-scheme.md)"`，它会读讨论结论、逐条复核优先级，再用同一条命令完成切换。

## 检查一下 {#e5-check}

**切换之后，第 2.3 步签字的判据表还成立吗？** 处理建议准确率 ≥ 70% 这类门禁判据，现在要变成“优先级准确率 ≥ ?”——这个阈值该由业务方重新签字，而不是由你在命令行里决定。
