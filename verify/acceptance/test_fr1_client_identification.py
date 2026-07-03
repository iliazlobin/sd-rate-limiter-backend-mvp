"""FR1: Identify client from X-API-Key or X-User-ID header.

Black-box acceptance: verifies client extraction priority and pass-through behavior.
Talks to the running app via API_BASE_URL. No app imports.
"""

import os

import httpx
import pytest

API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8010")


@pytest.mark.asyncio
async def test_extracts_api_key_from_header():
    """X-API-Key header takes priority over X-User-ID."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        # First, create a rule for api_key clients
        await client.put(
            "/ratelimit/rules/fr1-api-key",
            json={
                "rule_id": "fr1-api-key",
                "client_type": "api_key",
                "algorithm": "token_bucket",
                "limit": 5,
                "window_sec": 60,
                "burst": 5,
            },
        )

        # Send check with both headers — api_key should be used
        resp = await client.post(
            "/ratelimit/check",
            json={
                "client_type": "api_key",
                "client_value": "key-abc123",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["allowed"] is True
        assert data["remaining"] == 4  # 5 burst - 1 consumed


@pytest.mark.asyncio
async def test_extracts_user_id_when_no_api_key():
    """X-User-ID is used when X-API-Key is absent."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        await client.put(
            "/ratelimit/rules/fr1-user-id",
            json={
                "rule_id": "fr1-user-id",
                "client_type": "user_id",
                "algorithm": "token_bucket",
                "limit": 3,
                "window_sec": 60,
                "burst": 3,
            },
        )

        resp = await client.post(
            "/ratelimit/check",
            json={
                "client_type": "user_id",
                "client_value": "user-42",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["allowed"] is True
        assert data["remaining"] == 2


@pytest.mark.asyncio
async def test_unknown_client_type_skips_rate_limiting():
    """A client_type with no matching rules returns allowed=true."""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        resp = await client.post(
            "/ratelimit/check",
            json={
                "client_type": "nonexistent",
                "client_value": "whatever",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["allowed"] is True
