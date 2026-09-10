"""`whm_servers.is_reseller_credential` against a live database.

Two things a double cannot tell you, so both are here rather than in
`test_admin_server_routes.py` (which owns the HTTP envelope over doubles):

1. **The migration is additive.** The assertion is about an INSERT written *before* the
   column existed, so it is a raw statement naming four columns and nothing else — an ORM
   insert would carry the attribute and prove only that Python can send `false`. The column's
   `DEFAULT false` is what makes "existing rows keep working at zero configuration change"
   true rather than intended.

2. **The V109(b) rule holds on the row the write produces**, not on the request body. A patch
   that flips the flag on carries neither `name` nor `api_username`, and a patch renaming an
   already-marked row carries only one of the two, so every refusal here is checked against
   the stored row — which means it needs a stored row.

Every refusal ships its accepting twin beside it: the same call with the flag off, or with the
name the rule asks for. A test that only shows a refusal passes just as well against a service
that refuses every reseller write, and `false` rows must stay unbound (sixteen root credentials
cannot all be named `root`, V109).

Scratch database, migrated with `alembic upgrade head`, dropped after — skipped rather than
failed when Postgres is unreachable, unless `NOA_REQUIRE_POSTGRES` says otherwise.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import InstrumentedAttribute

from core.db.models import WHMServer
from core.secrets.crypto import SecretCipher
from core.servers.admin_repository import (
    SQLWHMServerAdminRepository,
    WHMServerCreate,
    WHMServerUpdate,
)
from core.servers.admin_service import WHMServerAdminService
from core.servers.errors import WHMResellerCredentialNameMismatchError
from support.database import MUTATED_TABLES, migrated_database, truncate
from support.rbac import RecordingAuditSink
from support.secrets import build_cipher

SCRATCH_DB = "noa_whm_reseller_credential_test"

ACTOR = "admin@example.com"
API_TOKEN = "whm-api-token-plaintext"

# The reseller credential from the live inventory (§R.33): five of the seven owners on that
# host are `web08cpnpool0*`, and a reseller row is named after the one it holds.
RESELLER = "web08cpnpool01"


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    """A scratch database at `head`. Skips when Postgres is unreachable."""
    with migrated_database(SCRATCH_DB) as url:
        yield url


@pytest.fixture
async def session_factory(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Per-test engine, with the mutated tables emptied first.

    Function-scoped because an asyncpg connection belongs to the event loop that opened it, and
    each test gets its own loop.
    """
    await truncate(database_url, *MUTATED_TABLES)
    engine = create_async_engine(database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as opened:
        yield opened


@pytest.fixture
def cipher() -> SecretCipher:
    return build_cipher()


@pytest.fixture
def service(session: AsyncSession, cipher: SecretCipher) -> WHMServerAdminService:
    return WHMServerAdminService(
        repository=SQLWHMServerAdminRepository(session),
        cipher=cipher,
        audit_sink=RecordingAuditSink(),
    )


def whm_spec(name: str, *, api_username: str, **overrides: object) -> WHMServerCreate:
    """A create spec whose `api_token` is **plaintext**, as the service takes it."""
    values: dict[str, object] = {
        "name": name,
        "base_url": f"https://{name}.example.net:2087",
        "api_username": api_username,
        "api_token": API_TOKEN,
        "verify_ssl": True,
    }
    values.update(overrides)
    return WHMServerCreate(**values)  # type: ignore[arg-type]


async def _observed(
    session_factory: async_sessionmaker[AsyncSession],
    server_id: UUID,
    column: InstrumentedAttribute[Any],
) -> Any:
    """One stored column as a **separate** connection sees it, or `None` for no such row.

    Three readers over one statement rather than three copies of it, and it takes the
    mapped attribute rather than a column name so nothing interpolates a caller's string into
    SQL. The second connection is the point: a value read back through the session that wrote
    it cannot tell a commit from a flush (V100(c)).
    """
    async with session_factory() as observer:
        return await observer.scalar(sa.select(column).where(WHMServer.id == server_id))


async def observed_flag(
    session_factory: async_sessionmaker[AsyncSession], server_id: UUID
) -> bool | None:
    return await _observed(session_factory, server_id, WHMServer.is_reseller_credential)


async def observed_name(
    session_factory: async_sessionmaker[AsyncSession], server_id: UUID
) -> str | None:
    return await _observed(session_factory, server_id, WHMServer.name)


async def observed_api_username(
    session_factory: async_sessionmaker[AsyncSession], server_id: UUID
) -> str | None:
    return await _observed(session_factory, server_id, WHMServer.api_username)


# --- C12: the migration is additive ---


async def test_a_row_inserted_without_the_column_reads_false(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """An INSERT written before the column existed still succeeds, and the row is not a reseller.

    Raw SQL naming four columns, because that is the shape of the statement this migration
    must not break: `id`, the timestamps, `verify_ssl` and now `is_reseller_credential` are
    all filled by the database.
    """
    inserted = await session.execute(
        sa.text(
            "INSERT INTO whm_servers (name, base_url, api_username, api_token) "
            "VALUES ('legacy-root', 'https://legacy.example.net:2087', 'root', :token) "
            "RETURNING id, is_reseller_credential"
        ),
        {"token": API_TOKEN},
    )
    server_id, flag = inserted.one()
    await session.commit()

    assert flag is False
    assert await observed_flag(session_factory, server_id) is False


async def test_the_column_refuses_null(session: AsyncSession) -> None:
    """NOT NULL, so "unknown credential class" is not a state a writer can park a row in.

    The negative control for the test above: a nullable column with a default would satisfy
    it while letting an explicit `NULL` through, and a `NULL` flag would make V109(a)'s filter
    and V109(b)'s rule disagree about the same row.
    """
    with pytest.raises(sa.exc.IntegrityError):
        await session.execute(
            sa.text(
                "INSERT INTO whm_servers "
                "(name, base_url, api_username, api_token, is_reseller_credential) "
                "VALUES ('null-flag', 'https://null.example.net:2087', 'root', :token, NULL)"
            ),
            {"token": API_TOKEN},
        )


# --- V109(b) on create ---


async def test_a_reseller_create_not_named_after_its_api_username_is_refused(
    service: WHMServerAdminService, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The row an account CHANGE could never address, refused at the write (V109(b), V106)."""
    with pytest.raises(WHMResellerCredentialNameMismatchError) as refusal:
        await service.create(
            whm_spec("web08", api_username=RESELLER, is_reseller_credential=True),
            actor_email=ACTOR,
        )

    assert refusal.value.error_code == "whm_reseller_credential_name_mismatch"

    async with session_factory() as observer:
        count = await observer.scalar(sa.text("SELECT count(*) FROM whm_servers"))
    assert count == 0, "a refused create persisted a row"


async def test_a_reseller_create_named_after_its_api_username_is_stored(
    service: WHMServerAdminService, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The accepting twin. Without it, the refusal above passes against a blanket refusal."""
    created = await service.create(
        whm_spec(RESELLER, api_username=RESELLER, is_reseller_credential=True), actor_email=ACTOR
    )

    assert created.is_reseller_credential is True
    assert await observed_flag(session_factory, created.id) is True


async def test_a_root_row_may_be_named_anything(
    service: WHMServerAdminService, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """`false` rows are not bound: sixteen root credentials cannot all be named `root`.

    This is what keeps the rule from being unconditional — delete the `is_reseller_credential`
    guard and this test still passes, which is the point: it is the other half of the compare.
    """
    created = await service.create(whm_spec("web16", api_username="root"), actor_email=ACTOR)

    assert created.is_reseller_credential is False
    assert await observed_flag(session_factory, created.id) is False


async def test_the_compare_ignores_case_and_surrounding_whitespace(
    service: WHMServerAdminService,
) -> None:
    """`strip().lower()` on both sides — the normalisation V106's owner compare uses.

    An operator reads `Web08CpnPool01` off a WHM page and pastes it with a trailing space; the
    same credential either way. The third case is the separation control: a value that differs
    by an actual character is still refused, so this test cannot pass against a compare that
    normalises everything to equal.
    """
    cased = await service.create(
        whm_spec("Web08CpnPool01", api_username=RESELLER, is_reseller_credential=True),
        actor_email=ACTOR,
    )
    assert cased.is_reseller_credential is True

    padded = await service.create(
        whm_spec("web09cpnpool01", api_username="  web09cpnpool01 ", is_reseller_credential=True),
        actor_email=ACTOR,
    )
    assert padded.is_reseller_credential is True

    with pytest.raises(WHMResellerCredentialNameMismatchError):
        await service.create(
            whm_spec("web08cpnpool1", api_username=RESELLER, is_reseller_credential=True),
            actor_email=ACTOR,
        )


# --- V109(b) on update: the operands may both be stored columns ---


async def test_flipping_the_flag_on_alone_is_refused(
    service: WHMServerAdminService, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The patch that carries neither field, which is why the rule reads the stored row."""
    created = await service.create(whm_spec("web16", api_username=RESELLER), actor_email=ACTOR)

    with pytest.raises(WHMResellerCredentialNameMismatchError):
        await service.update(
            created.id, WHMServerUpdate(is_reseller_credential=True), actor_email=ACTOR
        )

    assert await observed_flag(session_factory, created.id) is False, "a refused patch was stored"


async def test_flipping_the_flag_on_alone_is_accepted_when_the_row_already_matches(
    service: WHMServerAdminService, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The accepting twin of the case above, on a row whose name already is its username."""
    created = await service.create(whm_spec(RESELLER, api_username=RESELLER), actor_email=ACTOR)

    updated = await service.update(
        created.id, WHMServerUpdate(is_reseller_credential=True), actor_email=ACTOR
    )

    assert updated.is_reseller_credential is True
    assert await observed_flag(session_factory, created.id) is True


async def test_renaming_a_reseller_row_away_from_its_api_username_is_refused(
    service: WHMServerAdminService, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """One operand from the patch, one from the row — and the stored name survives the refusal."""
    created = await service.create(
        whm_spec(RESELLER, api_username=RESELLER, is_reseller_credential=True), actor_email=ACTOR
    )

    with pytest.raises(WHMResellerCredentialNameMismatchError):
        await service.update(created.id, WHMServerUpdate(name="web08"), actor_email=ACTOR)

    assert await observed_name(session_factory, created.id) == RESELLER


async def test_moving_only_the_api_username_of_a_reseller_row_is_refused(
    service: WHMServerAdminService, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The mirror image: the row keeps its name and the credential moves out from under it.

    The stored value is read back for the reason B10 records one table over: an error answered
    over a write that landed is a green `pytest.raises` and a wrong row, so raising is half the
    assertion and "the column did not move" is the other half.
    """
    created = await service.create(
        whm_spec(RESELLER, api_username=RESELLER, is_reseller_credential=True), actor_email=ACTOR
    )

    with pytest.raises(WHMResellerCredentialNameMismatchError):
        await service.update(
            created.id, WHMServerUpdate(api_username="web08cpnpool02"), actor_email=ACTOR
        )

    assert await observed_api_username(session_factory, created.id) == RESELLER


async def test_a_reseller_row_may_move_both_together(
    service: WHMServerAdminService, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A rekeyed reseller row is a legitimate save, and this is what makes the two refusals
    above assertions about the *pair* rather than about editing a reseller row at all."""
    created = await service.create(
        whm_spec(RESELLER, api_username=RESELLER, is_reseller_credential=True), actor_email=ACTOR
    )

    updated = await service.update(
        created.id,
        WHMServerUpdate(name="web08cpnpool02", api_username="web08cpnpool02"),
        actor_email=ACTOR,
    )

    assert updated.name == "web08cpnpool02"
    assert await observed_name(session_factory, created.id) == "web08cpnpool02"


async def test_unmarking_a_row_frees_its_name(
    service: WHMServerAdminService, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Clearing the checkbox and renaming in one save is accepted, because the resulting row is
    a root credential and the rule is about the result (V109(b))."""
    created = await service.create(
        whm_spec(RESELLER, api_username=RESELLER, is_reseller_credential=True), actor_email=ACTOR
    )

    updated = await service.update(
        created.id,
        WHMServerUpdate(name="web08", is_reseller_credential=False),
        actor_email=ACTOR,
    )

    assert updated.is_reseller_credential is False
    assert updated.name == "web08"
    assert await observed_flag(session_factory, created.id) is False


async def test_an_unrelated_patch_leaves_a_reseller_row_alone(
    service: WHMServerAdminService, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A save that touches neither field keeps the flag: `None` means "leave alone" here too,
    so editing an SSH port cannot silently unmark a reseller credential."""
    created = await service.create(
        whm_spec(RESELLER, api_username=RESELLER, is_reseller_credential=True), actor_email=ACTOR
    )

    updated = await service.update(created.id, WHMServerUpdate(verify_ssl=False), actor_email=ACTOR)

    assert updated.is_reseller_credential is True
    assert await observed_flag(session_factory, created.id) is True
