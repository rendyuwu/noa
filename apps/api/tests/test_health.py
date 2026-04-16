import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from noa_api.core.config import Settings


async def test_health_ok(create_test_app) -> None:
    app = create_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_app_allows_configured_cors_origin(create_test_app) -> None:
    app = create_test_app(
        Settings.model_validate(
            {
                "environment": "development",
                "api_cors_allowed_origins": ["http://localhost:3000"],
                "llm_api_key": SecretStr("test-key"),
            }
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


@pytest.mark.parametrize("llm_api_key", [None, "   "])
def test_create_app_requires_llm_api_key(
    create_test_app,
    llm_api_key: str | None,
) -> None:
    settings = Settings.model_validate(
        {
            "environment": "test",
            "llm_api_key": llm_api_key,
        }
    )

    with pytest.raises(ValueError, match="llm_api_key is required"):
        create_test_app(settings)
