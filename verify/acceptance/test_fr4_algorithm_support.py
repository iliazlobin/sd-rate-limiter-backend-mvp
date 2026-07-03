"""FR4: Support token_bucket and sliding_window_counter algorithms.

Black-box acceptance: verifies both algorithms can be used per-rule and produce
different behavior (burst tolerance vs strict window enforcement).
Talks to the running app via API_BASE_URL. No app imports.
"""

import os

import httpx
import pytest

API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8010")


@pytest.fixture(autouse=True)
async def setup_rules():
    """Create one rule of each algorithm type."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        await client.put(
            "/ratelimit/rules/fr4-tb",
            json={
                "rule_id": "fr4-tb",
                "client_type": "api_key",
                "algorithm": "token_bucket",
                "limit": 20,
                "window_sec": 60,
                "burst": 5,
            },
        )
        await client.put(
            "/ratelimit/rules/fr4-sw",
            json={
                "rule_id": "fr4-sw",
                "client_type": "user_id",
                "algorithm": "sliding_window_counter",
                "limit": 5,
                "window_sec": 60,
            },
        )
    yield


@pytest.mark.asyncio
async def test_token_bucket_rule_allows_burst():
    """Token bucket with burst=5 allows up to 5 rapid requests."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        for i in range(5):
            resp = await client.post(
                "/ratelimit/check",
                json={
                    "client_type": "api_key",
                    "client_value": "fr4-tb-burst",
                },
            )
            assert resp.status_code == 200
            assert resp.json()["allowed"] is True, f"Request {i + 1}/5 should be allowed"


@pytest.mark.asyncio
async def test_sliding_window_enforces_limit():
    """Sliding window with limit=5 rejects the 6th request."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        # First 5 allowed
        for i in range(5):
            resp = await client.post(
                "/ratelimit/check",
                json={
                    "client_type": "user_id",
                    "client_value": "fr4-sw-limit",
                },
            )
            assert resp.json()["allowed"] is True, f"Request {i + 1}/5 should be allowed"

        # 6th denied
        resp = await client.post(
            "/ratelimit/check",
            json={
                "client_type": "user_id",
                "client_value": "fr4-sw-limit",
            },
        )
        assert resp.json()["allowed"] is False, "6th request should be denied"


@pytest.mark.asyncio
async def test_algorithms_are_independent():
    """Token bucket and sliding window rules are evaluated independently."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        # Exhaust token bucket for api_key
        for _ in range(5):
            await client.post(
                "/ratelimit/check",
                json={
                    "client_type": "api_key",
                    "client_value": "indep-tb",
                },
            )
        resp_tb = await client.post(
            "/ratelimit/check",
            json={
                "client_type": "api_key",
                "client_value": "indep-tb",
            },
        )
        assert resp_tb.json()["allowed"] is False, "Token bucket should be exhausted"

        # Sliding window for user_id should be unaffected
        resp_sw = await client.post(
            "/ratelimit/check",
            json={
                "client_type": "user_id",
                "client_value": "indep-sw",
            },
        )
        assert resp_sw.json()["allowed"] is True, "Sliding window should be independent"


@pytest.mark.asyncio
async def test_sliding_window_has_no_burst():
    """Sliding window should NOT have burst behavior — strict per-window counting."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        # Create a sliding window rule with limit=3, small window
        await client.put(
            "/ratelimit/rules/fr4-sw-noburst",
            json={
                "rule_id": "fr4-sw-noburst",
                "client_type": "api_key",
                "algorithm": "sliding_window_counter",
                "limit": 3,
                "window_sec": 60,
            },
        )

        # All 3 allowed
        for i in range(3):
            resp = await client.post(
                "/ratelimit/check",
                json={
                    "client_type": "api_key",
                    "client_value": "sw-noburst",
                },
            )
            assert resp.json()["allowed"] is True

        # 4th denied (no burst — strict limit)
        resp = await client.post(
            "/ratelimit/check",
            json={
                "client_type": "api_key",
                "client_value": "sw-noburst",
            },
        )
        assert resp.json()["allowed"] is False, (
            "Sliding window with no burst should deny 4th request"
        )


@pytest.mark.asyncio
async def test_rules_crud_endpoints():
    """Rule CRUD endpoints work: list, create, delete."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        # List
        resp = await client.get("/ratelimit/rules")
        assert resp.status_code == 200
        rules = resp.json()
        assert len(rules) >= 2  # at least our two fixtures

        # Delete
        resp = await client.delete("/ratelimit/rules/fr4-tb")
        assert resp.status_code == 204

        # Verify deleted
        resp = await client.get("/ratelimit/rules")
        rule_ids = [r["rule_id"] for r in resp.json()]
        assert "fr4-tb" not in rule_ids
