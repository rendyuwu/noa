from __future__ import annotations

from functools import cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from noa_api.core.config import settings


def create_engine() -> AsyncEngine:
    return create_async_engine(
        str(settings.postgres_url),
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@cache
def get_engine() -> AsyncEngine:
    return create_engine()


@cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return create_session_factory(get_engine())
