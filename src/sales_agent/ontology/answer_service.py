from __future__ import annotations

import json
import re
from typing import Any, Protocol

from sales_agent.llm.base import ChatModel
from sales_agent.ontology.schemas import GraphEvidence, OntologyAnswer


def _parse_json(raw: str) -> dict[str, Any]:
    """Parse LLM JSON output, handling markdown code fences and common defects."""
    text = raw.strip()
    # 剥离 ```json ... ``` 包裹
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 尝试匹配首个 JSON 对象
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        # json_repair 兜底
        try:
            from json_repair import repair_json
            repaired = repair_json(text, return_objects=True)
            if isinstance(repaired, dict):
                return repaired
        except Exception:  # noqa: BLE001
            pass
        raise


def ontology_answer_to_sections(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert ontology answer JSON to sales-agent summary/sections."""
    answer = str(raw.get("answer", "")).strip()
    evidence = raw.get("evidence") if isinstance(raw.get("evidence"), list) else []
    sections: list[dict[str, str]] = []
    if evidence:
        sections.append({
            "title": "依据摘要",
            "content": "\n".join(f"- {item}" for item in evidence if item),
        })
    confidence = raw.get("confidence")
    if confidence is not None:
        sections.append({"title": "可信度", "content": str(confidence)})
    return {"summary": answer, "sections": sections}


class RetrievalProtocol(Protocol):
    async def retrieve(self, *, tenant_id: str, agent_id: str | None, question: str) -> GraphEvidence: ...


ONTOLOGY_RESPONSE_PROMPT = """你是销售知识图谱回答器。基于图谱事实回答用户问题，不要编造。

图谱证据：
{graph_json}

用户问题：{question}
任务类型：{task_type}

输出 JSON：
{{"answer":"自然语言回答","evidence":["使用的事实或来源"],"confidence":0.8}}
"""


# 图谱证据送入 LLM 时的上限（防止超 context window）
_MAX_ENTITIES_FOR_PROMPT = 10
_MAX_FACTS_FOR_PROMPT = 25


def _compact_evidence(evidence: GraphEvidence, question: str = "") -> dict[str, Any]:
    """压缩图谱证据到 LLM prompt 可接受的大小。

    实体和事实各保留前 N 条，优先保留与问题关键词相关的事实。
    每条只保留 name/type/predicate/value 等关键字段。
    """
    # 从问题中提取关键词用于相关性排序
    q_terms = set(question) if question else set()

    def _relevance(f: dict) -> int:
        """计算事实与问题的相关性得分。"""
        score = 0
        fv = str(f.get("value", "")) + str(f.get("predicate", ""))
        for ch in q_terms:
            if ch in fv:
                score += 1
        return score

    # 按相关性排序事实，取前 N
    sorted_facts = sorted(evidence.facts_used, key=_relevance, reverse=True)
    facts = [
        {
            "subject": f.get("subject_name", f.get("name", "")),
            "predicate": f.get("predicate", ""),
            "object": f.get("object_name", ""),
            "value": str(f.get("value", ""))[:200],
        }
        for f in sorted_facts[:_MAX_FACTS_FOR_PROMPT]
    ]

    entities = [
        {"name": e.get("name", ""), "type": e.get("type", "")}
        for e in evidence.matched_entities[:_MAX_ENTITIES_FOR_PROMPT]
    ]

    return {
        "ontology_intent": evidence.ontology_intent,
        "entities": entities,
        "facts": facts,
        "source_documents": [d.get("title", "") for d in evidence.source_documents[:5]],
        "confidence": evidence.confidence,
    }


def render_response_prompt(
    graph_evidence: GraphEvidence,
    message: str,
    task_type: str,
    prompt_text: str | None = None,
) -> str:
    """渲染图谱回答 prompt 的完整字符串。

    Args:
        prompt_text: 自定义 prompt 模板。为 None 时使用 ONTOLOGY_RESPONSE_PROMPT。
    """
    template = prompt_text or ONTOLOGY_RESPONSE_PROMPT
    compact = _compact_evidence(graph_evidence, question=message)
    return template.format(
        graph_json=json.dumps(compact, ensure_ascii=False),
        question=message,
        task_type=task_type,
    )


class OntologyAnswerService:
    def __init__(self, retrieval: RetrievalProtocol, chat_model: ChatModel):
        self.retrieval = retrieval
        self.chat_model = chat_model

    async def answer_for_task(
        self,
        *,
        tenant_id: str,
        agent_id: str | None,
        task_type: str,
        message: str,
        prompt_text: str | None = None,
    ) -> OntologyAnswer:
        graph_evidence = await self.retrieval.retrieve(
            tenant_id=tenant_id,
            agent_id=agent_id,
            question=message,
        )
        return await self.generate_answer(
            graph_evidence=graph_evidence,
            message=message,
            task_type=task_type,
            prompt_text=prompt_text,
        )

    async def generate_answer(
        self,
        *,
        graph_evidence: GraphEvidence,
        message: str,
        task_type: str,
        prompt_text: str | None = None,
    ) -> OntologyAnswer:
        """基于已检索到的图谱证据生成结构化答案。

        与 `answer_for_task` 分离，便于流式端点在「检索后、生成前」推送检索过程，
        再调用本方法完成回答。

        Args:
            prompt_text: 自定义 prompt 模板（含 {graph_json}/{question}/{task_type}
                占位符）。为 None 时使用内置 ONTOLOGY_RESPONSE_PROMPT。
        """
        rendered_prompt = render_response_prompt(
            graph_evidence, message, task_type,
            prompt_text=prompt_text,
        )
        raw = await self.chat_model.generate(
            messages=[{"role": "user", "content": rendered_prompt}],
            temperature=0.2,
            max_tokens=1600,
        )
        try:
            parsed = _parse_json(raw)
        except (json.JSONDecodeError, ValueError):
            parsed = {"answer": raw, "evidence": [], "confidence": graph_evidence.confidence}
        answer = ontology_answer_to_sections(parsed)
        sources = [
            {
                "document_id": doc.get("id", ""),
                "title": doc.get("title", "图谱来源"),
                "display_title": doc.get("title", "图谱来源"),
                "score": graph_evidence.confidence,
                "source_type": "ontology",
            }
            for doc in graph_evidence.source_documents[:3]
        ]
        return OntologyAnswer(answer=answer, sources=sources, graph_evidence=graph_evidence)
