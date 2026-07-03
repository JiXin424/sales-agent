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
    engine_type: str = "legacy_rag",
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
    # 构建 LLM prompt（根据引擎类型选择不同调优策略）
    cases_text = _format_low_score_cases(low_score_cases[:10])

    if engine_type == "ontology_neo4j":
        prompt = _build_ontology_tuning_prompt(current_config, diagnosis, cases_text)
    else:
        prompt = _build_rag_tuning_prompt(current_config, diagnosis, cases_text)

    # 调 LLM
    from eval.deepeval_metrics import get_judge_model
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
        entity_limit=data.get("entity_limit"),
        facts_per_entity=data.get("facts_per_entity"),
        max_entities_for_prompt=data.get("max_entities_for_prompt"),
        max_facts_for_prompt=data.get("max_facts_for_prompt"),
        vector_fallback_top_k=data.get("vector_fallback_top_k"),
        reasoning=data.get("reasoning", ""),
        expected_improvement=data.get("expected_improvement", ""),
    )


def _build_rag_tuning_prompt(current_config: dict, diagnosis: "Diagnosis", cases_text: str) -> str:
    """RAG 引擎的调优 prompt——调整 chunk_size / chunk_overlap / top_k。"""
    return f"""你是一位检索系统调优专家。以下是一个 RAG 系统的评估结果。

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
    "top_k": <int 或 null>,
    "chunk_size": <int 或 null>,
    "chunk_overlap": <int 或 null>,
    "reasoning": "<调整理由，1-2 句话>",
    "expected_improvement": "<预期改善方向>"
}}
"""


def _build_ontology_tuning_prompt(current_config: dict, diagnosis: "Diagnosis", cases_text: str) -> str:
    """Ontology 引擎的调优 prompt——调整实体匹配 / 图遍历 / 向量回退参数（均为运行时参数，立即生效）。"""
    return f"""你是一位知识图谱检索系统调优专家。以下是一个基于 Neo4j 知识图谱（Ontology）的 RAG 系统评估结果。

## 当前 Ontology 检索配置
- entity_limit（Cypher 查询返回的最多实体数）: {current_config.get("entity_limit", 15)}
- facts_per_entity（每个实体取前 N 条事实）: {current_config.get("facts_per_entity", 20)}
- max_entities_for_prompt（最终塞给 LLM 的最多实体数）: {current_config.get("max_entities_for_prompt", 10)}
- max_facts_for_prompt（最终塞给 LLM 的最多事实数）: {current_config.get("max_facts_for_prompt", 25)}
- vector_fallback_top_k（图查询无结果时的向量回退条数）: {current_config.get("vector_fallback_top_k", 5)}

## 诊断结论
{diagnosis.reasoning}

低分题目数：{diagnosis.low_score_count}

## 低分题目详情
{cases_text}

## 任务
Ontology 知识图谱检索的核心调优方向：

1. **实体匹配不足**（Recall 低但 Relevancy 正常）：Cypher 返回的实体太少，漏了关键信息
   → 增大 entity_limit（如 15→25），让更多候选实体进入
   → 或增大 facts_per_entity（如 20→30），让每个实体带回更多事实

2. **上下文不足**（Faithfulness 高但 Correctness 低）：给 LLM 的实体/事实太少，不足以回答
   → 增大 max_entities_for_prompt（如 10→15）
   → 或增大 max_facts_for_prompt（如 25→40）

3. **图查询完全失败，向量回退也不够**（Recall 和 Relevancy 同时为 0）
   → 增大 vector_fallback_top_k（如 5→10），让更多向量候选进入

4. **实体太多导致噪音**（Relevancy 低）：减少 entity_limit 和 max_entities_for_prompt

请只返回 JSON 格式，不要加额外解释：
{{
    "entity_limit": <int 或 null>,
    "facts_per_entity": <int 或 null>,
    "max_entities_for_prompt": <int 或 null>,
    "max_facts_for_prompt": <int 或 null>,
    "vector_fallback_top_k": <int 或 null>,
    "reasoning": "<调整理由，1-2 句话>",
    "expected_improvement": "<预期改善方向>"
}}
"""


def _format_low_score_cases(cases: list[MetricSnapshot]) -> str:
    """格式化低分题目列表（包含检索和生成两维度指标）。"""
    lines = []
    for i, snap in enumerate(cases[:10], 1):
        lines.append(
            f"### 题目 {i}: {snap.question_id}\n"
            f"- 问题: {snap.question[:120]}\n"
            f"- ContextualRelevancy: {snap.contextual_relevancy:.2f}\n"
            f"- ContextualRecall: {snap.contextual_recall:.2f}\n"
            f"- Faithfulness: {snap.faithfulness:.2f}\n"
            f"- Correctness: {snap.correctness:.2f}\n"
            f"- Agent回答: {snap.answer[:150]}\n"
            f"- 参考答案: {snap.reference[:150]}\n"
        )
    return "\n".join(lines) if lines else "（无低分题目）"


async def apply_tuning(tenant_id: str, tuning: RetrievalTuning) -> bool:
    """将调优结果应用到检索配置。

    RAG 参数写入 tenant.config_json.retrieval（chunk_size/overlap 为摄入参数，仅 top_k 运行时生效）。
    Ontology 参数写入 tenant.config_json.ontology（全部为运行时参数，下一轮立即生效）。
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
        changed = False

        # ── RAG 参数 ──
        retrieval = config.get("retrieval", {})
        if tuning.top_k is not None:
            retrieval["top_k"] = tuning.top_k
            changed = True
        if tuning.chunk_size is not None:
            retrieval["chunk_size"] = tuning.chunk_size
            changed = True
        if tuning.chunk_overlap is not None:
            retrieval["chunk_overlap"] = tuning.chunk_overlap
            changed = True
        if changed and retrieval:
            config["retrieval"] = retrieval

        # ── Ontology 参数（全部运行时立即生效） ──
        ontology = config.get("ontology", {})
        onto_changed = False
        for key in ("entity_limit", "facts_per_entity", "max_entities_for_prompt",
                     "max_facts_for_prompt", "vector_fallback_top_k"):
            val = getattr(tuning, key, None)
            if val is not None:
                ontology[key] = val
                onto_changed = True
                changed = True
        if onto_changed:
            config["ontology"] = ontology

        if changed:
            tenant.config_json = json.dumps(config, ensure_ascii=False)
            await db.commit()
            logger.info(
                "Applied tuning for tenant=%s: retrieval=%s ontology=%s",
                tenant_id,
                {k: v for k, v in retrieval.items() if k in ("top_k", "chunk_size", "chunk_overlap")},
                {k: v for k, v in ontology.items()
                 if k in ("entity_limit", "facts_per_entity", "max_entities_for_prompt",
                          "max_facts_for_prompt", "vector_fallback_top_k")},
            )

        return changed
