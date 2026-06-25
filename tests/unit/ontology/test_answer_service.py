import pytest

from sales_agent.ontology.answer_service import OntologyAnswerService
from sales_agent.ontology.schemas import GraphEvidence


class FakeRetrieval:
    async def retrieve(self, tenant_id, agent_id, question):
        return GraphEvidence(
            ontology_intent="entity_info",
            center_entities=[{"name": "福利卡"}],
            facts_used=[{"predicate": "description", "value": "员工福利产品"}],
            confidence=0.9,
        )


class FakeChat:
    async def generate(self, messages, temperature=0.3, max_tokens=2000):
        return '{"answer":"福利卡是员工福利产品。","evidence":["description: 员工福利产品"],"confidence":0.9}'


@pytest.mark.asyncio
async def test_answer_service_returns_summary_sections():
    service = OntologyAnswerService(retrieval=FakeRetrieval(), chat_model=FakeChat())
    result = await service.answer_for_task(
        tenant_id="t1",
        agent_id="a1",
        task_type="knowledge_qa",
        message="福利卡是什么",
    )
    assert result.answer["summary"] == "福利卡是员工福利产品。"
    assert result.graph_evidence.confidence == 0.9
