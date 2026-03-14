from __future__ import annotations

from noa_api.storage.postgres.client import get_engine, get_session_factory


def test_engine_accessor_is_process_singleton() -> None:
    assert get_engine() is get_engine()


def test_session_factory_accessor_is_process_singleton() -> None:
    assert get_session_factory() is get_session_factory()
