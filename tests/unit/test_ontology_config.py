from sales_agent.core.config import Settings


def test_neo4j_config_defaults_disabled():
    settings = Settings()
    assert settings.ontology.knowledge_engine == "legacy_rag"
    assert settings.neo4j.uri == ""
    assert settings.neo4j.database == "neo4j"
    assert settings.ontology.vector_fallback == "conservative"


def test_neo4j_config_from_yaml_fields():
    settings = Settings(
        ontology={"knowledge_engine": "ontology_neo4j", "vector_fallback": "conservative"},
        neo4j={
            "uri": "bolt://neo4j:7687",
            "user": "neo4j",
            "password": "secret",
            "database": "sales",
            "visual_url": "https://neo4j.example/workspace",
        },
    )
    assert settings.ontology.knowledge_engine == "ontology_neo4j"
    assert settings.neo4j.uri == "bolt://neo4j:7687"
    assert settings.neo4j.user == "neo4j"
    assert settings.neo4j.password == "secret"
    assert settings.neo4j.database == "sales"
    assert settings.neo4j.visual_url == "https://neo4j.example/workspace"
