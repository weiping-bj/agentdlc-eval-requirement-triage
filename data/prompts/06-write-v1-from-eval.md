# 任务：根据 V0 的评估结果，写出我们自己的 V1（手册第 5.2 步）

你在实验目录 `~/adlc-lab` 中工作。目标：让“需求反馈分诊 Agent”通过发布标准——先消除红线（误拒不可自动拒绝的需求、拒绝话术红线，一条都不能有），再让“是否重要 / 是否紧急 / 处理建议”三项准确率和拒绝话术通过率都达到 70% 以上。这一轮只**修 Agent**（提示词和工具选择），不改评估体系。

请按顺序完成：

1. 运行 `./44-failures.sh`，并读取 `results/` 下最新的 `eval-*.json`：
   - 先列出红线用例：`label_accuracy` 为 `CriticalFail` 的，以及 `reject_quality` 的 explanation 里有“红线：”的；
   - 再列出 `label_accuracy` 不是 `Pass` 的用例（feedback_id、期望 vs 实际），和 `reject_quality` 不是 `Pass` 的用例。
2. 对每个失败用例，到 `data/golden/golden_set.jsonl` 里找同一 feedback_id 的 `rationale`（标注理由）和 `dimensions`（五维），归纳出 3–5 条主要失败原因。逐条判断：
   - **修 Agent**：Agent 拿到了足够的信息和规则却没用对，或者工具给它的信息不够 → 这一轮改；
   - **修评估体系**：Agent 按它拿到的信息做得没错，是黄金集标注、能力目录或判据本身有问题 → 这一轮**不改**，只记下来。
   “修评估体系”只指黄金集标注、能力目录和判据，不要评价评估器本身好不好。
3. 阅读 `agent/adlc_agent/prompts/v0.md`、`agent/adlc_agent/tools.py`（里面已有的工具：`lookup_capability`、`search_capabilities`、`find_similar_requests`，看它们各自返回什么）、`data/golden/LABELING_GUIDE.md`（标注口径以它为准）和 `data/capability_catalog.json` 的结构。**不要**打开 `agent/adlc_agent/prompts/v1-ref.md` 和 `v1-ref.changes.md`：那是参考答案，留着最后对照。
4. 在 `agent/adlc_agent/prompts/v0.md` 的基础上写出 `agent/adlc_agent/prompts/v1.md`，针对第 2 步“修 Agent”的原因修改判断口径、红线和话术要求。输出的 JSON 格式保持完全不变（字段：feedback_id, important, urgent, decision, level, reasoning, reject_reason, customer_reply；level 仍为 null），并要求只输出一个 JSON 对象。
5. 在 `agent/adlc_agent/tools.py` 的 `TOOLSETS` 里增加 `"v1"`，从已有的工具函数里选（不要新写工具函数）。提示词里提到的工具必须都在这个列表里。
6. 不要修改 `data/`、`evaluators/`、`labkit/` 下的任何文件，也不要修改 `v0.md`、`v1-ref.md`、`v1-ref.changes.md`。
7. 把总结写进 `agent/adlc_agent/prompts/v1.changes.md`：第一行写“基于 v0；依据 results/eval-<run_id>.json”（换成第 1 步读的那个文件）；再用一两句话说明评估发现了什么；然后写一张 Markdown 表格，每行以 | 开头和结尾，表头为 | 失败原因（评估发现） | 修改（文件） | 预期影响的指标 |，每个“修 Agent”的原因一行；如果第 2 步有“修评估体系”的原因，最后加一节“## 这一轮不改的（修评估体系）”逐条列出。
8. 检查：运行 `python3 -c "import sys; sys.path.insert(0, 'agent'); from adlc_agent import tools; print(sorted(tools.TOOLSETS))"`，确认输出里有 `v1`。
9. 最后在终端用中文输出同样的总结（失败原因 → 修改 → 预期影响的指标），并提醒我下一步：`./50-switch-version.sh v1`，然后 `./30-deploy-runtime.sh`。
