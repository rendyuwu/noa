"""Async engine + session factory.

First DB access in the repo, so this module exists to serve the login flow and
is shared by everything after it.

`noa-old` reached for `@cache`d module-level `get_engine()` / `get_session_factory()`
accessors. Both are functions here, and the app calls them once in its lifespan and
hangs the results on `app.state`. Two reasons:

- The login flow requires services be constructed once at startup rather than per request, and
  a cached global is the same idea with a worse failure mode: a test that builds a
  second app silently shares the first one's engine and its event loop, then fails
  somewhere unrelated when that loop closes.
- Engine disposal has an owner. The lifespan that created it awaits
  `engine.dispose()` on shutdown, so connections do not outlive the app.

`create_async_engine` is lazy — it opens no connection until first use — so an app
with an unreachable database still boots and answers `/health`. A broken
`POSTGRES_URL` surfaces on the first query, not at import.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.config import Settings


class SessionFactory(Protocol):
    """What a caller outside FastAPI's dependency graph needs: call it, get a session.

    A Protocol rather than the concrete `async_sessionmaker[AsyncSession]` so a path that opens its
    own transactions can be exercised without Postgres — the expiry sweeper is the case in `core/`:
    its loop, its error handling and its one-session-per-pass rule are all testable without a
    database, while the SQL it runs is pinned separately against a real one.

    `noa_api.mcp_request_auth.McpSessionFactory` states the same shape on the MCP side, where
    it cannot import this one: `core/` must not depend on an app.
    """

    def __call__(self) -> AbstractAsyncContextManager[AsyncSession]: ...


def create_engine(settings: Settings) -> AsyncEngine:
    """Build the asyncpg engine from settings.

    `pool_pre_ping` costs one round trip per checkout and buys immunity to stale
    connections after a Postgres restart or a firewall idle-timeout — otherwise the
    first request after either gets a connection error that a retry would fix.
    """
    return create_async_engine(
        settings.postgres_url_str,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory bound to `engine`.

    `expire_on_commit=False`: request handlers read attributes off an ORM object after committing (a
    login response reads `user.email` after the transaction closes), and the default would issue a
    refresh against a session the request is done with.
    """
    return async_sessionmaker(engine, expire_on_commit=False)


__all__ = ["SessionFactory", "create_engine", "create_session_factory"]
