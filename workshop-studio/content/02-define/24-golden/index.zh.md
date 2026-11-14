---
title: "2.4 判据 → 黄金集：补样本并冻结"
weight: 40
---

> **你在这里**：**① 定义** → ② 构建 → ③ 评估 → ④ 发布 → ⑤ 观测 → ⑥ 回流 · 圆心：黄金标准

**用时**：约 5 分钟

判据表里有一类“部分覆盖”是**评估器能判，但黄金集里还没有能检验它的样本**。比如业务方说“哪怕看起来很小的缺陷也不能自动拒绝”，而现有黄金集里的缺陷样本都是严重故障。这类缺口不用写新评估器，补 1–3 条黄金样本就能补上。这一步是在**修评估体系**：改的是尺子，不是 Agent。

## 提议样本 {#s-2-4-propose}

`adlc-criteria` 签完字后会接着列出“可以靠补黄金样本补上缺口的判据”，并提议 1–3 条样本（红线优先）。它优先从上个月已有、还不在黄金集里的需求里挑（`criteria.py candidates`），按 `data/golden/LABELING_GUIDE.md` 标注；都不合适时才新写一条合成需求。

然后它会在内存里试运行，生成**预览**（这时还没有改 `data/`）：

- `golden-proposals.csv`：要新增的黄金样本（`golden-set-feedback.csv` 的格式，操作 = 新增）；
- `extra-feedback.proposed.jsonl`：只有新写了需求时才有；
- `criteria.md` 末尾的提议表和写入步骤。

主持人会读出每条提议（需求标题、期望标签、为什么补），然后问：“要把这 N 条写进黄金集吗？”

## 写入并冻结 {#s-2-4-apply}

你们同意后，它才按 `criteria.md` 的写入步骤逐条执行（任何一步报错就停下）：

1. 新写了需求时：追加到 `data/templates/extra-feedback.jsonl`，运行 `python3 data/tools/generate_mock_data.py` 重新生成上个月的需求；
2. `criteria.py verify <目录> --before-apply` → `python3 data/tools/golden_tool.py apply-feedback --feedback <目录>/golden-proposals.csv --dry-run` 预览 → 去掉 `--dry-run` 正式写入；
3. **冻结**：`python3 data/tools/golden_tool.py freeze`（校验、导出、写 `data/golden/MANIFEST.json`，并把黄金集同步进评估器目录 `evaluators/*/golden_set.json`）；
4. `criteria.py verify <目录>` 再检查一次（全部 ✅），把判据表里补上的判据更新为新黄金编号，重新发布预览。

**预期输出**（冻结这一步，条数是你们补完之后的）：

```
已更新：evaluators/adlc_label_accuracy/golden_set.json, evaluators/adlc_reject_quality/golden_set.json
黄金集共 31 条
- 是否重要：否 18，是 13
- 是否紧急：否 25，是 6
- 处理建议：待评估 6，拒绝 13，迭代 12（多答案可接受 6 条）
- 优先级：P0 6，P1 7，P2 18
- 样例类型：对抗 4，应拒绝 7，模糊 9，简单 11
- 五维覆盖：性能 5，成本 6，认知 12，责任 13，质量 10
- 不可自动拒绝：7 条
已冻结：31 条，sha256=c99624dbf113…，版本 v1,v2
下一步：重新部署评估器 Lambda，让新黄金集生效（见 evaluators/README.md）
```

自己确认一下改了什么（实验目录是一个 git 仓库）：

```bash
git status --short data/ evaluators/
```

:::alert{header="冻结之后，谁会用到它" type="info"}
- 黄金集答案是**打包进评估器 Lambda** 的。第 4 章第一次注册评估器时会自动带上这一版。如果你已经做过第 4 章、回头再补样本，必须重跑 `./40-create-evaluators.sh`，否则评估用的还是旧黄金集。
- 新写了需求时，Agent 的相似需求工具读的是**打包进 Runtime** 的需求反馈。第 3 章部署时会自动带上；已经部署过的话，重跑 `./30-deploy-runtime.sh`。
- 从现在开始到第 5 章结束，**不要再改黄金集**：V0 和 V1 要用同一把尺子量，分数才可比。
:::

下一步：[2.5 讨论：如果要 P0/P1/P2 分级](/02-define/25-discussion)
