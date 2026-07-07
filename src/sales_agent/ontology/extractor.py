from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING, Any

from sales_agent.core.exceptions import ModelFailedError
from sales_agent.llm.base import ChatModel
from sales_agent.ontology.schemas import EntityCandidate, EvidenceItem, FactCandidate

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# LLM 提取最大重试次数
_MAX_EXTRACTION_RETRIES = 3


def _extract_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        # 最后兜底：用 json-repair 修复 LLM 常见 JSON 瑕疵（缺逗号、尾逗号、未闭合引号等）
        try:
            from json_repair import repair_json
            repaired = repair_json(text, return_objects=True)
            if isinstance(repaired, dict):
                return repaired
        except Exception:  # noqa: BLE001
            pass
        raise


def _evidence_items(items: list[dict[str, Any]] | None) -> list[EvidenceItem]:
    return [
        EvidenceItem(
            excerpt=str(item.get("excerpt", "")),
            locator=str(item.get("locator", "")),
            confidence=float(item.get("confidence", 0.8)),
            extraction_method=str(item.get("extraction_method", "llm")),
        )
        for item in (items or [])
        if item.get("excerpt")
    ]


def parse_entities_json(raw: str) -> list[EntityCandidate]:
    data = _extract_json(raw)
    return [
        EntityCandidate(
            type=str(item.get("type", "Concept")),
            name=str(item.get("name", "")).strip(),
            aliases=[str(a) for a in item.get("aliases", []) if a],
            properties=item.get("properties") if isinstance(item.get("properties"), dict) else {},
            confidence=float(item.get("confidence", 0.8)),
            evidence=_evidence_items(item.get("evidence")),
        )
        for item in data.get("entities", data.get("objects", []))
        if str(item.get("name", "")).strip()
    ]


def parse_facts_json(raw: str) -> list[FactCandidate]:
    data = _extract_json(raw)
    return [
        FactCandidate(
            subject_name=str(item.get("subject_name", item.get("subject", ""))).strip(),
            predicate=str(item.get("predicate", item.get("type", "related_to"))),
            object_name=(str(item.get("object_name", item.get("object", ""))).strip() or None),
            value=(str(item.get("value", "")).strip() or None),
            fact_type=str(item.get("fact_type", "relation")),
            properties=item.get("properties") if isinstance(item.get("properties"), dict) else {},
            confidence=float(item.get("confidence", 0.8)),
            status=str(item.get("status", "active")),
            risk_level=str(item.get("risk_level", "low")),
            evidence=_evidence_items(item.get("evidence")),
        )
        for item in data.get("facts", data.get("relations", []))
        if str(item.get("subject_name", item.get("subject", ""))).strip()
    ]


ENTITY_EXTRACTION_PROMPT = """你是销售知识本体抽取专家。请从文档中抽取实体。
输出 JSON：{{"entities":[{{"type":"Product","name":"实体名","aliases":[],"properties":{{}},"confidence":0.8,"evidence":[{{"excerpt":"原文片段","locator":"位置"}}]}}]}}
只抽取文档明确提到的信息。

文档：
{content}
"""

FACT_EXTRACTION_PROMPT = """你是销售知识本体抽取专家。请从文档和实体列表中抽取可审计事实。
输出 JSON：{{"facts":[{{"subject_name":"主体","predicate":"关系或属性","object_name":"客体","value":null,"fact_type":"relation","confidence":0.8,"risk_level":"low","evidence":[{{"excerpt":"原文片段","locator":"位置"}}]}}]}}
高风险事实包括价格承诺、交付承诺、资质认证、政策条款、合规边界和关键技术指标。
重点抽取：赔付标准（差价N倍赔付）、保障条款（退票补贴/买贵退差/假一赔N）、服务承诺（N天无理由/先行赔付）、价格政策等具有法律或商业约束力的承诺。
只抽取文档明确提到的信息。

实体：
{entities_json}

文档片段：
{content}
"""


