---
name: adlc-criteria
description: AgentDLC 定义环第二步“障碍 → 判据 → 黄金集”：读 adlc-fishbone 产出的 fishbone-data.json，给每条已确认的障碍写一条判据（主语是 Agent、谓语可观测），定维度、档位（红线 / 门禁 / 观测）、执行方式（代码断言 / 裁判 / 内置评估器 / 指标读数），算出实验现有评估器是否已覆盖；对能靠补样本补上的缺口，提议 1–3 条黄金样本，预览并经业务方同意后用实验自带的工具写入。用户说“判据”“判据表”“criteria”“障碍转判据”“补黄金集”“鱼骨图之后”时使用。约 10 分钟判据 + 5–10 分钟黄金样本。
---

# 障碍 → 判据 → 黄金集（workshop 版）

鱼骨图回答“什么会挡住上线”；这一步把每条障碍翻译成**判据**——一条可打分的、主语是 Agent、谓语可观测的要求——并说清楚它将来由谁来判、实验里现在有没有人在判。
判据表要业务方（扮演业务方的那组学员）签字。所有命令都在**实验仓库根目录**运行。

确定性的部分都在脚本里（可测试）：判据规则、覆盖判定（对照本目录的 `evaluator-coverage.json`）、黄金集编号校验、黄金样本的预览与试运行。你负责判断：判据怎么写、选哪个档位、映射到哪些检查、哪些黄金样本能检验它。

## 判据规则（`criteria.py build` 会逐条检查）

1. **主语是 Agent，谓语可观测**：写“Agent 不把功能缺陷类反馈判为拒绝”，不写“让客户满意”“客户觉得专业”。
2. **一条障碍一条判据**：每条已确认的障碍（包括没入图的备选）都要有，且只有一条。
3. **档位**：
   - **红线**：违反一次就是事故，0 违例，不打分，`threshold` 留空（`null`）。例：误拒不可自动拒绝的需求、话术泄露其他客户名称。
   - **门禁**：达到阈值才放行，写清阈值。例：处理建议准确率 ≥ 0.70（角色卡里领导定的目标）。
   - **观测**：只看趋势，不进结论，阈值写“只看趋势”或一个参考值。
4. **执行方式**（按成本从低到高选，能用代码判的永远不交给裁判）：
   - **代码断言**：和黄金集逐字段比对、字段是否为空、规则 / 名单匹配。
   - **裁判**（LLM-as-a-Judge）：主观但有评分标准的部分。**红线不能用裁判判**（裁判有翻转率，判不成一票否决）；裁判作门禁前要先校准。
   - **内置评估器**：`Builtin.Correctness`、`Builtin.Helpfulness`。
   - **指标读数**（不是评估器）：**成本和性能维一律用它**，从 trace / 运行记录读（`latency_ms`、`gen_ai.usage.*` token、处理失败条数、处理建议分布），不交给评估器打分。
5. **覆盖**（脚本按 `evaluator-coverage.json` 算，不要自己判）：
   - **已覆盖**：现有评估器就检查这条，档位和执行方式都够，黄金集里也有能检验它的样本；
   - **部分覆盖**：只查到一部分——现有实现是裁判而判据要代码断言、档位不够、只有读数没有阈值，或者**评估器能判但黄金集里还没有这类样本**（这种可以靠补黄金样本补上）；
   - **未覆盖**：现有评估器和读数都不查它，要新写评估器或断言。

## 流程

### 第 0 步：找到鱼骨图（不提问）

```bash
ls -td outputs/fishbone/*/ | head -5
```

用刚做完的那个目录（有多个、拿不准时问一句“用哪一个？”）。目录里要有 `fishbone-data.json` 且 `status` 是 `approved_for_render`（脚本会检查）；没有就先用 `adlc-fishbone` skill。鱼骨图改过之后，判据表要重新 build（实验的发布工具会比对两者）。
判断是不是**实验场景**（需求反馈分诊 Agent，业务方用的是角色卡 `data/scenario/role-card.md`）：鱼骨图 `metadata.background` 里有 `role-card.md`，`use_case` 也是需求反馈分诊；拿不准就问一句。是实验场景就运行：

```bash
python3 .claude/skills/adlc-criteria/scripts/criteria.py hints
```

它打印角色卡每类坑对应的能力目录、需求反馈和黄金集条目（只给你用，不要念给业务方）。

### 第 1 步：草稿骨架（不提问）

```bash
python3 .claude/skills/adlc-criteria/scripts/criteria.py skeleton outputs/fishbone/<目录>
```

