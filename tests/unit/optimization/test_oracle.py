"""Test oracle corpus lookup and cross-tenant rejection."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from sales_agent.optimization.types import OracleLookupResult


@pytest.mark.asyncio
async def test_oracle_rejects_wrong_tenant():
    """Oracle must reject lookups where knowledge version doesn't match tenant."""
    from sales_agent.optimization.oracle import CorpusOracle

    mock_db = AsyncMock()
    mock_db.scalar = AsyncMock(return_value=None)  # KV not found

    oracle = CorpusOracle(mock_db)
    result = await oracle.lookup_fact(
        tenant_id="tenant-a",
        knowledge_version_id="kv-from-tenant-b",
        fact_text="some fact",
    )
    assert result.status == "invalid_lineage"


@pytest.mark.asyncio
async def test_oracle_returns_present_for_matching_fact():
    """When fact text is found in chunks, status must be 'present'."""
    from sales_agent.optimization.oracle import CorpusOracle

    chunk = MagicMock()
    chunk.id = "chunk_1"
    chunk.text = "The annual plan costs 299 yuan per user."

    mock_db = AsyncMock()
    # First call: KV exists
    mock_db.scalar = AsyncMock(return_value=MagicMock())
    # Second call: chunk query
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [chunk]
    mock_db.execute = AsyncMock(return_value=mock_result)

    oracle = CorpusOracle(mock_db)
    result = await oracle.lookup_fact(
        tenant_id="t1",
        knowledge_version_id="kv1",
        fact_text="299 yuan",
    )
    assert result.status == "present"
    assert "chunk_1" in result.supporting_chunks
