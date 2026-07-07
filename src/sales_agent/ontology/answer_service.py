from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Protocol

from sales_agent.llm.base import ChatModel
from sales_agent.ontology.schemas import GraphEvidence, OntologyAnswer

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def graph_evidence_to_sources(evidence: GraphEvidence) -> list[dict[str, Any]]:
    """将 GraphEvidence 转为 source dict 列表，供 hybrid 模式与 RAG sources 合并。

    source_documents 优先，为空时 fallback 到 facts_used。
    """
    raw_docs = evidence.source_documents if evidence.source_documents else evidence.facts_used
    sources: list[dict[str, Any]] = []
    for doc in raw_docs[:5]:
        sources.append({
            "document_id": doc.get("id", doc.get("name", "")),
            "title": doc.get("title") or doc.get("name", "图谱来源"),
            "display_title": doc.get("title") or doc.get("name", "图谱来源"),
            "score": evidence.confidence,
            "source_type": "ontology",
            "text": (
                doc.get("content") or doc.get("text") or doc.get("value")
                or doc.get("snippet") or doc.get("title") or doc.get("name", "")
            )[:2000],
        })
    return sources


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

## 严格规则

1. 只提取与用户问题直接相关的信息，不要堆砌。
2. 每条引用必须标注具体来源（图谱事实的 source_documents），禁止模糊表述。
3. 从图谱事实中识别约束性表述（含"必须""不能""禁止""前提是"等词），作为回答底线。
4. 多个事实矛盾时，指出矛盾并给出判断。
5. 无相关事实时坦诚说明，不要编造。
6. 不要重复图谱事实中已包含的完整表述，提炼要点即可。
7. 🚫 图谱中没有的具体数字（笔数、百分比、时长、金额、服务费率等），一律不得写入回答。
8. 🚫 禁止跨文档推测：某文档中的产品名/卡种/规则不能自动套用到另一产品/场景。
9. 🚫 禁止编造具体人名、联系方式、内部文档名。
10. 🚫 图谱中没有明确记载的信息，即使在其他上下文中看似合理，也绝对禁止写入。宁缺毋滥。
11. **专有名词精确性**：品牌名、平台名、产品名、公司名必须原样引用，不得替换为同类竞品或近义词。
12. **竞品防火墙**：来源含"竞对""对比""竞品"等字样的事实，仅用于描述竞争对手，禁止用于描述福多多自身。
13. **跨品类防火墙**：同一竞品不同品类价格政策可能不同，严禁跨品类套用。

## 图谱证据

{graph_json}

## 用户问题

{question}

任务类型：{task_type}

## 输出 JSON

{{"answer":"自然语言回答","evidence":["使用的事实或来源"],"confidence":0.8}}
"""


# 图谱证据送入 LLM 时的上限（防止超 context window）
# 默认值在 OntologyConfig 中定义，运行时可通过配置调整
def _max_entities_for_prompt() -> int:
    from sales_agent.core.config import get_settings
    return get_settings().ontology.max_entities_for_prompt

def _max_facts_for_prompt() -> int:
    from sales_agent.core.config import get_settings
    return get_settings().ontology.max_facts_for_prompt


# 中文问题分词：按常见虚词/功能词切分，提取有意义的检索词
_SPLIT_FUNCTION_WORDS = re.compile(r'[的了呢吗啊吧是有哪些什么怎么在哪去]')


def _extract_question_terms(question: str) -> list[str]:
    """从中文问题中提取有意义的检索词（按虚词切分 + 过滤空白）。

    例如 "全品c的产品的冷饮有哪些品牌" → ["全品c", "产品", "冷饮", "品牌"]
    """
    terms = [t.strip() for t in _SPLIT_FUNCTION_WORDS.split(question) if t.strip()]
    return terms or [question]  # 兜底：如果切分后为空，用原始问题


def _compact_evidence(evidence: GraphEvidence, question: str = "") -> dict[str, Any]:
    """压缩图谱证据到 LLM prompt 可接受的大小。

    实体和事实各保留前 N 条，优先保留与问题关键词相关的事实。
    每条只保留 name/type/predicate/value 等关键字段。
    """
    # 从问题中提取有意义的检索词（词级匹配，不再是字符级）
    q_terms = _extract_question_terms(question) if question else []

    def _relevance(f: dict) -> int:
        """计算事实与问题的相关性得分（词级子串匹配）。"""
        score = 0
        fv = str(f.get("value", "")) + str(f.get("predicate", ""))
        for term in q_terms:
            if term in fv:
                score += len(term)  # 长词匹配权重更高
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
        for f in sorted_facts[:_max_facts_for_prompt()]
    ]

    entities = [
        {"name": e.get("name", ""), "type": e.get("type", "")}
        for e in evidence.matched_entities[:_max_entities_for_prompt()]
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
    def __init__(
        self,
        retrieval: RetrievalProtocol,
        chat_model: ChatModel,
        *,
        db: AsyncSession | None = None,
        tenant_id: str | None = None,
        agent_id: str | None = None,
    ):
        self.retrieval = retrieval
        self.chat_model = chat_model
        self.db = db
        self.tenant_id = tenant_id
        self.agent_id = agent_id

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
                占位符）。为 None 时通过 ``PromptRegistry`` 解析
                ``knowledge/ontology_response``（db/tenant_id 在 ``__init__`` 注入时
                生效）；DB 未配置时回退到内置 :data:`ONTOLOGY_RESPONSE_PROMPT`。
        """
        if prompt_text is None:
            from sales_agent.services.prompt_resolver_helper import resolve_knowledge_prompt
            prompt_text = await resolve_knowledge_prompt(
                self.db,
                "ontology_response",
                self.tenant_id,
                self.agent_id,
                default=ONTOLOGY_RESPONSE_PROMPT,
                graph_json=json.dumps(
                    _compact_evidence(graph_evidence, question=message),
                    ensure_ascii=False,
                ),
                question=message,
                task_type=task_type,
            )
            rendered_prompt = prompt_text
        else:
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
        # 构建 sources：优先用 source_documents，为空则用 facts_used 兜底
        raw_docs = graph_evidence.source_documents if graph_evidence.source_documents else graph_evidence.facts_used
        sources = [
            {
                "document_id": doc.get("id", doc.get("name", "")),
                "title": doc.get("title") or doc.get("name", "图谱来源"),
                "display_title": doc.get("title") or doc.get("name", "图谱来源"),
                "score": graph_evidence.confidence,
                "source_type": "ontology",
                "text": (
                    doc.get("content") or doc.get("text") or doc.get("value")
                    or doc.get("snippet") or doc.get("title") or doc.get("name", "")
                )[:2000],
            }
            for doc in raw_docs[:5]
        ]
        return OntologyAnswer(answer=answer, sources=sources, graph_evidence=graph_evidence)
