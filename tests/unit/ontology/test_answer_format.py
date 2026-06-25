from sales_agent.ontology.answer_service import ontology_answer_to_sections


def test_ontology_answer_to_sections_keeps_summary():
    result = ontology_answer_to_sections(
        {"answer": "福多多的核心优势是供应稳定。", "evidence": ["证据A"], "confidence": 0.9}
    )
    assert result["summary"] == "福多多的核心优势是供应稳定。"
    assert result["sections"][0]["title"] == "依据摘要"
    assert "证据A" in result["sections"][0]["content"]
