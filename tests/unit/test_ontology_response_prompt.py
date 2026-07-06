"""验证 ONTOLOGY_RESPONSE_PROMPT 保留 schema + 含福多多知识规则。"""

from sales_agent.ontology.answer_service import ONTOLOGY_RESPONSE_PROMPT


def test_ontology_response_prompt_has_placeholders():
    assert "{graph_json}" in ONTOLOGY_RESPONSE_PROMPT
    assert "{question}" in ONTOLOGY_RESPONSE_PROMPT
    assert "{task_type}" in ONTOLOGY_RESPONSE_PROMPT


def test_ontology_response_prompt_has_qdrant_rules():
    """迁移后应含旧 qdrant_llm 的防火墙规则。"""
    assert "竞品防火墙" in ONTOLOGY_RESPONSE_PROMPT
    assert "跨品类防火墙" in ONTOLOGY_RESPONSE_PROMPT
    assert "专有名词精确性" in ONTOLOGY_RESPONSE_PROMPT


def test_ontology_response_prompt_has_json_schema():
    assert '"answer"' in ONTOLOGY_RESPONSE_PROMPT
    assert '"evidence"' in ONTOLOGY_RESPONSE_PROMPT
    assert '"confidence"' in ONTOLOGY_RESPONSE_PROMPT