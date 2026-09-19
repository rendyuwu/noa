"""`SQLServerRepository` over `whm_servers`, against a live database.

`test_whm_server_ref.py` and `test_whm_tools_read.py` cover policy over in-memory doubles;
this covers the SQL. Everything asserted here is something a fake cannot tell you:

- that `ORDER BY name` is really the database's ordering and not the double's `sorted()`,
  which is what the resolver's tie handling and the tool's output order both assume;
- that `get_by_id` really answers `None` for an id that is not there, rather than raising
  the way `scalar_one()` would;
- that a row round-trips through `to_safe_dict` with its server-generated `id` and
  timestamps present — the three columns the in-memory rows have to fill by hand.

A scratch database is created, migrated with `alembic upgrade head`, and dropped — skipped
(never failed) when Postgres is unreachable, exactly as the other repository tests do, so
the suite still runs without Docker.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.db.models import WHMServer
from core.servers.repository import SQLServerRepository
from support.database import MUTATED_TABLES, migrated_database, truncate
from support.servers import API_TOKEN, SSH_PASSWORD

SCRATCH_DB = "noa_whm_server_repository_test"


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    """A scratch database at `head`. Skips when Postgres is unreachable."""
    with migrated_database(SCRATCH_DB) as url:
        yield url


@pytest.fixture
async def session(database_url: str) -> AsyncIterator[AsyncSession]:
    """Per-test engine and session, with the mutated tables emptied first.

    Function-scoped because an asyncpg connection belongs to the event loop that opened it,
    and each test gets its own loop.
    """
    await truncate(database_url, *MUTATED_TABLES)
    engine = create_async_engine(database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as opened:
            yield opened
    finally:
        await engine.dispose()


@pytest.fixture
def repository(session: AsyncSession) -> SQLServerRepository[WHMServer]:
    return SQLServerRepository(session, model=WHMServer)


async def insert(session: AsyncSession, name: str, *, base_url: str | None = None) -> WHMServer:
    """One `whm_servers` row, written the way the admin routes will."""
    server = WHMServer(
        name=name,
        base_url=base_url or f"https://{name}.example.net:2087",
        api_username="root",
        api_token=API_TOKEN,
        verify_ssl=True,
        ssh_username="noa",
        ssh_password=SSH_PASSWORD,
    )
    session.add(server)
    await session.flush()
    await session.refresh(server)
    return server


async def test_servers_come_back_ordered_by_name(
    session: AsyncSession, repository: SQLServerRepository[WHMServer]
) -> None:
    """The `ORDER BY` is the database's, not the caller's.

    Inserted out of order on purpose: the ambiguity `choices` an operator picks from and the
    tool's listing both present this sequence, and "row three" should mean the same row on
    the next call.
    """
    await insert(session, "charlie")
    await insert(session, "alpha")
    await insert(session, "bravo")

    servers = await repository.list_servers()

    assert [server.name for server in servers] == ["alpha", "bravo", "charlie"]


async def test_an_empty_table_lists_nothing(repository: SQLServerRepository[WHMServer]) -> None:
    """No servers configured is an empty list, not an error."""
    assert await repository.list_servers() == []


async def test_get_by_id_finds_the_row(
    session: AsyncSession, repository: SQLServerRepository[WHMServer]
) -> None:
    written = await insert(session, "alpha")

    found = await repository.get_by_id(written.id)

    assert found is not None
    assert found.id == written.id
    assert found.name == "alpha"


async def test_get_by_id_answers_none_for_a_missing_row(
    repository: SQLServerRepository[WHMServer],
) -> None:
    """`None`, not a raise: `resolve_whm_server_ref` turns it into `host_not_found`."""
    assert await repository.get_by_id(uuid4()) is None


async def test_a_stored_row_renders_safely(
    session: AsyncSession, repository: SQLServerRepository[WHMServer]
) -> None:
    """`to_safe_dict` on a real row: identifiers present, credentials absent.

    Run against a row the insert path generated, because `id`, `created_at` and `updated_at`
    are never the caller's — an in-memory double fills them by hand, and this is the only place
    that check is against the real thing.
    """
    written = await insert(session, "alpha")

    [server] = await repository.list_servers()
    rendered = server.to_safe_dict()

    assert rendered["id"] == str(written.id)
    assert rendered["created_at"] is not None
    assert rendered["has_api_token"] is True
    assert API_TOKEN not in rendered.values()
    assert SSH_PASSWORD not in rendered.values()
    assert "api_token" not in rendered
