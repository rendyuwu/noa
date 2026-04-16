import os

import pytest
from httpx import ASGITransport, AsyncClient

from noa_api.core.config import Settings

os.environ.setdefault("LLM_API_KEY", "test-llm-api-key")

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


@pytest.mark.parametrize("llm_api_key", [None, "   "])
def test_create_app_requires_llm_api_key(llm_api_key: str | None) -> None:
    settings = Settings.model_validate(
        {"environment": "test", "llm_api_key": llm_api_key}
    )

    with pytest.raises(ValueError, match="llm_api_key is required"):
        create_app(settings)
