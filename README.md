# 场景 12：需求价值打分 Agent · 评估驱动动手实验

> 所有数据均为合成数据（synthetic）。本仓库是一次 Agent DLC workshop 的动手实验交付物。

**操作手册（HTML，GitHub Pages）**：打开 [`docs/index.html`](docs/index.html)，或在 GitHub Pages 上访问（Settings → Pages → Branch `main`, folder `/docs`）。

## 实验内容

一个需求反馈分诊 Agent：对每条平台需求反馈判断 **是否重要（是/否）**、**是否紧急（是/否）**、**处理建议（迭代/拒绝/待评估）**，
拒绝时给出专业理由和对客户的沟通话术。用 Amazon Bedrock AgentCore Evaluations 评估 V0 → 优化为 V1 → 再评估对比。

两条路线：

| 路线 | 说明 |
|------|------|
| A 版 | 自有 Agent（不在 AgentCore Runtime 上）：OTel + ADOT 埋点，或已有日志 → 转换 → CloudWatch → Evaluations |
| B 版 | mock agent 部署到 AgentCore Runtime → CloudWatch → Evaluations（含在线评估） |

## 发布前（维护者）

```bash
./tools/set-repo-url.sh https://github.com/<owner>/<name>   # 替换仓库地址占位符
```

## License

MIT-0，见 [LICENSE](LICENSE)。
