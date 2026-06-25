from __future__ import annotations

import json
import re
from typing import Any

from sales_agent.llm.base import ChatModel
from sales_agent.ontology.schemas import EntityCandidate, EvidenceItem, FactCandidate


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
            return json.loads(match.group(0))
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

实体：
{entities_json}

文档：
{content}
"""


async def extract_entities(chat_model: ChatModel, content: str) -> list[EntityCandidate]:
    raw = await chat_model.generate(
        messages=[{"role": "user", "content": ENTITY_EXTRACTION_PROMPT.format(content=content[:6000])}],
        temperature=0.1,
        max_tokens=3000,
    )
    return parse_entities_json(raw)


async def extract_facts(
    chat_model: ChatModel,
    content: str,
    entities: list[EntityCandidate],
) -> list[FactCandidate]:
    entities_json = json.dumps(
        [{"type": e.type, "name": e.name, "aliases": e.aliases, "properties": e.properties} for e in entities],
        ensure_ascii=False,
    )
    raw = await chat_model.generate(
        messages=[{"role": "user", "content": FACT_EXTRACTION_PROMPT.format(content=content[:6000], entities_json=entities_json)}],
        temperature=0.1,
        max_tokens=4000,
    )
    return parse_facts_json(raw)
