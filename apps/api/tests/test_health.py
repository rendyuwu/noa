"""Scaffold smoke tests."""

from fastapi.testclient import TestClient

from noa_api.main import app, create_app


def test_health_ok() -> None:
    """`/health` returns 200 with `{"status":"ok"}`."""
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_app_returns_independent_instances() -> None:
    """Factory is usable per-test; tests never share app state."""
    assert create_app() is not create_app()


def test_mcp_is_mounted_and_authenticated() -> None:
    """The execution-time permission re-check: `/mcp` exists since the FastMCP mount, and it refuses
    a caller with no credential.

    On the process-wide `app` — the one uvicorn serves — rather than a test-built instance,
    because "the shipped app mounts an *authenticated* endpoint" is the claim. The refusal
    needs no database: a missing bearer is named before any session is opened.
    `test_mcp_mount.py` covers the mount's behaviour in depth.
    """
    with TestClient(app) as client:
        response = client.post("/mcp", json={})

    assert response.status_code == 401
    assert response.json()["error_code"] == "mcp_token_missing"
