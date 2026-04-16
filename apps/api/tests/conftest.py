from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import SecretStr
from structlog.contextvars import clear_contextvars

from noa_api.core.config import Settings


@pytest.fixture(autouse=True)
def clear_structlog_contextvars() -> Iterator[None]:
    clear_contextvars()
    yield
    clear_contextvars()


@pytest.fixture
def test_settings() -> Settings:
    return Settings.model_validate(
        {
            "environment": "test",
            "llm_api_key": SecretStr("test-key"),
        }
    )


@pytest.fixture
def create_test_app(monkeypatch: pytest.MonkeyPatch, test_settings: Settings):
    from noa_api.core import config as config_module

    monkeypatch.setattr(config_module, "settings", test_settings)

    from noa_api.main import create_app

    def factory(app_settings: Settings | None = None):
        return create_app(test_settings if app_settings is None else app_settings)

    return factory
