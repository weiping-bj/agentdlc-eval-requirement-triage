# 已有 Agent 日志 → CloudWatch：日志格式（A 版第 A4 章）

把你的 Agent **每一次调用**整理成一行 JSON（JSONL 文件，UTF-8）。转换脚本
[`labkit/convert_logs.py`](../labkit/convert_logs.py) 会把每一行变成一个 session / 一条 trace，
按 Strands Agents 的 OTel GenAI 规范写入 CloudWatch，AgentCore Evaluations 就能直接评估。

| 字段 | 必填 | 说明 | 例子 |
|------|:---:|------|------|
| `session_id` | ✅ | 会话 ID（一次需求处理一个） | `sess-0001` |
| `timestamp` | ✅ | 调用开始时间，ISO 8601 或毫秒时间戳 | `2026-10-12T09:30:00+08:00` |
| `user_input` | ✅ | 发给 Agent 的完整输入（需求反馈原文） | `需求编号: FB-2610-001\n标题: ...` |
| `agent_output` | ✅ | Agent 的最终输出（文本或 JSON） | `{"important":"是",...}` |
| `feedback_id` | 推荐 | 需求编号，用来和黄金集对齐（标签准确率评估器需要） | `FB-2610-001` |
| `model` | 可选 | 使用的模型 | `my-llm-v2` |
| `latency_ms` | 可选 | 耗时（毫秒） | `8200` |
| `agent_name` | 可选 | Agent 名称 | `triage_agent` |
| `tool_calls` | 可选 | 工具调用列表：`[{"name","input","output","status"}]`，`status` 为 `success`/`error` | 见样例 |

说明：

- `agent_output` 里如果是“三项判断”JSON（键名可用 `important/urgent/decision` 或 `是否重要/是否紧急/处理建议`），
  标签准确率评估器就能打分；否则只能用内置评估器（如 Helpfulness）。
- 时间早于 14 天的日志会被自动平移到最近 1 小时内（CloudWatch 不接收过旧的 span），先后顺序不变。
- 没有 `tool_calls` 也可以导入，只是工具类评估器无法使用。
- 样例文件：[`samples/customer-agent-logs.jsonl`](samples/customer-agent-logs.jsonl)。

先检查、再发送：

```bash
./11-A-convert-logs.sh check telemetry/samples/customer-agent-logs.jsonl
./11-A-convert-logs.sh send  telemetry/samples/customer-agent-logs.jsonl
./12-A-check-telemetry.sh
```
