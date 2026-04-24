from __future__ import annotations

from unittest.mock import patch

from noa_api.storage.postgres.client import (
    create_engine,
    get_engine,
    get_session_factory,
)


def test_engine_accessor_is_process_singleton() -> None:
    assert get_engine() is get_engine()


def test_session_factory_accessor_is_process_singleton() -> None:
    assert get_session_factory() is get_session_factory()


def test_create_engine_passes_pool_config_from_settings() -> None:
    with patch("noa_api.storage.postgres.client.settings") as mock_settings:
        mock_settings.postgres_url = "postgresql+asyncpg://localhost/test"
        mock_settings.db_pool_size = 3
        mock_settings.db_max_overflow = 7

        engine = create_engine()
        pool = engine.pool

        assert pool.size() == 3
        assert pool._max_overflow == 7
