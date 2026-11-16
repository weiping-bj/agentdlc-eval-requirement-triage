# 任务：按讨论结论补充或纠正平台能力目录

你是本仓库（场景 12）的数据助手。平台能力目录 `data/capability_catalog.json` 是 Agent 判断“已有能力 / 明确不做”的依据。讨论中客户补充和纠正了一些能力。

## 输入
- `data/templates/discussion-feedback.md` 第 3 节：能力补充表与纠错。
- `data/capability_catalog.json`：结构为 `{"platform": {...}, "capabilities": [...]}`。每个能力有以下字段：
  - `id`：已有 `CAP-xxx`，明确不做的是 `OOS-xxx`；
  - `name`、`module`；
  - `status`：已支持 / 部分支持 / 规划中 / 明确不做；
  - `description`、`limits`、`workaround`；
  - `effort_hint`：低 / 中 / 高；
  - `keywords`（数组）、`how_to`。

## 步骤
1. 按第 3 节逐行处理：
   - 已存在的能力：修改字段；
   - 新能力：追加。编号接着现有最大编号往后排，明确不做的用 `OOS-`。
   - `keywords` 写 3~6 个客户可能使用的说法，Agent 的检索靠它命中。
2. 能力的状态变化可能让黄金集标签失效，例如某能力从“部分支持”改为“已支持”后，相关样例应改为拒绝。
   1. 在 `data/golden/golden_set.jsonl` 的 `matched_capabilities` 中查找引用了被修改能力的条目，列给我。
   2. 建议的标签修改写进 `data/templates/golden-set-feedback.csv`（操作=修改），然后按 03 号提示词执行。
3. 运行 `python3 data/tools/golden_tool.py validate`（会检查黄金集引用的能力编号是否存在），再运行 `python3 evaluators/sync_shared.py`（同时重新生成网页版 `docs/catalog.html` 和 Excel 版 `data/capability_catalog.csv`）。
4. 汇报：新增 / 修改了哪些能力，影响了哪些黄金集样例。并提醒：Agent（B 版需重新部署 Runtime）和评估器 Lambda 都要重新部署，才能用上新目录。

## 约束
- 只写平台通用能力，不写某个客户的私有配置。
- 保持 JSON 合法，用 2 空格缩进，中文不转义。
- `docs/catalog.html`、`data/capability_catalog.csv` 是生成文件，不要手改；只改 JSON，再运行上面的 `sync_shared.py`。
