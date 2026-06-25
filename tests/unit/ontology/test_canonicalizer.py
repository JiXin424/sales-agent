from sales_agent.ontology.canonicalizer import canonical_key, normalize_aliases


def test_canonical_key_lowercases_and_removes_spacing():
    assert canonical_key(" 网票 福多多 ") == "网票福多多"
    assert canonical_key("FDD Product") == "fddproduct"


def test_normalize_aliases_deduplicates_and_keeps_order():
    assert normalize_aliases([" 福多多 ", "福多多", "FDD", ""]) == ["福多多", "FDD"]
