# AgentDLC 动手实验：需求价值打分 Agent · 评估驱动动手实验

> 所有数据均为合成数据（synthetic）。本仓库是一次 Agent DLC workshop 的动手实验交付物。

**操作手册**：Workshop Studio 版（中文，主线手册）在 [`workshop-studio/content/`](workshop-studio/content/)；
[`docs/`](docs/index.html) 是早先的 GitHub Pages 版（只做了脚本改名，内容以 Workshop Studio 版为准，之后会由它重新生成）。

## 实验内容

场景是**企业软件平台的需求评审**（样例平台：合成的“示例 CRM PaaS”）：一个需求反馈分诊 Agent 对每条需求反馈判断
**是否重要（是/否）**、**是否紧急（是/否）**、**处理建议（迭代/拒绝/待评估）**，拒绝时给出专业理由和对客户的沟通话术。

实验按 AgentDLC 六环飞轮推进：定义 → 构建 → 评估 → 发布 → 观测 → 回流，中心是黄金标准（黄金集 + 五维判据）。
主线只有一条：mock agent 部署在 Amazon Bedrock AgentCore Runtime 上，用 AgentCore Evaluations 评估 V0 → 优化出 V1 → 再评估对比。
可选扩展：自托管 Agent 埋点接入（ADOT）、已有日志接入、自带 Agent 接入指南、用自己的场景画鱼骨图、标签方案切换、按讨论意见重新生成模拟数据。
把你们自己的 Agent 接入同一套评估：手册扩展《自带 Agent 接入指南》（旧版 [`docs/own-agent-guide.html`](docs/own-agent-guide.html)）。

| 脚本 | 章 | 作用 |
|------|----|------|
| `00-config.sh` | — | 共用配置（被其它脚本 `source`，不是步骤） |
| `10-check-env.sh` · `11-enable-observability.sh` | 1 进入实验环境 | 检查环境 · 开启 Transaction Search（WS 的 CloudFormation 已运行） |
| `30-deploy-runtime.sh` | 3 构建 | 部署 / 调用 / 查看 Runtime |
| `40-create-evaluators.sh` · `41-online-eval.sh` · `42-run-golden.sh` · `43-run-eval.sh` · `44-failures.sh` | 4 评估 | 注册评估器 · 在线评估 · 跑黄金集 · 按需评估 · 失败归因 |
| `50-switch-version.sh` · `51-compare.sh` | 5 优化 | 切换版本（v0 / v1 / v1-ref）· 前后对比页 |
| `60-triage-report.sh` | 6 发布与观测 | 上月 60 条需求的分诊报告 |
| `99-cleanup.sh` | 9 清理 | 删除实验资源 |
| `ext-*.sh` | 扩展 | 自托管 Agent、日志转换、遥测检查、标签方案切换 |

脚本编号的十位数就是手册的章号；同一个脚本在后面的章节会再用到（例如第 5 章重新运行 `30-deploy-runtime.sh`）。
只有一条主线时不需要选择运行位置；扩展用 `--mode local`（EC2 上自托管的 mock agent），默认是 `--mode runtime`。

## 发布前检查清单（维护者）

1. （只用于 `docs/` 的 GitHub Pages 版）`./tools/set-repo-url.sh https://github.com/<owner>/<name>` 替换仓库地址占位符。
2. 确认 `config/lab.env` 的 `AWS_REGION` 与 workshop 区域一致（EC2 上会自动跟随实例所在区域）。
3. 在实验账号提前开通 Anthropic 模型访问；不可用时 `./tools/use-model.sh nova`。
4. （GitHub Pages 版）GitHub → Settings → Pages：Branch `main`，folder `/docs`。
5. 只在专用实验账号中部署 `ec2/adlc-ec2.yaml`。

AWS Workshop Studio 交付（不依赖 GitHub）：见 [`workshop-studio/README.md`](workshop-studio/README.md)，实验包由 `./tools/build-ws-bundle.sh` 构建。

## License

MIT-0，见 [LICENSE](LICENSE)。
