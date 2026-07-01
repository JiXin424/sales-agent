"""Centralized retry, timeout, and cache policies for graph nodes."""

from __future__ import annotations

import httpx
from langgraph.types import RetryPolicy, TimeoutPolicy, CachePolicy


# ── LLM call retry: network errors, rate limits ──────────────────────
LLM_RETRY_POLICY = RetryPolicy(
    initial_interval=1.0,
    backoff_factor=2.0,
    max_attempts=3,
    retry_on=(httpx.TimeoutException, ConnectionError, TimeoutError),
)

# ── DB query retry ───────────────────────────────────────────────────
DB_RETRY_POLICY = RetryPolicy(
    initial_interval=0.5,
    backoff_factor=2.0,
    max_attempts=3,
)

# ── Retrieval retry ───────────────────────────────────────────────────
RETRIEVAL_RETRY_POLICY = RetryPolicy(
    initial_interval=0.5,
    backoff_factor=2.0,
    max_attempts=2,
    retry_on=(httpx.TimeoutException, ConnectionError),
)

# ── Timeouts ──────────────────────────────────────────────────────────
LLM_TIMEOUT = TimeoutPolicy(run_timeout=120.0)
DB_TIMEOUT = TimeoutPolicy(run_timeout=30.0)
RETRIEVAL_TIMEOUT = TimeoutPolicy(run_timeout=15.0)

# ── Cache policies (P1) ──────────────────────────────────────────────
# Cache retrieval results for 5 minutes to avoid redundant RAG calls
CACHE_RETRIEVAL = CachePolicy(ttl=300)

# Cache LLM generations for 2 minutes (same message → same answer)
CACHE_GENERATION = CachePolicy(ttl=120)
