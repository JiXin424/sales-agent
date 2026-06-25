from __future__ import annotations

import json
from typing import Any, Protocol

from sales_agent.llm.base import ChatModel
from sales_agent.ontology.schemas import GraphEvidence, OntologyAnswer


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
    ) -> OntologyAnswer:
        graph_evidence = await self.retrieval.retrieve(
            tenant_id=tenant_id,
            agent_id=agent_id,
            question=message,
        )
        raw = await self.chat_model.generate(
            messages=[{
                "role": "user",
                "content": ONTOLOGY_RESPONSE_PROMPT.format(
                    graph_json=json.dumps(graph_evidence.to_dict(), ensure_ascii=False),
                    question=message,
                    task_type=task_type,
                ),
            }],
            temperature=0.2,
            max_tokens=1600,
        )
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
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
