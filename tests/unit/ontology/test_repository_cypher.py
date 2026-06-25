from sales_agent.ontology.repository import (
    upsert_entity_statement,
    create_fact_statement,
    retrieval_statement,
    vector_query_statement,
)


def test_upsert_entity_statement_uses_canonical_unique_key():
    stmt = upsert_entity_statement()
    assert "MERGE (e:Entity" in stmt
    assert "tenant_id: $tenant_id" in stmt
    assert "canonical_key: $canonical_key" in stmt
    assert "type: $type" in stmt


def test_create_fact_statement_uses_fact_node_model():
    stmt = create_fact_statement()
    assert "CREATE (f:Fact" in stmt
    assert "SUBJECT_OF" in stmt
    assert "OBJECT_OF" in stmt
    assert "SUPPORTED_BY" in stmt
    assert "SourceDocument" in stmt


def test_retrieval_statement_filters_active_facts():
    stmt = retrieval_statement()
    assert "f.status = 'active'" in stmt
    assert "e.tenant_id = $tenant_id" in stmt


def test_vector_query_statement_uses_vector_index():
    stmt = vector_query_statement()
    assert "db.index.vector.queryNodes" in stmt
    assert "entity_embedding_vector" in stmt
