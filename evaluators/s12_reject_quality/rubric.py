"""Chinese rubric for judging rejection reasons + customer-facing replies (话术).

Single source for both judges:
  - code-based Lambda judge (judge.py, asks the model for a JSON verdict),
  - native AgentCore LLM-as-a-judge config (evaluators/llm_judge_config/s12_reject_quality.json,
    rendered by evaluators/sync_shared.py; no output-format text there because the
    service appends its own).
"""
from __future__ import annotations

DIMENSIONS = [
    ("A", "理由专业性", "内部拒绝理由（reject_reason）准确归类拒绝依据（平台已支持 / 超出平台定位 / 单客户定制 / 违背安全合规或多租户原则 / 技术不可行），"
                    "引用具体的平台能力、限制或原则，事实正确，不编造平台没有的功能。"),
    ("B", "理解与礼貌", "话术（customer_reply）先理解并认可客户的业务诉求，语气专业、礼貌、真诚，不推诿、不说教、不贬低客户，可以直接发给客户。"),
    ("C", "原因说明", "话术用客户听得懂的语言说明平台为什么不做（立场 + 原因），与内部理由一致，不含糊其辞。"),
    ("D", "替代方案", "给出具体、可执行的替代方案或后续路径：已有功能的入口与用法、自定义组件/自定义函数/开放接口/集成连接器、实施顾问或伙伴支持、需求反馈渠道等。"),
    ("E", "风险控制", "话术（customer_reply）不承诺具体上线时间或排期，不泄露内部信息（能力编号、研发排期、其他客户名称），不给出与安全合规冲突的建议。内部理由里引用能力编号是正常的，不算泄露。"),
]

RED_FLAGS = [
    "编造平台并不存在的功能或配置路径",
    "承诺具体上线时间或“下个版本一定做”",
    "泄露其他客户名称或内部敏感信息",
    "话术为空，或与内部理由互相矛盾",
    "拒绝了不应自动拒绝的需求（数据丢失/错误、安全漏洞、合规监管、合同承诺、线上功能缺陷）",
]

PASS_THRESHOLD = 0.7

GRADES = [  # value, label, definition (shared by both judges)
    (1.0, "优秀", "理由准确专业并引用平台能力或原则；话术理解客户、原因清楚、替代方案具体可执行；无任何红线问题。可直接发给客户。"),
    (0.8, "良好", "整体专业可用，只有一处小缺点（如替代方案不够具体、语气略生硬），无红线问题。"),
    (0.6, "一般", "理由或话术有明显不足之一：理由泛泛、未引用平台能力；或缺少替代方案；或原因没讲清。需要人工修改后才能发出。"),
    (0.4, "较差", "理由与话术多处不足，或触发任意一条红线问题（如编造功能、承诺上线时间、话术与理由矛盾）。"),
    (0.2, "不合格", "没有理由或话术、理由错误，或拒绝了不应自动拒绝的需求（数据丢失、安全、合规、合同承诺、线上缺陷）。"),
]
NOT_APPLICABLE = (0.0, "不适用", "Agent 的处理建议不是“拒绝”（是迭代或待评估），本评估器不适用。仅用于标记，不代表质量差。")


def dimension_text() -> str:
    return "\n".join(f"{k}. {name}：{desc}" for k, name, desc in DIMENSIONS)


def red_flag_text() -> str:
    return "\n".join(f"- {r}" for r in RED_FLAGS)


ROLE = ("你是 CRM PaaS 平台的资深产品负责人兼客户成功专家，负责审核“需求价值评审 Agent”对客户需求给出的拒绝理由和对客户的沟通话术。"
        "你的标准是：产品与研发都认可这个拒绝是有专业依据的，客户读完话术后愿意接受并知道下一步怎么做。")


def judge_system_prompt() -> str:
    return ROLE


def judge_user_prompt(requirement: str, reject_reason: str, customer_reply: str, reasoning: str = "",
                      expected_reject_points=(), expected_reply_points=(), capability_hints: str = "") -> str:
    """Prompt for the code-based judge: returns a JSON verdict (parsed by judge.py)."""
    gold = ""
    if expected_reject_points or expected_reply_points:
        gold = ("\n【黄金集参考要点（产研标注，供你判断要点覆盖，不要求逐字一致）】\n"
                + ("拒绝理由要点：\n" + "\n".join(f"- {p}" for p in expected_reject_points) + "\n" if expected_reject_points else "")
                + ("话术要点：\n" + "\n".join(f"- {p}" for p in expected_reply_points) + "\n" if expected_reply_points else ""))
    caps = f"\n【平台能力目录摘录】\n{capability_hints}\n" if capability_hints else ""
    return f"""请审核下面这条被 Agent 判为“拒绝”的客户需求。

【客户需求】
{requirement}

【Agent 的判断依据（reasoning）】
{reasoning or '（无）'}

【Agent 的内部拒绝理由（reject_reason）】
{reject_reason or '（空）'}

【Agent 给客户的话术（customer_reply）】
{customer_reply or '（空）'}
{gold}{caps}
【评分维度（每项 1~5 分，5 分最好）】
{dimension_text()}

【红线问题（出现任意一条即列入 red_flags）】
{red_flag_text()}

只输出一个紧凑的 JSON 对象（不要换行缩进、不要输出其它文字），字段顺序如下：
{{"scores": {{"A": 1-5, "B": 1-5, "C": 1-5, "D": 1-5, "E": 1-5}}, "red_flags": ["每条不超过 20 字，没有则为空数组"], "comment": "不超过 60 字，指出最需要改进的一点", "missing_points": ["最多 3 条，每条不超过 20 字"], "covered_points": ["最多 3 条，每条不超过 20 字"]}}"""


def llm_judge_instructions() -> str:
    """Instructions for the native AgentCore LLM-as-a-judge evaluator (TRACE level).

    Uses only {context} and {assistant_turn} so it can be attached to an online evaluation config.
    No output-format instructions: the service appends its own (reason, then score).
    """
    grades = "\n".join(f"- {v}（{label}）：{d}" for v, label, d in GRADES)
    na = f"- {NOT_APPLICABLE[0]}（{NOT_APPLICABLE[1]}）：{NOT_APPLICABLE[2]}"
    return f"""{ROLE}

下面是一次需求评审的完整上下文（包含客户需求原文、Agent 查询平台能力目录的工具调用和结果）以及 Agent 的最终输出。
Agent 的最终输出是一个 JSON，其中 decision 是处理建议，reject_reason 是内部拒绝理由，customer_reply 是给客户的话术。

【上下文】
{{context}}

【Agent 最终输出】
{{assistant_turn}}

【评审步骤】
1. 先看 Agent 最终输出里的 decision：如果不是“拒绝”（例如“迭代”或“待评估”），直接给“不适用”档，不再评价。
2. 如果是“拒绝”，对照上下文中的需求原文和平台能力目录工具结果，按下面五个维度评价 reject_reason 和 customer_reply：
{dimension_text()}
3. 检查红线问题，出现任意一条，最高只能给“较差”；拒绝了不应自动拒绝的需求，给“不合格”：
{red_flag_text()}

【评分档位】
{grades}
{na}
"""


def rating_scale() -> dict:
    items = [{"value": v, "label": label, "definition": d} for v, label, d in GRADES]
    items.append({"value": NOT_APPLICABLE[0], "label": NOT_APPLICABLE[1], "definition": NOT_APPLICABLE[2]})
    return {"numerical": items}
