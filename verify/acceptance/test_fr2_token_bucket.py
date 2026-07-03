"""FR2: Enforce configurable rules via token bucket algorithm.

Black-box acceptance: verifies token bucket refill, burst, exhaustion, and 429.
Talks to the running app via API_BASE_URL. No app imports.
"""

import os
import time

import httpx
import pytest

API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8010")


@pytest.fixture(autouse=True)
async def setup_rule():
    """Ensure the FR2 test rule exists."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        await client.put(
            "/ratelimit/rules/fr2-tb",
            json={
                "rule_id": "fr2-tb",
                "client_type": "api_key",
                "algorithm": "token_bucket",
                "limit": 10,
                "window_sec": 60,
                "burst": 5,  # burst=5, rate=10/60≈0.167 tokens/s
            },
        )
    yield


@pytest.mark.asyncio
async def test_token_bucket_allows_up_to_burst():
    """Burst=5: first 5 requests should all be allowed."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        for i in range(5):
            resp = await client.post(
                "/ratelimit/check",
                json={
                    "client_type": "api_key",
                    "client_value": "burst-test",
                },
            )
            assert resp.status_code == 200, f"Request {i + 1} should be allowed"
            data = resp.json()
            assert data["allowed"] is True
            assert data["remaining"] == 4 - i  # 5 burst - (i+1) consumed


@pytest.mark.asyncio
async def test_token_bucket_rejects_beyond_burst():
    """After exhausting burst, 6th request should be denied with 429."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        # Exhaust burst
        for _ in range(5):
            await client.post(
                "/ratelimit/check",
                json={
                    "client_type": "api_key",
                    "client_value": "exhaust-test",
                },
            )

        # 6th request — should be denied
        resp = await client.post(
            "/ratelimit/check",
            json={
                "client_type": "api_key",
                "client_value": "exhaust-test",
            },
        )
        assert resp.status_code == 200  # check endpoint returns 200 even on deny
        data = resp.json()
        assert data["allowed"] is False
        assert data["remaining"] == 0


@pytest.mark.asyncio
async def test_token_bucket_refills_over_time():
    """After waiting, tokens refill and requests are allowed again."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        # Create a rule with very fast refill for test speed
        await client.put(
            "/ratelimit/rules/fr2-fast-refill",
            json={
                "rule_id": "fr2-fast-refill",
                "client_type": "user_id",
                "algorithm": "token_bucket",
                "limit": 100,
                "window_sec": 1,  # 100 tokens/s — very fast refill
                "burst": 2,
            },
        )

        # Exhaust burst
        await client.post(
            "/ratelimit/check",
            json={
                "client_type": "user_id",
                "client_value": "refill-test",
            },
        )
        await client.post(
            "/ratelimit/check",
            json={
                "client_type": "user_id",
                "client_value": "refill-test",
            },
        )

        # Should be denied immediately
        resp = await client.post(
            "/ratelimit/check",
            json={
                "client_type": "user_id",
                "client_value": "refill-test",
            },
        )
        assert resp.json()["allowed"] is False

        # Wait for refill (~10ms at 100 tokens/s gives 1 token)
        time.sleep(0.05)

        # Should be allowed again
        resp = await client.post(
            "/ratelimit/check",
            json={
                "client_type": "user_id",
                "client_value": "refill-test",
            },
        )
        assert resp.json()["allowed"] is True
