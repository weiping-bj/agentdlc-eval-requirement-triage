# s12_reject_quality：拒绝理由与话术质量（代码型 LLM 评审，TRACE）

- **Lambda handler**：`lambda_function.handler`。整个目录 zip 部署，依赖 boto3（Lambda 运行时自带）。
- **执行角色权限**：见 `execution-role-policy.json`。
- **文件分工**：

  | 文件 | 作用 |
  |------|------|
  | `judge.py` | 算法：评审调用、分数换算、模型回退 |
  | `rubric.py` | 中文评分标准，原生 LLM-as-a-judge 配置也由它生成 |
  | `span_adapter.py` | 适配层 |
  | `golden_set.json` | 打包的黄金集（含拒绝要点、话术要点） |
  | `capability_catalog.json` | 能力目录，给评审模型做事实依据 |

- **模型和返回值**：见 [../README.md](../README.md) 第 4 节。
