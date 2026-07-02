"""Test FactInventory: dedup via canonical hash, conflict detection."""

import pytest
from sales_agent.optimization.fact_inventory import FactInventory, FactRecord


class TestFactInventory:
    def test_fact_hash_is_stable_across_order(self):
        """Canonical hash must be identical regardless of field order."""
        inv = FactInventory.__new__(FactInventory)
        a = inv.compute_hash(FactRecord(
            subject="Product", predicate="has_discount",
            object_values=["A", "B"],
        ))
        b = inv.compute_hash(FactRecord(
            subject="Product", predicate="has_discount",
            object_values=["B", "A"],
        ))
        assert a == b

    def test_different_facts_have_different_hashes(self):
        inv = FactInventory.__new__(FactInventory)
        a = inv.compute_hash(FactRecord(subject="A", predicate="X", object_values=["1"]))
        b = inv.compute_hash(FactRecord(subject="B", predicate="Y", object_values=["2"]))
        assert a != b

    def test_hash_is_case_insensitive(self):
        inv = FactInventory.__new__(FactInventory)
        a = inv.compute_hash(FactRecord(subject="Product", predicate="Price", object_values=["100"]))
        b = inv.compute_hash(FactRecord(subject="product", predicate="price", object_values=["100"]))
        assert a == b


class TestFactInventoryIntegration:
    @pytest.mark.asyncio
    async def test_store_creates_unique_facts(self, db_session):
        """Store two different facts → both get unique IDs."""
        from sqlalchemy import select, func
        from sales_agent.models.knowledge_fact import KnowledgeFact
        from sales_agent.models.knowledge_version import KnowledgeVersion

        # Setup: create a knowledge version
        kv = KnowledgeVersion(
            id="kv_test_1",
            tenant_id="test_tenant_001",
            agent_id="test_agent_001",
            version_number=1,
        )
        db_session.add(kv)
        await db_session.flush()

        inv = FactInventory(db_session)
        r1 = await inv.store("test_tenant_001", "kv_test_1", FactRecord(
            subject="折扣", predicate="是", object_values=["5折"],
            document_revision_id="dr1", document_id="doc1",
        ))
        r2 = await inv.store("test_tenant_001", "kv_test_1", FactRecord(
            subject="折扣", predicate="是", object_values=["6折"],
            document_revision_id="dr2", document_id="doc2",
        ))

        assert r1.fact_id != r2.fact_id
        # Second should be conflicting since same subject+predicate, different object
        assert r2.conflict_status == "conflicting"
