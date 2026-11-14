---
title: "扩展 6 按讨论意见重新生成模拟数据"
weight: 60
---

> **你在这里**：**① 定义** → ② 构建 → ③ 评估 → ④ 发布 → ⑤ 观测 → ⑥ 回流 · 圆心：黄金标准（修评估体系回路）

**用时**：约 20 分钟

主线只在第 2.4 步补了 1–3 条黄金样本。如果讨论里对**整批数据**有意见（例如“我们行业的反馈更多是集成类”“上个月其实有 200 条”），或者想逐条修改黄金集的标签，就用这个扩展：按讨论模板调整 60 条模拟需求和黄金集。这同样是“修评估体系”：改完要重新部署评估器和 Runtime，并重新跑 V0 基线。

## E6.1 把讨论结论写进模板 {#e6-1}

两份模板在 `data/templates/`：

| 模板 | 写什么 |
|---|---|
| `discussion-feedback.md` | 标签口径、不可自动拒绝的规则、能力目录补充（第 3 节）、对模拟数据的意见（第 5 节，用大白话写） |
| `golden-set-feedback.csv` | 逐条意见：“操作”列填 `保留` / `修改` / `删除` / `新增`，改标签就直接改对应列 |

两种写法任选：

::::tabs{variant="container"}
:::tab{id="claude" label="在 EC2 上让 Claude Code 改（改动小时）"}
运行 `claude`，直接说，例如：“把 `data/templates/discussion-feedback.md` 第 5 节写成：集成类的需求再多 5 条，客户行业加上新能源。”CSV 不要粘贴进对话（行多、逗号多，容易错列），逐条改标签用右边的方式。
:::
:::tab{id="s3" label="在笔记本上填好，经 S3 拷到 EC2"}
EC2 没有入站端口、不能 scp，所以用你的私有 S3 桶中转。先在 EC2 上创建（或找到）桶并打印控制台链接：

```bash
./tools/pull-templates.sh --prepare
```

在笔记本浏览器打开打印的链接，点 **上传**，把填好的 `discussion-feedback.md` 和 `golden-set-feedback.csv` 拖进去（不要改文件名）。要拿到模板原件，先在 EC2 上 `aws s3 cp data/templates/discussion-feedback.md s3://<桶名>/`、`aws s3 cp data/templates/golden-set-feedback.csv s3://<桶名>/`，再在 S3 控制台下载。回到 EC2 拷进 `data/templates/`（原文件自动备份；Excel 存成的 GBK 编码会自动转成 UTF-8）：

```bash
./tools/pull-templates.sh
```

它最后会打印按顺序要做的下一步。
:::
::::

## E6.2 改模拟数据 {#e6-2}

模板第 5 节写了意见才做（没填或写的是“基本符合”就跳到 E6.3）。交给 Claude Code：

```bash
claude "$(cat data/prompts/02-regenerate-mock-data.md)"
```

它会把意见翻译成 `data/templates/mock-data-params.yaml` 的参数（条数、月份、类别占比、行业、渠道），种子库里没有的需求类型写进 `data/templates/extra-feedback.jsonl`，运行 `python3 data/tools/generate_mock_data.py` 重新生成，最后汇报改了哪些参数、类别分布和剩下的警告。自己看一眼参数改了什么：`git diff data/templates/mock-data-params.yaml`。参数一点没改时，生成结果和实验自带的 60 条完全一样（同样的参数 + 同样的随机种子）。

## E6.3 改黄金集并冻结 {#e6-3}

先预览 CSV 里的改动（不写入）：

```bash
python3 data/tools/golden_tool.py apply-feedback --feedback data/templates/golden-set-feedback.csv --dry-run
```

再交给 Claude Code 按模板更新黄金集（它读两份模板、调用同一个脚本、校验）：

```bash
claude "$(cat data/prompts/03-update-golden-set.md)"
```

模板第 3 节补了能力目录时，先运行 `claude "$(cat data/prompts/04-update-capability-catalog.md)"`。最后冻结：

```bash
python3 data/tools/golden_tool.py freeze
```

## E6.4 让新数据生效，并重新跑基线 {#e6-4}

```bash
./40-create-evaluators.sh      # 黄金集打包在评估器 Lambda 里
./30-deploy-runtime.sh         # 需求反馈和能力目录打包在 Runtime 里
```

然后切回 V0 重新跑一遍基线（`./50-switch-version.sh v0`、`./30-deploy-runtime.sh`、`./42-run-golden.sh`、`./43-run-eval.sh`）：尺子变了，旧的 V0 分数不能再拿来对比。想在笔记本上核对当前数据，运行 `./tools/publish-data-pages.sh`（见第 2.1 步）。

## 检查一下 {#e6-check}

**这次改数据，改的是 Agent 的输入、尺子，还是两者都改了？** 改了需求反馈或能力目录 → Agent 的输入变了（重新部署 Runtime）；改了黄金集 → 尺子变了（重新部署评估器、重跑基线）。两者都改时，两件事都要做。