async def _generate_with_retry(
    chat_model: ChatModel,
    messages: list[dict[str, str]],
    temperature: float = 0.1,
    max_tokens: int = 5000,
    max_retries: int = _MAX_EXTRACTION_RETRIES,
) -> str:
    """调用 LLM generate，带退避重试（处理服务暂时不可用等瞬时错误）。"""
    last_error: str = ""
    for attempt in range(max_retries + 1):
        try:
            return await chat_model.generate(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except ModelFailedError as exc:
            last_error = str(exc)
            if attempt < max_retries:
                delay = 2.0 * (2 ** attempt)
                logger.warning(
                    "LLM extraction failed (attempt %d/%d): %s, retrying in %.1fs",
                    attempt + 1, max_retries + 1, last_error[:120], delay,
                )
                await asyncio.sleep(delay)
            else:
                raise ModelFailedError(
                    detail=f"LLM extraction failed after {max_retries + 1} attempts: {last_error}"
                ) from exc
    # 不应到达这里
    raise ModelFailedError(detail=f"Unexpected retry loop exit: {last_error}")


async def extract_entities(
    chat_model: ChatModel,
    content: str,
    *,
    db: AsyncSession | None = None,
    tenant_id: str | None = None,
    agent_id: str | None = None,
) -> list[EntityCandidate]:
    """从文档中提取实体。长文档自动分片提取后去重合并。

    Parameters
    ----------
    db, tenant_id, agent_id :
        可选 DB 上下文，传入时走 ``PromptRegistry`` 三级回退（运营后台编辑
        ``knowledge/entity_extraction`` 生效），否则回退到模块常量
        :data:`ENTITY_EXTRACTION_PROMPT`。单测不传仍走旧路径。
    """
    chunks = _chunk_content(content)
    seen: set[str] = set()
    all_entities: list[EntityCandidate] = []

    for chunk in chunks:
        # 分片间加延迟，降低 API 瞬时并发压力
        if len(chunks) > 1:
            await asyncio.sleep(3)
        from sales_agent.services.prompt_resolver_helper import resolve_knowledge_prompt
        prompt = await resolve_knowledge_prompt(
            db,
            "entity_extraction",
            tenant_id,
            agent_id,
            default=ENTITY_EXTRACTION_PROMPT,
            content=chunk,
        )
        raw = await _generate_with_retry(
            chat_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=5000,
        )
        for entity in parse_entities_json(raw):
            key = (entity.type, entity.name)
            if key not in seen:
                seen.add(key)
                all_entities.append(entity)

    return all_entities


# 每批实体数：控制单次 LLM 输出大小，降低长 JSON 漏逗号/截断的概率。
FACT_BATCH_SIZE = 8
# 单次 LLM 提取的文档内容上限（字符数）。
# DeepSeek-chat 大 prompt 易触发限流，小分片可绕过。
MAX_CONTENT_CHARS = 8000
# 超长文档分片提取时的片长和重叠量
CHUNK_SIZE = 8000
CHUNK_OVERLAP = 300


def _chunk_content(content: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """将长文档切为重叠片段，供 LLM 分批提取。"""
    if len(content) <= MAX_CONTENT_CHARS:
        return [content]
    chunks = []
    start = 0
    while start < len(content):
        end = min(start + chunk_size, len(content))
        chunks.append(content[start:end])
        if end >= len(content):
            break
        start = end - overlap
    return chunks


async def extract_facts(
    chat_model: ChatModel,
    content: str,
    entities: list[EntityCandidate],
    batch_size: int = FACT_BATCH_SIZE,
    *,
    db: AsyncSession | None = None,
    tenant_id: str | None = None,
    agent_id: str | None = None,
) -> list[FactCandidate]:
    """按实体分批抽取事实。长文档遍历全分片，跨分片去重合并。

    原实现只取 ``chunks[0]``（首 3000 字符），导致长文档后半部分的事实
    （如零风险承诺的赔付条款）永远不会被 LLM 看到。修复后每个分片独立
    提取事实，按 (subject_name, predicate, object_name) 去重。

    Parameters
    ----------
    db, tenant_id, agent_id :
        可选 DB 上下文，传入时走 ``PromptRegistry`` 三级回退（运营后台编辑
        ``knowledge/fact_extraction`` 生效），否则回退到模块常量
        :data:`FACT_EXTRACTION_PROMPT`。单测不传仍走旧路径。
    """
    chunks = _chunk_content(content)
    seen: set[tuple[str, str, str | None]] = set()
    all_facts: list[FactCandidate] = []

    for chunk_idx, chunk in enumerate(chunks):
        # 分片间加延迟，降低 API 瞬时并发压力
        if chunk_idx > 0:
            await asyncio.sleep(3)

        for i in range(0, len(entities), batch_size):
            batch = entities[i:i + batch_size]
            # 批次间加延迟
            if i > 0:
                await asyncio.sleep(2)
            entities_json = json.dumps(
                [{"type": e.type, "name": e.name, "aliases": e.aliases, "properties": e.properties} for e in batch],
                ensure_ascii=False,
            )
            from sales_agent.services.prompt_resolver_helper import resolve_knowledge_prompt
            prompt = await resolve_knowledge_prompt(
                db,
                "fact_extraction",
                tenant_id,
                agent_id,
                default=FACT_EXTRACTION_PROMPT,
                content=chunk,
                entities_json=entities_json,
            )
            raw = await _generate_with_retry(
                chat_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=6000,
            )
            for fact in parse_facts_json(raw):
                key = (fact.subject_name, fact.predicate, fact.object_name)
                if key not in seen:
                    seen.add(key)
                    all_facts.append(fact)
    return all_facts
