"""Test optimization auth models are registered correctly."""

from sales_agent.core.database import Base
import sales_agent.models.optimization_auth  # noqa: F401


def test_auth_tables_and_tenant_keys():
    expected = {"optimization_api_credentials", "optimization_command_audits"}
    tables = set(Base.metadata.tables)
    missing = expected - tables
    assert not missing, f"Missing tables: {missing}"
    for name in expected:
        assert "tenant_id" in Base.metadata.tables[name].c, (
            f"{name} missing tenant_id column"
        )


def test_credential_has_token_hash_not_plaintext():
    columns = Base.metadata.tables["optimization_api_credentials"].c
    assert "token_hash" in columns.keys()
    assert "token_prefix" in columns.keys()
    assert "subject" in columns.keys()
    assert "revoked_at" in columns.keys()
    assert "expires_at" in columns.keys()
    # No plaintext token column
    assert "token" not in columns.keys()
    assert "plaintext" not in columns.keys()


def test_command_audit_has_required_fields():
    columns = Base.metadata.tables["optimization_command_audits"].c
    assert "action" in columns.keys()
    assert "idempotency_key" in columns.keys()
    assert "sanitized_input_json" in columns.keys()
    assert "outcome" in columns.keys()
