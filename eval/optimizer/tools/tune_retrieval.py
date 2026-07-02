"""
Tool A：检索配置调优。

输入：当前检索配置 + 低分题目详情 + 逐题指标
输出：建议的新配置值 + 理由 + 写入 DB
"""

from __future__ import annotations

import json
import logging
import os

from eval.optimizer.state import Diagnosis, MetricSnapshot, RetrievalTuning

logger = logging.getLogger(__name__)


async def tune_retrieval(
    tenant_id: str,
    diagnosis: Diagnosis,
    low_score_cases: list[MetricSnapshot],
    current_config: dict,
) -> RetrievalTuning:
    """调用 LLM 分析低分题目，建议检索配置调整。

    Args:
        tenant_id: 租户 ID
        diagnosis: 诊断结果
        low_score_cases: 低分题目详情
        current_config: 当前检索配置

    Returns:
        RetrievalTuning 调优建议
    """
    # 构建 LLM prompt
    cases_text = _format_low_score_cases(low_score_cases[:10])

    prompt = f"""你是一位检索系统调优专家。以下是一个 RAG 系统的评估结果。

## 当前检索配置
- top_k（返回 chunk 数）: {current_config.get("top_k", 5)}
- chunk_size（分块大小/字符）: {current_config.get("chunk_size", 700)}
- chunk_overlap（分块重叠/字符）: {current_config.get("chunk_overlap", 120)}

## 诊断结论
{diagnosis.reasoning}

低分题目数：{diagnosis.low_score_count}

## 低分题目详情
{cases_text}

## 任务
请根据以上信息，判断当前检索配置的问题，并给出调整建议。

分析要点：
1. 如果 ContextualRelevancy 很低（<0.3），说明检索捞回了大量无关内容
   → 可能是 chunk_size 太小导致语义碎片化，或 top_k 太大引入了噪音
   → 建议增大 chunk_size 让语义更完整，或减小 top_k
2. 如果 ContextualRecall 很低（<0.3），说明关键信息没被检索到
   → 可能是 chunk_size 太大导致关键信息被稀释，或 top_k 太小
   → 建议减小 chunk_size 或增大 top_k
3. 如果两者都低，优先调整最可能改善的单个参数（渐进式调优）

请只返回 JSON 格式，不要加额外解释：
{{
    "top_k": <int 或 null，建议的 top_k 值，不需要改则填 null>,
    "chunk_size": <int 或 null，建议值>,
    "chunk_overlap": <int 或 null，建议值>,
    "reasoning": "<调整理由，1-2 句话>",
    "expected_improvement": "<预期改善方向，如 '预计 ContextualRelevancy 从 0.1 提升到 0.4'>"
}}
"""

    # 调 LLM
    from deepeval_metrics import get_judge_model
    judge = get_judge_model()
    raw = judge.generate(prompt)

    # 解析 JSON
    if isinstance(raw, tuple):
        raw = raw[0]
    if isinstance(raw, str):
        raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 尝试提取 JSON 块
        import re
        match = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
        if match:
            data = json.loads(match.group())
        else:
            logger.warning("Failed to parse LLM output as JSON: %s", raw[:500])
            return RetrievalTuning(
                reasoning=f"LLM 输出无法解析，保持原配置。原始输出: {raw[:300]}"
            )

    return RetrievalTuning(
        top_k=data.get("top_k"),
        chunk_size=data.get("chunk_size"),
        chunk_overlap=data.get("chunk_overlap"),
        reasoning=data.get("reasoning", ""),
        expected_improvement=data.get("expected_improvement", ""),
    )


def _format_low_score_cases(cases: list[MetricSnapshot]) -> str:
    """格式化低分题目列表。"""
    lines = []
    for i, snap in enumerate(cases[:10], 1):
        lines.append(
            f"### 题目 {i}: {snap.question_id}\n"
            f"- 问题: {snap.question[:120]}\n"
            f"- ContextualRelevancy: {snap.contextual_relevancy:.2f}\n"
            f"- ContextualRecall: {snap.contextual_recall:.2f}\n"
            f"- Faithfulness: {snap.faithfulness:.2f}\n"
            f"- 参考答案: {snap.reference[:150]}\n"
        )
    return "\n".join(lines) if lines else "（无低分题目）"


async def apply_tuning(tenant_id: str, tuning: RetrievalTuning) -> bool:
    """将调优结果应用到检索配置。

    当前实现：更新 tenant config JSON 中的 retrieval 字段。
    如果 tenant 没有独立配置，则在全局 settings 中生效。
    """
    from sales_agent.core.database import get_session_factory
    from sqlalchemy import select, update
    from sales_agent.models.tenant import Tenant

    factory = get_session_factory()
    async with factory() as db:
        stmt = select(Tenant).where(Tenant.id == tenant_id)
        result = await db.execute(stmt)
        tenant = result.scalar_one_or_none()

        if tenant is None:
            logger.warning("Tenant %s not found in DB, config not persisted", tenant_id)
            return False

        config = json.loads(tenant.config_json) if tenant.config_json else {}
        retrieval = config.get("retrieval", {})

        changed = False
        if tuning.top_k is not None:
            retrieval["top_k"] = tuning.top_k
            changed = True
        if tuning.chunk_size is not None:
            retrieval["chunk_size"] = tuning.chunk_size
            changed = True
        if tuning.chunk_overlap is not None:
            retrieval["chunk_overlap"] = tuning.chunk_overlap
            changed = True

        if changed:
            config["retrieval"] = retrieval
            tenant.config_json = json.dumps(config, ensure_ascii=False)
            await db.commit()
            logger.info(
                "Applied retrieval tuning for tenant=%s: %s",
                tenant_id,
                {k: v for k, v in retrieval.items()
                 if v != ({"top_k": 5, "chunk_size": 700, "chunk_overlap": 120}).get(k)},
            )

        return changed
