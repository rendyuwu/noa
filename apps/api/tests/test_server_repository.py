"""`SQLServerRepository` against a live database, over all three inventory tables.

One generic class binds the table with a parameter (`SQLServerRepository(session, model=…)`), so
the SQL is proved once and re-run per table. `test_server_reference.py` and the tool suites cover
policy over in-memory doubles; this covers what a fake cannot tell you:

- that `ORDER BY name` is really the database's ordering and not the double's `sorted()`, which
  is what the resolver's tie handling and the tool's output order both assume;
- that `get_by_id` really answers `None` for an id that is not there, rather than raising the way
  `scalar_one()` would — on the CHANGE path a raise would reach an operator as a failed run
  rather than as a question they can answer;
- that a row round-trips with its server-generated `id` and timestamps present — the three
  columns the in-memory rows have to fill by hand.

Each table's `to_safe_dict` shape is its own, so those three stay plain tests at the bottom,
beside the one value that is Proxmox's alone: `verify_ssl` has a **server default of `false`**
(the column comment says why — Proxmox ships a self-signed certificate), the opposite of WHM's.

A scratch database is created, migrated with `alembic upgrade head`, and dropped — skipped
(never failed) when Postgres is unreachable, so the suite still runs without Docker.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.db.models import PMGServer, ProxmoxServer, WHMServer
from core.servers.repository import SQLServerRepository
from support.database import MUTATED_TABLES, migrated_database, truncate
from support.servers import API_TOKEN, PROXMOX_API_TOKEN_SECRET, SSH_PASSWORD

SCRATCH_DB = "noa_test_server_repository"


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


@dataclass(frozen=True)
class Table:
    """One inventory table and the columns an insert writes to it.

    `model` is `Any` rather than the three-way union it really is: `SQLServerRepository`'s
    `ServerModelT` is a *constrained* TypeVar, so it accepts each of the three and rejects a
    union of them. The `columns` builder below names which table each one is.
    """

    model: Any
    columns: Callable[[str], dict[str, Any]]


WHM_TABLE = Table(
    model=WHMServer,
    columns=lambda name: {
        "name": name,
        "base_url": f"https://{name}.example.net:2087",
        "api_username": "root",
        "api_token": API_TOKEN,
        "verify_ssl": True,
        "ssh_username": "noa",
        "ssh_password": SSH_PASSWORD,
    },
)
PMG_TABLE = Table(
    model=PMGServer,
    columns=lambda name: {
        "name": name,
        "ssh_host": f"{name}.example.net",
        "ssh_username": "noa-ops",
        "ssh_password": SSH_PASSWORD,
        "ssh_host_key_fingerprint": "SHA256:pinned",
    },
)
# No `verify_ssl` key on purpose: the column default is what the two Proxmox tests below observe.
PROXMOX_TABLE = Table(
    model=ProxmoxServer,
    columns=lambda name: {
        "name": name,
        "base_url": f"https://{name}.example.net:8006",
        "api_token_id": "root@pam!noa",
        "api_token_secret": PROXMOX_API_TOKEN_SECRET,
    },
)


async def insert(session: AsyncSession, table: Table, name: str, **overrides: Any) -> Any:
    """One row, written the way the admin routes will."""
    server = table.model(**{**table.columns(name), **overrides})
    session.add(server)
    await session.flush()
    await session.refresh(server)
    return server


TABLES = pytest.mark.parametrize(
    "table", [WHM_TABLE, PMG_TABLE, PROXMOX_TABLE], ids=lambda table: table.model.__tablename__
)


# --- What every inventory table owes its callers ---


@TABLES
async def test_servers_come_back_ordered_by_name(session: AsyncSession, table: Table) -> None:
    """The `ORDER BY` is the database's, not the caller's.

    Inserted out of order on purpose: the ambiguity `choices` an operator picks from and the
    tool's listing both present this sequence, and "row three" should mean the same row on the
    next call.
    """
    repository = SQLServerRepository(session, model=table.model)
    await insert(session, table, "charlie")
    await insert(session, table, "alpha")
    await insert(session, table, "bravo")

    servers = await repository.list_servers()

    assert [server.name for server in servers] == ["alpha", "bravo", "charlie"]


@TABLES
async def test_an_empty_table_lists_nothing(session: AsyncSession, table: Table) -> None:
    """No servers configured is an empty list, not an error."""
    repository = SQLServerRepository(session, model=table.model)

    assert await repository.list_servers() == []


@TABLES
async def test_get_by_id_finds_the_row(session: AsyncSession, table: Table) -> None:
    repository = SQLServerRepository(session, model=table.model)
    written = await insert(session, table, "alpha")

    found = await repository.get_by_id(written.id)

    assert found is not None
    assert found.id == written.id
    assert found.name == "alpha"


@TABLES
async def test_get_by_id_answers_none_for_a_missing_row(
    session: AsyncSession, table: Table
) -> None:
    """`None`, not a raise: the resolver turns it into `host_not_found`."""
    repository = SQLServerRepository(session, model=table.model)

    assert await repository.get_by_id(uuid4()) is None


@TABLES
async def test_a_stored_row_carries_its_generated_identity(
    session: AsyncSession, table: Table
) -> None:
    """`id`, `created_at` and `updated_at` are filled at insert, never by the caller, and the
    in-memory rows fill them by hand — this is the only place the real thing is checked."""
    repository = SQLServerRepository(session, model=table.model)
    written = await insert(session, table, "alpha")

    [server] = await repository.list_servers()

    assert server.id == written.id
    assert server.created_at is not None
    assert server.updated_at is not None


# --- What each table renders outward ---


async def test_a_stored_whm_row_renders_safely(session: AsyncSession) -> None:
    """`to_safe_dict` on a real row: identifiers present, credentials absent.

    Run against a row the insert path generated, because `id`, `created_at` and `updated_at`
    are never the caller's — an in-memory double fills them by hand, and this is the only place
    that check is against the real thing.
    """
    repository = SQLServerRepository(session, model=WHMServer)
    written = await insert(session, WHM_TABLE, "alpha")

    [server] = await repository.list_servers()
    rendered = server.to_safe_dict()

    assert rendered["id"] == str(written.id)
    assert rendered["created_at"] is not None
    assert rendered["has_api_token"] is True
    assert API_TOKEN not in rendered.values()
    assert SSH_PASSWORD not in rendered.values()
    assert "api_token" not in rendered


async def test_a_stored_pmg_row_renders_safely(session: AsyncSession) -> None:
    """`to_safe_dict` on a real row: identifiers present, credentials absent.

    Nothing exposed over MCP renders a PMG row, but the admin server-CRUD routes do, and the row
    already has the method, so the guarantee is checked where the row is real.
    """
    repository = SQLServerRepository(session, model=PMGServer)
    await insert(session, PMG_TABLE, "pmg1")

    [server] = await repository.list_servers()
    rendered = server.to_safe_dict()

    assert rendered["ssh_host"] == "pmg1.example.net"
    assert rendered["has_ssh_password"] is True
    assert SSH_PASSWORD not in rendered.values()
    assert "ssh_password" not in rendered


async def test_a_stored_proxmox_row_renders_safely(session: AsyncSession) -> None:
    """`to_safe_dict` on a real row: identifiers present, the secret absent."""
    repository = SQLServerRepository(session, model=ProxmoxServer)
    await insert(session, PROXMOX_TABLE, "pve1")

    [server] = await repository.list_servers()
    rendered = server.to_safe_dict()

    assert rendered["base_url"] == "https://pve1.example.net:8006"
    assert rendered["has_api_token_secret"] is True
    assert PROXMOX_API_TOKEN_SECRET not in rendered.values()
    assert "api_token_secret" not in rendered


# --- Proxmox's own TLS default ---


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


async def test_verify_ssl_round_trips_when_it_is_set(session: AsyncSession) -> None:
    """The other half: a row that turns verification on keeps it on."""
    repository = SQLServerRepository(session, model=ProxmoxServer)
    await insert(session, PROXMOX_TABLE, "pve1", verify_ssl=True)
    await insert(session, PROXMOX_TABLE, "pve2")

    strict, lenient = await repository.list_servers()

    assert strict.verify_ssl is True
    assert lenient.verify_ssl is False
