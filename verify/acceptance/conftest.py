"""Acceptance test isolation — reset non-default rules and bucket state.

Each test function starts with a clean slate: only the default rule exists,
all bucket state cleared. This prevents cross-test interference from accumulated
rules and counters in the shared server instance.
"""

import os

import httpx
import pytest

API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8010")


@pytest.fixture(autouse=True)
async def _reset_before_test():
    """Reset rules and buckets before each test function."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        await client.post("/ratelimit/rules/admin/reset")
    yield
