from __future__ import annotations

from fastapi import Response
from httpx import ASGITransport, AsyncClient

from noa_api.main import create_app


async def test_health_includes_x_request_id_header() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.headers.get("x-request-id")


async def test_inbound_x_request_id_is_preserved() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/health", headers={"x-request-id": "req-from-client"}
        )

    assert response.status_code == 200
    assert response.headers.get("x-request-id") == "req-from-client"


async def test_uncaught_exception_returns_safe_500_envelope_with_request_id() -> None:
    app = create_app()

    @app.get("/_tests/error")
    async def error_route() -> Response:
        raise RuntimeError("boom")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_tests/error")

    assert response.status_code == 500
    request_id = response.headers.get("x-request-id")
    assert request_id
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "detail": "Internal server error",
        "error_code": "internal_server_error",
        "request_id": request_id,
    }
