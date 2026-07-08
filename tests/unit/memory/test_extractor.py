import pytest

from sales_agent.services.memory.extractor import extract_memory_candidates


class FakeModel:
    async def generate(self, messages, temperature, max_tokens):
        joined = "\n".join(m["content"] for m in messages)
        assert "assistant_answer" not in joined
        return """
        {
          "candidates": [
            {
              "memory_type": "user_fact",
              "normalized_key": "sales_region",
              "content": {"key": "sales_region", "value": "华东区"},
              "evidence_text": "我负责华东区",
              "source_kind": "inferred_user",
              "stability": "stable",
              "sensitivity": "normal",
              "confidence_band": "candidate"
            }
          ]
        }
        """


@pytest.mark.asyncio
async def test_extract_candidates_uses_only_user_evidence():
    candidates = await extract_memory_candidates(
        user_message="我负责华东区",
        topic_summary="用户在讨论区域负责范围",
        verified_tool_facts=[],
        chat_model=FakeModel(),
    )

    assert len(candidates) == 1
    assert candidates[0].source_kind == "inferred_user"
    assert candidates[0].normalized_key == "sales_region"


class BadModel:
    async def generate(self, messages, temperature, max_tokens):
        return "不是 JSON"


@pytest.mark.asyncio
async def test_parse_failure_returns_empty_candidates():
    assert await extract_memory_candidates(
        user_message="我负责华东区",
        topic_summary="",
        verified_tool_facts=[],
        chat_model=BadModel(),
    ) == []
