from sales_agent.ontology.extractor import parse_entities_json, parse_facts_json


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
