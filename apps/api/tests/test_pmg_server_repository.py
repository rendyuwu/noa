"""`SQLPMGServerRepository` against a live database.

`test_pmg_server_ref.py` and `test_pmg_tools_whitelist_search.py` cover policy over in-memory
doubles; this covers the SQL. The same split `test_whm_server_repository.py` makes, and the same
three things a fake cannot tell you:

- that `ORDER BY name` is really the database's ordering and not the double's `sorted()`, which
  is what the resolver's tie handling assumes;
- that `get_by_id` really answers `None` for an id that is not there, rather than raising the
  way `scalar_one()` would;
- that a row round-trips with its server-generated `id` and timestamps present — the three
  columns the in-memory rows have to fill by hand.

A scratch database is created, migrated with `alembic upgrade head`, and dropped — skipped
(never failed) when Postgres is unreachable, exactly as the other repository tests do, so the
suite still runs without Docker.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.db.models import PMGServer
from core.servers.pmg_repository import SQLPMGServerRepository
from support.database import MUTATED_TABLES, migrated_database, truncate
from support.servers import SSH_PASSWORD

SCRATCH_DB = "noa_pmg_server_repository_test"


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    """A scratch database at `head`. Skips when Postgres is unreachable."""
    with migrated_database(SCRATCH_DB) as url:
        yield url


@pytest.fixture
async def session(database_url: str) -> AsyncIterator[AsyncSession]:
    """Per-test engine and session, with the mutated tables emptied first.

    Function-scoped because an asyncpg connection belongs to the event loop that opened it, and
    each test gets its own loop.
    """
    await truncate(database_url, *MUTATED_TABLES)
    engine = create_async_engine(database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as opened:
            yield opened
    finally:
        await engine.dispose()


@pytest.fixture
def repository(session: AsyncSession) -> SQLPMGServerRepository:
    return SQLPMGServerRepository(session)


async def insert(session: AsyncSession, name: str, *, ssh_host: str | None = None) -> PMGServer:
    """One `pmg_servers` row, written the way the admin routes will."""
    server = PMGServer(
        name=name,
        ssh_host=ssh_host or f"{name}.example.net",
        ssh_username="noa-ops",
        ssh_password=SSH_PASSWORD,
        ssh_host_key_fingerprint="SHA256:pinned",
    )
    session.add(server)
    await session.flush()
    await session.refresh(server)
    return server


async def test_servers_come_back_ordered_by_name(
    session: AsyncSession, repository: SQLPMGServerRepository
) -> None:
    """The `ORDER BY` is the database's, not the caller's.

    Inserted out of order on purpose: the ambiguity `choices` an operator picks from present
    this sequence, and "the second one" should mean the same row on the next call.
    """
    await insert(session, "charlie")
    await insert(session, "alpha")
    await insert(session, "bravo")

    servers = await repository.list_servers()

    assert [server.name for server in servers] == ["alpha", "bravo", "charlie"]


async def test_an_empty_table_lists_nothing(repository: SQLPMGServerRepository) -> None:
    """No PMG servers configured is an empty list, not an error."""
    assert await repository.list_servers() == []


async def test_get_by_id_finds_the_row(
    session: AsyncSession, repository: SQLPMGServerRepository
) -> None:
    written = await insert(session, "pmg1")

    found = await repository.get_by_id(written.id)

    assert found is not None
    assert found.id == written.id
    assert found.ssh_host == "pmg1.example.net"


async def test_get_by_id_answers_none_for_a_missing_row(
    repository: SQLPMGServerRepository,
) -> None:
    """`None`, not a raise: `resolve_pmg_server_ref` turns it into `host_not_found` (§V.18)."""
    assert await repository.get_by_id(uuid4()) is None


async def test_a_stored_row_carries_its_generated_identity(
    session: AsyncSession, repository: SQLPMGServerRepository
) -> None:
    """`id`, `created_at` and `updated_at` are server defaults, and the in-memory rows fill them
    by hand — this is the only place the real thing is checked."""
    written = await insert(session, "pmg1")

    [server] = await repository.list_servers()

    assert server.id == written.id
    assert server.created_at is not None
    assert server.updated_at is not None


async def test_a_stored_row_renders_safely(
    session: AsyncSession, repository: SQLPMGServerRepository
) -> None:
    """`to_safe_dict` on a real row: identifiers present, credentials absent (§V.2, §V.8).

    Not on `PMGServerRowLike` — nothing exposed over MCP renders a PMG row — but the admin
    routes of §T.54 will, and the row already has the method, so the guarantee is checked where
    the row is real.
    """
    await insert(session, "pmg1")

    [server] = await repository.list_servers()
    rendered = server.to_safe_dict()

    assert rendered["ssh_host"] == "pmg1.example.net"
    assert rendered["has_ssh_password"] is True
    assert SSH_PASSWORD not in rendered.values()
    assert "ssh_password" not in rendered