它写 `criteria-draft.json`（每条已确认障碍一条空判据，已存在就不覆盖），并打印可选的检查（`catalog`）和当前黄金集（`golden`）。
草稿里的 `scenario` 填 `lab`（实验场景）或 `own`（小组自己的场景）；只有 `lab`、并且鱼骨图的背景材料里有实验角色卡时，脚本才接受第 4 步的黄金样本。没问到的方面（鱼骨图 `not_asked`）没有障碍，也就没有判据。

### 第 2 步：写判据（不提问，约 3–5 分钟）

逐条填 `criteria-draft.json` 的 `criteria`：

| 字段 | 怎么填 |
|------|--------|
| `text` | “Agent …”开头，谓语落在 Agent 的输出或 trace 上；用便签和 `evidence` 里的数字（例如“整批 60 条 30 分钟内完成”“每条模型费用 ≤ 0.5 元”） |
| `dimension` | 认知 / 质量 / 责任 / 成本 / 性能（确实不属于五维才写“其他”）。按**判据本身**定，通常和鱼骨图上的方面一致；鱼骨图放错了方面（例如把“一条反馈四个诉求漏掉后面几个”放进了性能），判据照样按判据本身定（这条是质量），在 `note` 里写明；要改鱼骨图就回 adlc-fishbone 第 3 步，读回并再次确认后重新生成，然后 `skeleton --force`（或把这条的 `obstacle_id` 改成新位置）再 build。成本维只留给费用、人力读数，性能维只留给延迟、吞吐、稳定性、可用性读数 |
| `tier` / `threshold` | 按上面的档位规则；红线写 `null` |
| `executor` | 代码断言 / 裁判 / 内置评估器 / 指标读数；成本、性能只能是指标读数 |
| `checks` | 从 `criteria.py catalog` 里选**确实检查这个谓语**的检查编号；一个都不对就写 `[]`（未覆盖），不要硬凑 |
| `golden_ids` | 能检验这条判据的现有黄金样本（`criteria.py golden`），要满足检查的条件（例如 `misreject` 只对“不可自动拒绝”的样本起作用）；没有就写 `[]` |
| `golden_gap` | 可选：现有样本有，但没覆盖障碍里的具体情形时写一句（例如“黄金集里的缺陷样本都是严重故障，没有‘看起来很小’的缺陷”）；覆盖会记为部分覆盖，可以补黄金样本 |
| `note` | 可选：一句话说明，例如“菜单路径可改成对照能力目录的代码断言” |

然后：

```bash
python3 .claude/skills/adlc-criteria/scripts/criteria.py build outputs/fishbone/<目录>
```

有错误就按提示改草稿再 build，直到退出码 0。它写 `criteria.json`（以它为准）和 `criteria.md`。

### 第 3 步：业务方签字（1–2 轮）

把判据表压缩成一张表读给他们（ID、判据、档位、阈值、覆盖），只问一个问题：

> 这是从你们的鱼骨图翻译出来的判据表。档位和阈值是按你们说的数字定的。你们签字吗？要改哪条的档位或阈值就说。

有修改：改草稿 → build → 再读一次改动的那几条确认。

### 第 4 步：补黄金样本（约 5–10 分钟，只用于实验场景）

`build` 会列出“可以靠补黄金样本补上缺口的判据”。从中挑最重要的（红线优先），提议 **1–3 条**黄金样本：

1. 先看上个月已有、还不在黄金集里的需求反馈（优先用它们，不用新写）：

   ```bash
   python3 .claude/skills/adlc-criteria/scripts/criteria.py candidates
   ```

   它带出种子库的标注；标签按 `data/golden/LABELING_GUIDE.md` 判断，和种子标注不一致时要有理由。
2. 已有条目都不合适时才新写一条需求反馈（`new_feedback`）：客户名用“XX（示例）”，不写真实人名、手机号、邮箱；要写 `category`（`gap_generic` / `existing_capability` / `customer_specific` / `out_of_scope` / `infeasible_or_redline` / `vague` / `incident` / `high_value_hard` / `adversarial` / `compliance_contract`）。编号留空，脚本按当前编号续上。
3. 写进 `criteria-draft.json` 的 `golden_proposals`：

   ```json
   {"criterion": "C-02", "feedback_id": "FB-2610-048",
    "why": "黄金集里的缺陷样本都是严重故障，没有“看起来很小”的缺陷",
    "expected": {"important": "否", "urgent": "否", "decision": "迭代", "level": "P2"},
    "acceptable_decisions": ["迭代"], "can_auto_reject": false,
    "dimensions": ["责任"], "case_type": "简单", "reject_points": [], "reply_points": [],
    "rationale": "线上功能缺陷不可自动拒绝；影响小、有临时办法，不重要不紧急。"}
   ```

   新写的条目把 `feedback_id` 换成 `"category": "incident", "new_feedback": {"title": …, "content": …, "customer": "…（示例）", "industry": …, "customer_tier": …, "channel": …, "module": …, "submitted_at": "2026-10-xx", "feedback_type": …, "contract_commitment": "否"}`。
   黄金样本的 `dimensions` 按 `LABELING_GUIDE.md` 第 7 节打（实验样例标签的口径：“性能”含长输入 / 多诉求），和判据的 `dimension` 是两套口径，不要互相改。
   判“拒绝”的样本必须写 `reject_points` 和 `reply_points`（话术要点至少覆盖：理解诉求、说明立场和原因、给出替代方案）。
   `can_auto_reject` 必须明确写 `true` / `false`（功能缺陷一律 `false`）；列表字段写字符串列表。`matched_capabilities` 不会写进黄金集（填写模板没有这一列），所以补样本补不了“能力目录命中率”。
