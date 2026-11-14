# 手册截图清单（维护者）

页面里的截图都用 `:image[...]{src="/static/images/<文件名>"}` 引用，文件放在 `workshop-studio/static/images/`。
文件还没有时，Workshop Studio 页面上显示为图片缺失（构建不会失败）；`tests/test_ws_handbook.py` 检查每个引用都在这张清单里。
飞轮图（`flywheel-*.svg`）不是截图，由 `python3 tools/ws_flywheel.py` 生成。截图前把账号 ID、实例 ID 以外的个人信息遮掉；PNG，宽度 1600 px 左右。

| 文件名 | 页面 · 步骤 | 截图里必须看得到 | 需要的状态 / 在哪里截 |
|---|---|---|---|
| `01-event-outputs.png` | 1 · 1.1 | Event Outputs 表里的 `SessionManagerUrl`、`FirstCommand`、`InstanceId` | Workshop Studio 测试活动的活动页面底部；没有测试活动时先用 CloudFormation 控制台 → 堆栈 `adlc-lab` → **输出** 代替，正式活动前替换 |
| `02-fishbone-start.png` | 2.2 · 开始访谈 | Claude Code 里输入的开场句（背景材料 `data/scenario/role-card.md`）和主持人的第一个问题 | 实验 EC2 上 `claude`，输入 2.2 的开场句后截第一轮；截完 `/exit`（会新建一个输出目录，不影响别的步骤）。等 `adlc-fishbone` 的通用版合并后再截 |
| `02-fishbone-preview.png` | 2.2 · 发布预览 | 鱼骨图网页：五个方面 + 其他，便签文字清楚 | S3 控制台 → 桶 `adlc-ws-<账号>-<区域>` → `reports/define/第3组-2026-11-14/fishbone.html` → **打开** |
| `02-criteria-preview.png` | 2.3 · 签字与预览 | 判据表网页：维度、档位、阈值、执行方式、覆盖（含“部分覆盖”） | 同一前缀下的 `criteria.html` → **打开** |
| `03-runtime-console.png` | 3 · 3.4 | Runtime `adlc_ws_agent`：状态 Ready、版本号、DEFAULT 端点、环境变量 `AGENT_VERSION` | Amazon Bedrock AgentCore 控制台 → **Runtime** → `adlc_ws_agent` |
| `03-trace-triage-span.png` | 3 · 3.4 | 一个 session 的调用树（`adlc.triage` → `invoke_agent` → `chat` / `execute_tool …`），右侧 `adlc.triage` 的属性里有 `adlc.triage_output` | CloudWatch → GenAI Observability → Bedrock AgentCore → Agents → `adlc_ws_agent` → Sessions → 任一 session → Traces |
| `04-online-eval-tab.png` | 4 · 4.6 | Evaluations 页签：各评估器的平均分和分布（V0 那次运行的时间段） | 同上 Agent 页 → **Evaluations**，时间范围选 V0 黄金集运行前后（本次实测：2026-11-14 13:47 UTC 起约 5 分钟） |
| `05-claude-v1-summary.png` | 5 · 5.2 | Claude Code 写完 V1 后的终端总结：失败原因 → 修改 → 预期影响的指标 | 实验 EC2 上运行 5.2 的命令，交互式确认后截最后一屏（会覆盖 `v1.md`，不影响已经生成的对比页） |
| `05-compare-top.png` | 5 · 5.5 | 对比页顶部：两个版本的结论、红线条数、各门禁前后值和 70% 门槛 | S3 控制台 → `reports/compare-runtime-v0-…-vs-runtime-v1-ref-….html` → **打开**（V0 → V1 参考版；也可截 V0 → V1） |
| `05-compare-changes.png` | 5 · 5.5 | ③ 改了什么：提示词差异、工具变化、“失败原因 → 修改 → 预期影响的指标”表 | 同一对比页，滚到 ③ |
| `05-compare-flips.png` | 5 · 5.5 | ④ 效果验证：错 → 对、对 → 错的条数，按字段 / 按五维 | 同一对比页，滚到 ④ |
| `06-online-eval-v1.png` | 6 · 6.2 | Evaluations 页签，时间范围是 V1 参考版那次运行，平均分明显高于 V0 | 同 `04-online-eval-tab.png`，时间范围本次实测：2026-11-14 14:03 UTC 起约 5 分钟 |
| `06-triage-report.png` | 6 · 6.3 | 分诊报告顶部：迭代 / 拒绝 / 待评估条数、重要 × 紧急矩阵 | S3 控制台 → `reports/triage-report-runtime-v1-ref-….html` → **打开** |
