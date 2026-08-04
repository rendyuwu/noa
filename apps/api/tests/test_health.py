"""Scaffold smoke tests (T3)."""

from fastapi.testclient import TestClient

from noa_api.main import app, create_app


def test_health_ok() -> None:
    """V51: `/health` returns 200 with `{"status":"ok"}`."""
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_app_returns_independent_instances() -> None:
    """Factory is usable per-test; tests never share app state."""
    assert create_app() is not create_app()


def test_mcp_not_mounted_yet() -> None:
    """V1: no `/mcp` route until T11 supplies the TokenVerifier.

    Guards against mounting an unauthenticated MCP endpoint by accident.
    """
    with TestClient(app) as client:
        response = client.post("/mcp", json={})

    assert response.status_code == 404