4. 再 `build`：它按 `golden_tool.py apply-feedback` 的规则在内存里试运行，通过后写出**预览**（不动 `data/`）：
   `golden-proposals.csv`（`golden-set-feedback.csv` 格式，操作 = 新增）、新写条目的 `extra-feedback.proposed.jsonl`（`extra-feedback.jsonl` 格式），`criteria.md` 末尾有提议表和写入步骤。
5. 把提议读给业务方（每条：需求标题、期望标签、为什么补），并**跳出角色、对小组里的技术同学明确说**（这句对业务方角色没有意义）：
   “写进黄金集后要重新运行 `./40-create-evaluators.sh`：黄金集是打包在评估器 Lambda 里的，不重新部署，评估用的还是旧黄金集。”（新写了需求反馈时再加一句：还要重新运行 `./30-deploy-runtime.sh`，Agent 的相似需求工具读的是打包进 Runtime 的需求反馈。）
   然后只问一个问题：“要把这 N 条写进黄金集吗？”
6. **同意后**才按 `criteria.md` 的“写入步骤”逐条执行，任何一步报错就停下来报告：
   - 有新写条目时：把 `extra-feedback.proposed.jsonl` 的每一行追加到 `data/templates/extra-feedback.jsonl`，按提示改 `data/templates/mock-data-params.yaml` 的 `count`，运行 `python3 data/tools/generate_mock_data.py`；
   - `criteria.py verify <目录> --before-apply`（只查需求编号都在需求反馈里）→ `golden_tool.py apply-feedback --feedback <目录>/golden-proposals.csv --dry-run` → 去掉 `--dry-run` 再运行 → `golden_tool.py freeze` → `criteria.py verify <目录>`（这次要全部 ✅）；
   - 在 `criteria-draft.json` 里把这几条提议从 `golden_proposals` 挪到 `golden_applied`（留作记录），把新黄金编号（verify 会打印）填进对应判据的 `golden_ids`，删掉已补上的 `golden_gap`，再 `build`；
   - 提醒（或经他们同意后运行）`./40-create-evaluators.sh`。
   不同意：什么都不写，预览文件留在输出目录里即可。

自己的场景（草稿 `scenario` 是 `own`）：判据表照做（多半是“未覆盖”，这正是要新写评估器的清单），**跳过第 4 步**——实验的黄金集和评估器是需求分诊 Agent 的数据契约。
设置了 `ADLC_DATA_DIR`（Agent 改读另一个数据目录）时也跳过第 4 步，告诉讲师。

### 第 5 步：交付

用 `build` 最后打印的“输出目录”和文件列表，告诉他们：输出目录的**绝对路径**，每个文件一行（绝对路径 + 它是什么）；三档分布、已覆盖 / 部分覆盖 / 未覆盖各几条；未覆盖的就是后面要新写的评估器或断言；补了黄金样本的话，提醒重新部署评估器（`./40-create-evaluators.sh`）。
不要自己发布网页预览：实验手册会告诉大家用哪个工具、怎么把这个目录的路径填进去。

## 常见问题

- **把“话术红线”判成裁判**：规则不允许红线用裁判。能用代码判的部分（菜单路径对照能力目录、其他客户名单、承诺上线时间的说法）写成代码断言；实验现在靠裁判判，所以覆盖是“部分覆盖”——这是如实的结论，不是错误。
- **成本 / 性能想用评估器**：一律“指标读数”；实验记录了 `latency_ms` 和 token 用量，但没有阈值判定，所以是“部分覆盖”。
- **一条障碍想拆成好几条判据**：这一步每条障碍只写一条（把最能检验它的那条写具体）；拆分留到构建环。
- **黄金样本标签拿不准**：按 `LABELING_GUIDE.md`；模糊样例可以给多个可接受答案（`acceptable_decisions`），但别滥用。
