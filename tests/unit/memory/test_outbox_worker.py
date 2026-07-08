import json
import pytest

from sales_agent.services.memory.outbox_worker import process_outbox_payload


class FakeRepo:
    def __init__(self):
        self.stored = []
        self.activated = []

    async def corroborate_candidate(self, scope, candidate, conversation_id, message_id, now=None):
        self.stored.append((scope.user_id, candidate.normalized_key, conversation_id, message_id))
        return type("Result", (), {"status": "success"})()


class FakeModel:
    async def generate(self, messages, temperature, max_tokens):
        return json.dumps({
            "candidates": [{
                "memory_type": "user_fact",
                "normalized_key": "sales_region",
                "content": {"key": "sales_region", "value": "华东区"},
                "evidence_text": "我负责华东区",
                "source_kind": "inferred_user",
                "stability": "stable",
                "sensitivity": "normal",
                "confidence_band": "candidate"
            }]
        }, ensure_ascii=False)


@pytest.mark.asyncio
async def test_process_outbox_payload_stores_candidate_without_user_reply():
    repo = FakeRepo()
    result = await process_outbox_payload(
        repo=repo,
        chat_model=FakeModel(),
        payload={
            "tenant_id": "t1",
            "agent_id": "a1",
            "user_id": "u1",
            "conversation_id": "conv1",
            "message_id": "event1",
            "user_message": "我负责华东区",
            "topic_summary": "",
            "verified_tool_facts": [],
        },
    )

    assert result.candidate_count == 1
    assert repo.stored == [("u1", "sales_region", "conv1", "event1")]
