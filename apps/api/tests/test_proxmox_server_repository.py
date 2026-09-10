"""`SQLProxmoxServerRepository` against a live database.

`test_proxmox_server_ref.py` and `test_proxmox_tools_reset_password.py` cover policy over
in-memory doubles; this covers the SQL. The same split `test_whm_server_repository.py` and
`test_pmg_server_repository.py` make, and the same three things a fake cannot tell you:

- that `ORDER BY name` is really the database's ordering and not the double's `sorted()`, which
  is what the resolver's tie handling assumes;
- that `get_by_id` really answers `None` for an id that is not there, rather than raising the way
  `scalar_one()` would;
- that a row round-trips with its server-generated `id` and timestamps present — the three
  columns the in-memory rows have to fill by hand.

One thing here is Proxmox's alone: `verify_ssl` has a **server default of `false`** (the column
comment says why — Proxmox ships a self-signed certificate), which is the opposite of WHM's. So a
row written without it comes back `False`, and that is asserted rather than assumed: a client
built off a row whose TLS verification silently flipped is the kind of thing nothing else would
notice.

A scratch database is created, migrated with `alembic upgrade head`, and dropped — skipped (never
failed) when Postgres is unreachable, exactly as the other repository tests do, so the suite still
runs without Docker.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.db.models import ProxmoxServer
from core.servers.proxmox_repository import SQLProxmoxServerRepository
from support.database import MUTATED_TABLES, migrated_database, truncate
from support.servers import PROXMOX_API_TOKEN_SECRET

SCRATCH_DB = "noa_proxmox_server_repository_test"


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
def repository(session: AsyncSession) -> SQLProxmoxServerRepository:
    return SQLProxmoxServerRepository(session)


async def insert(
    session: AsyncSession,
    name: str,
    *,
    base_url: str | None = None,
    verify_ssl: bool | None = None,
) -> ProxmoxServer:
    """One `proxmox_servers` row, written the way the admin routes will."""
    server = ProxmoxServer(
        name=name,
        base_url=base_url or f"https://{name}.example.net:8006",
        api_token_id="root@pam!noa",
        api_token_secret=PROXMOX_API_TOKEN_SECRET,
        **({} if verify_ssl is None else {"verify_ssl": verify_ssl}),
    )
    session.add(server)
    await session.flush()
    await session.refresh(server)
    return server


async def test_servers_come_back_ordered_by_name(
    session: AsyncSession, repository: SQLProxmoxServerRepository
) -> None:
    """The `ORDER BY` is the database's, not the caller's.

    Inserted out of order on purpose: the ambiguity `choices` an operator picks from present this
    sequence, and "the second one" should mean the same row on the next call.
    """
    await insert(session, "charlie")
    await insert(session, "alpha")
    await insert(session, "bravo")

    servers = await repository.list_servers()

    assert [server.name for server in servers] == ["alpha", "bravo", "charlie"]


async def test_an_empty_table_lists_nothing(repository: SQLProxmoxServerRepository) -> None:
    """No Proxmox servers configured is an empty list, not an error."""
    assert await repository.list_servers() == []


async def test_get_by_id_finds_the_row(
    session: AsyncSession, repository: SQLProxmoxServerRepository
) -> None:
    written = await insert(session, "pve1")

    found = await repository.get_by_id(written.id)

    assert found is not None
    assert found.id == written.id
    assert found.base_url == "https://pve1.example.net:8006"


async def test_get_by_id_answers_none_for_a_missing_row(
    repository: SQLProxmoxServerRepository,
) -> None:
    """`None`, not a raise: `resolve_proxmox_server_ref` turns it into `host_not_found`,
    and on the CHANGE path a raise here would reach an operator as a failed run rather than
    as a question they can answer."""
    assert await repository.get_by_id(uuid4()) is None


async def test_a_stored_row_carries_its_generated_identity(
    session: AsyncSession, repository: SQLProxmoxServerRepository
) -> None:
    """`id`, `created_at` and `updated_at` are server defaults, and the in-memory rows fill them
    by hand — this is the only place the real thing is checked."""
    written = await insert(session, "pve1")

    [server] = await repository.list_servers()

    assert server.id == written.id
    assert server.created_at is not None
    assert server.updated_at is not None


async def test_verify_ssl_defaults_off_for_a_row_that_does_not_set_it() -> None:
    """Proxmox's own default, and the opposite of WHM's.

    Asserted because it is a security-relevant value that no code path sets explicitly: a client
    is built straight off this column, so a migration that flipped the default would change how
    NOA validates a certificate with nothing else to notice.
    """
    # Deliberately a claim about the *column*, not about an instance: an unsaved instance carries
    # `None` here, and the database is what applies `server_default` — an ORM attribute set to
    # `None` inserts JSON `null`, not an omission, so it never touches the default.
    assert ProxmoxServer.__table__.c.verify_ssl.server_default.arg == "false"


async def test_verify_ssl_round_trips_when_it_is_set(
    session: AsyncSession, repository: SQLProxmoxServerRepository
) -> None:
    """The other half: a row that turns verification on keeps it on."""
    await insert(session, "pve1", verify_ssl=True)
    await insert(session, "pve2")

    strict, lenient = await repository.list_servers()

    assert strict.verify_ssl is True
    assert lenient.verify_ssl is False


async def test_a_stored_row_renders_safely(
    session: AsyncSession, repository: SQLProxmoxServerRepository
) -> None:
    """`to_safe_dict` on a real row: identifiers present, the secret absent.

    Not on `ProxmoxServerRowLike`'s account alone — the admin routes of the admin CRUD build
    will render one — but the row already has the method, so the guarantee is checked where
    the row is real.
    """
    await insert(session, "pve1")

    [server] = await repository.list_servers()
    rendered = server.to_safe_dict()

    assert rendered["base_url"] == "https://pve1.example.net:8006"
    assert rendered["has_api_token_secret"] is True
    assert PROXMOX_API_TOKEN_SECRET not in rendered.values()
    assert "api_token_secret" not in rendered
