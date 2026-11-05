# 给 coding agent 的提示词（工具中立）

这些提示词可以直接粘贴给任意 coding agent（Claude Code、Kiro、Cursor、Q Developer 等）使用，前提是它能在仓库根目录读写文件、执行命令。用法：

1. 在 EC2 上进入仓库根目录，启动 coding agent。
2. 把对应 `.md` 的全文粘贴给 coding agent，或者告诉它：“请阅读并执行 `data/prompts/03-update-golden-set.md`”。
3. coding agent 会读取模板，运行脚本，然后汇报改了什么。**请人工看一眼汇报里的分布和差异再继续。**

| 提示词 | 什么时候用 | 读取的模板 |
|------|------|------|
| [01-assess-customer-data.md](01-assess-customer-data.md) | 客户带来了自己的需求数据 | 客户文件 + `column-mapping.example.json` |
| [02-regenerate-mock-data.md](02-regenerate-mock-data.md) | 讨论后要调整 mock 数据 | `mock-data-params.yaml`、`discussion-feedback.md` 第 5 节 |
| [03-update-golden-set.md](03-update-golden-set.md) | 讨论后要调整黄金集（最常用） | `discussion-feedback.md` 第 1、2、4 节、`golden-set-feedback.csv` |
| [04-update-capability-catalog.md](04-update-capability-catalog.md) | 补充或纠正平台能力目录 | `discussion-feedback.md` 第 3 节 |
| [05-switch-label-scheme.md](05-switch-label-scheme.md) | 从“是/否”切换到“P0/P1/P2”分级 | `discussion-feedback.md` 第 1.1 节 |

所有脚本只依赖 python3 标准库，模板默认在 `data/templates/`。
