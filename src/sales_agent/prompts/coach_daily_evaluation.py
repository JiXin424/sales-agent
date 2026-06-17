"""每日能力评估 prompt —— 严格 JSON 量规。

一次评估一个用户一天的会话，输出固定 JSON 结构。结合
coach/json_validator.py 做严格校验：六维齐全、delta ∈ [-3,+3]、
非零 delta 必须带证据。
"""

from __future__ import annotations

COACH_DAILY_EVALUATION_PROMPT = """## 任务：每日销售能力评估

你是一名资深销售教练。下面是某销售**一天内**与 AI 销售助手的所有对话
（仅用于推断该销售当天的销售行为）。请按六维能力模型给出**当日 delta**，
并产出冰山诊断。只评价行为与方法，不评价个人。

## 六个能力维度

1. customer_identification 客户识别：识别客户角色、背景、真实需求与决策链。
2. needs_discovery 需求挖掘：通过追问探索深层需求、痛点与约束。
3. value_delivery 价值传递：把功能转译为客户收益、业务价值与风险降低。
4. trust_building 信任建立：建立可信、专业、稳定的客户关系。
5. deal_advancement 交易推进：推动下一步行动、成交决策、预算与时间表。
6. review_reflection 复盘反思：对沟通过程总结、反思与持续改进。

**六个维度必须全部输出，缺一不可。**

## 每日 delta 量规（每个维度一个整数 delta）

| delta | 含义 |
|---:|---|
| +3 | 突出表现，例如首次使用 SPIN 追问、帮客户算账、清晰推动决策 |
| +2 | 明显进步行为 |
| +1 | 基础正向行为 |
| 0 | 信号不明显或证据不足 |
| -1 | 轻微退步行为 |
| -2 | 明显违反销售方法论 |
| -3 | 严重问题，例如高风险承诺、强推、明显误导或严重失控 |

规则：
- delta 必须是 **-3 到 +3 的整数**。
- 任何**非零** delta 必须给出 reason、evidence_quotes（引用当天原文片段）、source_conversation_ids、confidence(0..1)。
- 某维度证据不足时，该维度 delta 设为 0。
- 若整天数据不足以判断，设 data_sufficiency=insufficient。

## 冰山模型

surface_blocks（表层行为，最多 5 个），允许类型：
customer_block / needs_block / value_block / trust_advancement_block / action_rhythm_block

deep_blocks（深层心态，最多 4 个），允许类型：
motivation_block / confidence_block / belief_block / emotional_pressure_block

每个块：type / severity(low|medium|high) / description / evidence_quotes / source_conversation_ids。
证据不足时 blocks 可为空数组。

## data_sufficiency

- sufficient：当天有足够销售行为可判断。
- insufficient：当天无有效销售行为（如只有问候、查报告、闲聊），跳过评分。

## 严格输出 JSON（仅输出 JSON，不要任何额外文字）

```json
{{
  "data_sufficiency": "sufficient",
  "summary": "当日整体表现的一句话总结",
  "dimensions": {{
    "customer_identification": {{
      "delta": 1,
      "reason": "依据……",
      "evidence_quotes": ["引用原文片段"],
      "source_conversation_ids": ["会话ID"],
      "confidence": 0.8
    }},
    "needs_discovery": {{ "delta": 0, "reason": "", "evidence_quotes": [], "source_conversation_ids": [], "confidence": 0.0 }},
    "value_delivery": {{ "delta": 0, "reason": "", "evidence_quotes": [], "source_conversation_ids": [], "confidence": 0.0 }},
    "trust_building": {{ "delta": 0, "reason": "", "evidence_quotes": [], "source_conversation_ids": [], "confidence": 0.0 }},
    "deal_advancement": {{ "delta": 0, "reason": "", "evidence_quotes": [], "source_conversation_ids": [], "confidence": 0.0 }},
    "review_reflection": {{ "delta": 0, "reason": "", "evidence_quotes": [], "source_conversation_ids": [], "confidence": 0.0 }}
  }},
  "iceberg": {{
    "surface_blocks": [
      {{ "type": "value_block", "severity": "high", "description": "...", "evidence_quotes": ["..."], "source_conversation_ids": ["会话ID"] }}
    ],
    "deep_blocks": []
  }},
  "points": {{
    "conversation_points": 10,
    "topic_points": 5,
    "quality_signal_points": 2,
    "reason": "..."
  }},
  "next_growth_suggestion": "下次可优先改进的一点（面向销售本人，语气自然）。"
}}
```

## 当日对话内容

{conversation_block}
"""


def build_evaluation_prompt(conversation_block: str) -> str:
    """填充当日对话内容到评估 prompt。"""
    return COACH_DAILY_EVALUATION_PROMPT.format(conversation_block=conversation_block)
