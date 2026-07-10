import pytest

from sales_agent.ontology.extractor import extract_facts, parse_entities_json, parse_facts_json
from sales_agent.ontology.schemas import EntityCandidate


def test_parse_entities_json_handles_code_block():
    raw = '```json\n{"entities":[{"type":"Product","name":"福利卡","aliases":["卡"],"properties":{"price":"100"}}]}\n```'
    entities = parse_entities_json(raw)
    assert entities[0].type == "Product"
    assert entities[0].name == "福利卡"
    assert entities[0].aliases == ["卡"]


def test_parse_facts_json_handles_attribute_fact():
    raw = '{"facts":[{"subject_name":"福利卡","predicate":"price","value":"100元","fact_type":"attribute"}]}'
    facts = parse_facts_json(raw)
    assert facts[0].subject_name == "福利卡"
    assert facts[0].predicate == "price"
    assert facts[0].value == "100元"


def test_parse_facts_json_repairs_missing_comma():
    """LLM 偶发漏逗号 → json-repair 兜底修复，不再整个事实抽取失败。"""
    raw = '{"facts":[{"subject_name":"A","predicate":"p1"}{"subject_name":"B","predicate":"p2"}]}'
    facts = parse_facts_json(raw)
    assert len(facts) == 2
    assert facts[0].subject_name == "A"
    assert facts[1].subject_name == "B"


@pytest.mark.asyncio
async def test_extract_facts_chunks_entities_into_batches():
    """实体数 > batch_size 时分批调用 LLM，结果合并。"""
    calls: list[str] = []

    call_idx = 0

    class FakeChat:
        async def generate(self, messages, temperature=None, max_tokens=None, **kwargs):
            nonlocal call_idx
            calls.append(messages[0]["content"])
            # 每批返回不同 subject，避免被去重（key=subject/predicate/object）合并成 1
            subj = f"E{call_idx}"
            call_idx += 1
            return f'{{"facts":[{{"subject_name":"{subj}","predicate":"p","value":"v"}}]}}'

    entities = [EntityCandidate(type="Product", name=f"E{i}") for i in range(20)]
    facts = await extract_facts(FakeChat(), "content", entities, batch_size=8)
    # 20 实体 / 每批 8 = 3 批 → 3 次 generate
    assert len(calls) == 3
    assert len(facts) == 3  # 每批返回 1 个事实
    assert "E0" in calls[0] and "E8" not in calls[0]
    assert "E8" in calls[1]
