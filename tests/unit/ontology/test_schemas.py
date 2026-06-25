from sales_agent.ontology.schemas import EntityCandidate, FactCandidate, EvidenceItem


def test_entity_candidate_defaults():
    entity = EntityCandidate(type="Product", name="福利卡")
    assert entity.aliases == []
    assert entity.properties == {}
    assert entity.confidence == 0.8


def test_fact_candidate_relation_shape():
    fact = FactCandidate(
        subject_name="福多多",
        predicate="produces",
        object_name="福利卡",
        evidence=[EvidenceItem(excerpt="福多多提供福利卡", locator="doc.md#1")],
    )
    assert fact.fact_type == "relation"
    assert fact.status == "active"
    assert fact.evidence[0].excerpt == "福多多提供福利卡"
