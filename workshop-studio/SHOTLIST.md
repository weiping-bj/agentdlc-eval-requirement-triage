# 手册截图清单（维护者）

页面里的截图都用 `:image[...]{src="/static/images/<文件名>"}` 引用，文件放在 `workshop-studio/static/images/`。
文件还没有时，Workshop Studio 页面上显示为图片缺失（构建不会失败）；`tests/test_ws_handbook.py` 检查每个引用都在这张清单里。
飞轮图（`flywheel-*.svg`）不是截图，由 `python3 tools/ws_flywheel.py` 生成。截图前遮掉账号 ID、实例 ID、ARN 里的账号段和其他个人信息；
PNG，宽度不超过 1600 px（原图更宽时等比缩小）。现有截图来自 2026-11-14 在测试账号里的一次完整实测（区域 us-west-2）。

| 文件名 | 页面 · 步骤 | 状态 | 截图里看得到 | 在哪里截 / 需要的状态 |
|---|---|---|---|---|
| `01-event-outputs.png` | 1 · 1.1 | 已拍（替身） | 堆栈输出 `FirstCommand`、`InstanceId`、`SessionManagerUrl` 等 | 现在是 CloudFormation → 堆栈 `adlc-lab` → **输出** 页；正式活动前换成 Workshop Studio 活动页面底部的 **Event Outputs** |
| `ec2-console-connect.png` | 1 · 1.2（展开块） | 已拍 | EC2 实例列表，选中 `adlc-ws`，红框标出 **Connect** | EC2 控制台 → **实例** |
| `ec2-console-SSM.png` | 1 · 1.2（展开块） | 已拍 | Connect to instance：选中 **SSM Session Manager**，Ping status Online | 上一张点 Connect 之后 |
| `02-fishbone-start.png` | 2.2 · 开始访谈 | **未拍** | Claude Code 里输入 2.2 的开场句（背景材料 `data/scenario/role-card.md`）和主持人的第一个问题（带进度行） | 实验 EC2 上 `claude`；用通用版 `adlc-fishbone` |
| `02-fishbone-preview.png` | 2.2 · 发布预览 | **未拍** | 鱼骨图网页：五个方面 + 其他，便签文字清楚 | S3 控制台 → 桶 `adlc-ws-<账号>-<区域>` → `reports/define/<目录>/fishbone.html` → **打开** |
| `02-criteria-preview.png` | 2.3 · 签字与预览 | **未拍** | 判据表网页：维度、档位、阈值、执行方式、覆盖 | 同一前缀下的 `criteria.html` → **打开** |
| `03-runtime-console-Configuration.png` | 3 · 3.4 | 已拍 | Runtime `adlc_ws_agent`：Status Active、Versioned configurations、版本说明 | AgentCore 控制台 → **Runtime** → `adlc_ws_agent` → **Configuration**；本图是部署 V1 参考版之后（Version 3，说明 v1-ref） |
| `03-runtime-console-Endpoints.png` | 3 · 3.4 | 已拍 | DEFAULT 端点，Associated version | 同上 → **Endpoints** |
| `03-trace-triage-span.png` | 3 · 3.4 | 已拍 | Trajectory 全链路；右侧 `adlc.triage` 的 JSON 里有 `adlc.triage_output` | CloudWatch → GenAI Observability → Bedrock AgentCore → Agents → `adlc_ws_agent` → Sessions → 一个 session → Traces；先 **Clear filters** 再选 `adlc.triage`。本图是 V1 参考版处理上月需求的 session |
| `04-online-eval-tab-Others.png` | 4 · 4.6 | 已拍 | 自定义评估器卡片和柱状图 | 同上 Agent 页 → **Evaluations**。V0 重跑：session 2026-11-14 16:57:40–16:59:53 UTC，分数写入 17:06:17–17:11:16 UTC；范围 2026-11-14T16:50Z 到 2026-11-14T17:10Z（北京时间 2026-11-15T00:50+08:00 到 2026-11-15T01:10+08:00），2 分钟一根柱子。卡片：decision 0.49 · urgent 0.9 · important 0.69 · accuracy 0.69 · reject_quality 0.26。这张图里 Pass 红、Fail 绿，以图例为准 |
| `04-online-eval-tab-Builtin.png` | 4 · 4.6 | 已拍 | Builtin.Helpfulness 卡片和柱状图 | 同上；卡片 Helpfulness 0.83。第一次的 V0 运行没有能单独框出的范围（它的分数写入时，V1 的 session 已经开始） |
| `05-claude-v1-summary.png` | 5 · 5.2 | 已拍 | Claude Code 写完 V1 后的总结：失败原因 → 修改 → 预期影响的指标、这一轮不改的、下一步 | 实验 EC2 上运行 5.2 的命令，截最后一屏 |
| `05-compare-top-open.png` | 5 · 5.5 | 已拍 | S3 对象页，红框标出 **Open** | S3 控制台 → 对比页对象 |
| `05-compare-top.png` | 5 · 5.5 | 已拍 | 对比页顶部：两个版本的结论、红线、门禁前后值、两次运行 | 对比页 V0 → V1 参考版 → **打开** |
| `05-compare-changes.png` | 5 · 5.5 | 已拍（待重拍） | ③ 改了什么：提示词、工具、改动说明表 | 同一对比页 ③；图里改动说明第一行还是旧写法（`v1-ref.changes.md` 已改），下次重拍时一并更新 |
| `05-compare-flips.png` | 5 · 5.5 | 已拍 | ④ 效果验证：错 → 对、对 → 错，按字段 / 按五维 | 同一对比页 ④ |
| `06-online-eval-v1-Others.png` | 6 · 6.2 | 已拍 | 自定义评估器卡片和柱状图，只含 V1 参考版黄金集运行 | V1 参考版 session 2026-11-14 14:03:31–14:05:09 UTC（它的分数 14:11:17 UTC 以后才写入；范围里有更早运行写入的分数，所以能显示）；范围 2026-11-14T13:58Z 到 2026-11-14T14:08Z（北京时间 2026-11-14T21:58+08:00 到 2026-11-14T22:08+08:00）；卡片 decision 0.98 · urgent 0.96 · important 0.73 · accuracy 0.89 · reject_quality 0.86 |
| `06-online-eval-v1-Builtin.png` | 6 · 6.2 | 已拍 | Builtin.Helpfulness 卡片和柱状图 | 同上；卡片 Helpfulness 0.84 |
| `06-triage-report.png` | 6 · 6.3 | 已拍 | 分诊报告顶部：迭代 14 / 拒绝 27 / 待评估 19、重要 × 紧急矩阵 | S3 控制台 → `reports/triage-report-runtime-v1-ref-….html` → **打开** |
