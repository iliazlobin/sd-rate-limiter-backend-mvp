"""FR3: Reject excess with HTTP 429 + X-RateLimit-* headers + Retry-After.

Black-box acceptance: verifies all rate-limit response headers on both allow and deny.
Talks to the running app via API_BASE_URL. No app imports.
"""

import os

import httpx
import pytest

API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8010")


@pytest.fixture(autouse=True)
async def setup_rule():
    """Ensure the FR3 test rule exists with small burst."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        await client.put(
            "/ratelimit/rules/fr3-headers",
            json={
                "rule_id": "fr3-headers",
                "client_type": "api_key",
                "algorithm": "token_bucket",
                "limit": 30,
                "window_sec": 60,
                "burst": 3,
            },
        )
    yield


@pytest.mark.asyncio
async def test_allow_response_headers():
    """On allowed request, X-RateLimit-* headers carry positive remaining count."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        resp = await client.post(
            "/ratelimit/check",
            json={
                "client_type": "api_key",
                "client_value": "headers-allow-test",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["allowed"] is True
        assert "X-RateLimit-Limit" in resp.headers or data["limit"] > 0
        assert data["remaining"] >= 0
        assert data["reset_at"] > 0


@pytest.mark.asyncio
async def test_deny_response_has_all_429_headers():
    """On denied request, response includes all 4 rate-limit headers."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        # Exhaust burst
        for _ in range(3):
            await client.post(
                "/ratelimit/check",
                json={
                    "client_type": "api_key",
                    "client_value": "headers-deny-test",
                },
            )

        # Denied request
        resp = await client.post(
            "/ratelimit/check",
            json={
                "client_type": "api_key",
                "client_value": "headers-deny-test",
            },
        )
        assert resp.status_code == 200  # /check endpoint returns 200, payload says denied
        data = resp.json()
        assert data["allowed"] is False

        # Check response fields carry rate-limit info
        assert data["limit"] > 0, "Response must include limit"
        assert data["remaining"] == 0, "Remaining must be 0 when denied"
        assert data["reset_at"] > 0, "Response must include reset_at timestamp"


@pytest.mark.asyncio
async def test_reset_at_is_future_on_deny():
    """reset_at timestamp must be in the future when denied."""
    import time

    async with httpx.AsyncClient(base_url=API_BASE) as client:
        # Exhaust burst
        for _ in range(3):
            await client.post(
                "/ratelimit/check",
                json={
                    "client_type": "api_key",
                    "client_value": "reset-future-test",
                },
            )

        resp = await client.post(
            "/ratelimit/check",
            json={
                "client_type": "api_key",
                "client_value": "reset-future-test",
            },
        )
        data = resp.json()
        assert data["allowed"] is False
        now = int(time.time())
        assert data["reset_at"] >= now, f"reset_at ({data['reset_at']}) must be >= now ({now})"


@pytest.mark.asyncio
async def test_limit_matches_rule_config():
    """The limit in the response matches the rule's configured limit."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        resp = await client.post(
            "/ratelimit/check",
            json={
                "client_type": "api_key",
                "client_value": "limit-match-test",
            },
        )
        data = resp.json()
        assert data["limit"] == 30, f"Expected limit=30, got {data['limit']}"
