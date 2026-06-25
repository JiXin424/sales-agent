import pytest

from sales_agent.ontology.schema import schema_statements


def test_schema_statements_include_fact_and_vector_indexes():
    statements = schema_statements(vector_dimensions=1024)
    joined = "\n".join(statements)
    assert "entity_canonical_unique" in joined
    assert "entity_name_fulltext" in joined
    assert "entity_embedding_vector" in joined
    assert "fact_lookup" in joined
    assert "VECTOR" in joined
