# s12_label_accuracy：三项标签准确率（代码型评估器，TRACE）

- **Lambda handler**：`lambda_function.handler`。整个目录 zip 部署，只用标准库，不需要 Bedrock 权限。
- **文件分工**：

  | 文件 | 作用 |
  |------|------|
  | `label_scoring.py` | 算法 |
  | `span_adapter.py` | 适配层（从 `shared/` 同步） |
  | `golden_store.py` | 黄金集加载（从 `shared/` 同步） |
  | `golden_set.json` | 打包的黄金集 |
  | `batch_score.py` | 本地批量打分 |

- **返回值和环境变量**：见 [../README.md](../README.md) 第 3 节。
