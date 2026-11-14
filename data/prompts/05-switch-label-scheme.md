# 任务：把标签方案从“是/否”切换为“P0/P1/P2 分级”

业务方决定用优先级分级（`discussion-feedback.md` 第 1.1 节勾选了 B，或在讨论中口头决定）。请完成切换。

## 步骤
1. 读取 `discussion-feedback.md` 第 1.1 节和 1.2 节（没有填就按默认）：
   - 等级怎么算对：完全一致 → `--tolerance 0`（默认）；相差一级也算对 → `--tolerance 1`；
   - P0、P1、P2 各自的口径。默认推导：重要且紧急 = P0，重要不紧急 = P1，其它 = P2。
2. 先把版本切回 V0（新口径下要重新跑 V0 基线）：`./50-switch-version.sh v0`。再预览、执行一条命令（它依次改：标准 `config/local.env` → 黄金集补优先级并冻结 → 重新部署评估器（多一个 `label_level`）→ 重新部署 Runtime（提示词要求输出 `level`）→ 重新启用在线评估）：
   ```bash
   ./ext-switch-label-scheme.sh level --tolerance <0|1> --dry-run
   ./ext-switch-label-scheme.sh level --tolerance <0|1>
   ```
3. 如果讨论给的 P0/P1/P2 口径与默认推导不同：
   1. 逐条复核黄金集的 `expected.level`。
   2. 把需要改的写进 `data/templates/golden-set-feedback.csv`（操作=修改，填“优先级”列）。
   3. 运行 `python3 data/tools/golden_tool.py --scheme level apply-feedback --dry-run` 预览，再去掉 `--dry-run` 运行。
   4. 再运行一次 `./ext-switch-label-scheme.sh level --tolerance <0|1>`（每一步都可以重复运行），让评估器用上改过的黄金集。
   5. 把现场口径写进 `data/golden/LABELING_GUIDE.md` 第 6 节和 `agent/adlc_agent/prompts/level-scheme.md`，最后一条命令会把新提示词部署到 Runtime。
4. 告诉我：哪些文件变了（`git diff --stat`）、门禁多了“优先级准确率”，下一步是 `./42-run-golden.sh` 和 `./43-run-eval.sh`；切换前后的评估结果不能直接比较。
   切回“是/否”：`./ext-switch-label-scheme.sh binary`。
