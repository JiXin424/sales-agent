"""Centralized retry and timeout policies for graph nodes."""

from __future__ import annotations

import httpx
from langgraph.types import RetryPolicy, TimeoutPolicy

# LLM call retry: network errors, rate limits
LLM_RETRY_POLICY = RetryPolicy(
    initial_interval=1.0,
    backoff_factor=2.0,
    max_attempts=3,
    retry_on=(httpx.TimeoutException, ConnectionError, TimeoutError),
)

# DB query retry
DB_RETRY_POLICY = RetryPolicy(
    initial_interval=0.5,
    backoff_factor=2.0,
    max_attempts=3,
)

# LLM call timeout
LLM_TIMEOUT = TimeoutPolicy(run_timeout=120.0)

# DB query timeout
DB_TIMEOUT = TimeoutPolicy(run_timeout=30.0)
