from httpx import ASGITransport, AsyncClient

from noa_api.core.config import Settings
from noa_api.main import create_app


async def test_health_ok() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_app_allows_configured_cors_origin() -> None:
    app = create_app(
        Settings(
            environment="development",
            api_cors_allowed_origins=["http://localhost:3000"],
        )
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.options(
            "/health",
            headers={
                "origin": "http://localhost:3000",
                "access-control-request-method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
