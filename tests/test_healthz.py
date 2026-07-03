"""Health check endpoint tests."""

from fastapi.testclient import TestClient


class TestHealthz:
    """GET /healthz must return 200."""

    def test_returns_200(self, client: TestClient) -> None:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
