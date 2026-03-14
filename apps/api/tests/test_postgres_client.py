from __future__ import annotations

from noa_api.storage.postgres.client import create_engine, create_session_factory


def test_engine_accessor_is_process_singleton() -> None:
    assert create_engine() is create_engine()


def test_session_factory_accessor_is_process_singleton() -> None:
    engine = create_engine()

    assert create_session_factory(engine) is create_session_factory(engine)
