"""Shared fixtures for white-box tests."""

import pytest
from fastapi.testclient import TestClient

from src.rate_limiter.main import create_app


@pytest.fixture
def client() -> TestClient:
    """Test client backed by the FastAPI app factory."""
    app = create_app()
    with TestClient(app) as c:
        yield c
