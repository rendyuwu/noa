from __future__ import annotations

from noa_api.storage.postgres import client as postgres_client


def _get_engine_accessor():
    return getattr(postgres_client, "get_engine", postgres_client.create_engine)


def _get_session_factory_accessor():
    def create_session_factory() -> object:
        return postgres_client.create_session_factory(_get_engine_accessor()())

    return getattr(postgres_client, "get_session_factory", create_session_factory)


def test_engine_accessor_is_process_singleton() -> None:
    get_engine = _get_engine_accessor()

    assert get_engine() is get_engine()


def test_session_factory_accessor_is_process_singleton() -> None:
    get_session_factory = _get_session_factory_accessor()

    assert get_session_factory() is get_session_factory()
