"""`SQLMcpIdentityRepository` against a live database.

`test_mcp_identity_resolver.py` covers policy with in-memory doubles; this covers the SQL.
Anything asserted here is something a fake cannot tell you: that the join really reads
`users.is_active` in the same statement as the token row, that the TOFU bind really is a
compare-and-set at the database rather than in Python, that a revoke really deletes only
this operator's rows, and that a commit really makes them durable across sessions.

A scratch database is created, migrated with `alembic upgrade head`, and dropped — skipped
(never failed) when Postgres is unreachable, exactly as the other repository tests do.

One end-to-end case sits at the bottom: the real `McpIdentityResolver` over this repository
and a real minted token, so a mint and a verify are proved to agree on the
digest against real SQL rather than only against a double.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.auth.auth_repository import SQLAuthRepository
from core.auth.mcp_auth_errors import McpUserNotInDirectoryError
from core.auth.mcp_identity import McpIdentityResolver
from core.auth.mcp_token_repository import SQLMcpIdentityRepository, SQLMcpTokenRepository
from core.auth.mcp_token_service import (
    McpTokenService,
    generate_mcp_token,
    hash_mcp_token,
)
from core.db.models import User
from support.database import MUTATED_TABLES, migrated_database, truncate
from support.mcp_identity import LIBRECHAT_USER, OTHER_LIBRECHAT_USER, FakeDirectory
from support.mcp_tokens import LABEL
from support.rbac import RecordingAuditSink

SCRATCH_DB = "noa_mcp_identity_repository_test"

EMAIL = "operator@example.com"
OTHER_EMAIL = "colleague@example.com"

PREFIX = "noa_abcd1234"


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    """A scratch database at `head`. Skips when Postgres is unreachable."""
    with migrated_database(SCRATCH_DB) as url:
        yield url


@pytest.fixture
async def session_factory(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Per-test engine, with the mutated tables emptied first."""
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
def repository(session: AsyncSession) -> SQLMcpIdentityRepository:
    return SQLMcpIdentityRepository(session)


async def make_user(session: AsyncSession, email: str, *, is_active: bool = True) -> User:
    """Create a user through the login flow's repository — the same path login uses."""
    return await SQLAuthRepository(session).create_user(
        email=email, ldap_dn=f"CN={email}", display_name=email, is_active=is_active
    )


async def make_token(
    session: AsyncSession,
    user: User,
    *,
    librechat_user_id: str | None = None,
    expires_at: datetime | None = None,
    last_ldap_check_at: datetime | None = None,
) -> tuple[str, sa.Row[tuple[object, ...]]]:
    """Insert one token through the token repository and return its plaintext plus row id."""
    plaintext = generate_mcp_token()
    view = await SQLMcpTokenRepository(session).insert(
        user_id=user.id,
        token_hash=hash_mcp_token(plaintext),
        token_prefix=plaintext[:12],
        label=LABEL,
        expires_at=expires_at,
    )
    if librechat_user_id is not None or last_ldap_check_at is not None:
        await session.execute(
            sa.text(
                "UPDATE mcp_tokens SET librechat_user_id = :binding, "
                "last_ldap_check_at = :checked WHERE id = :id"
            ),
            {
                "binding": librechat_user_id,
                "checked": last_ldap_check_at,
                "id": str(view.id),
            },
        )
        await session.flush()
    return plaintext, view  # type: ignore[return-value]


# --- The join that resolves a caller ---


async def test_lookup_by_digest_returns_the_user_fields(
    session: AsyncSession, repository: SQLMcpIdentityRepository
) -> None:
    """One statement, so `is_active` describes the same instant the token was found."""
    user = await make_user(session, EMAIL)
    plaintext, view = await make_token(session, user)

    row = await repository.get_by_token_hash(hash_mcp_token(plaintext))

    assert row is not None
    assert row.token_id == view.id
    assert row.user_id == user.id
    assert row.email == EMAIL
    assert row.display_name == EMAIL
    assert row.is_active is True
    assert row.librechat_user_id is None
    assert row.last_ldap_check_at is None


async def test_lookup_reflects_a_disabled_user(
    session: AsyncSession, repository: SQLMcpIdentityRepository
) -> None:
    """The join reads the live row, so a disable lands on the very next request."""
    user = await make_user(session, EMAIL)
    plaintext, _ = await make_token(session, user)

    user.is_active = False
    await session.flush()

    row = await repository.get_by_token_hash(hash_mcp_token(plaintext))

    assert row is not None
    assert row.is_active is False


async def test_lookup_of_an_unknown_digest_is_none(
    session: AsyncSession, repository: SQLMcpIdentityRepository
) -> None:
    user = await make_user(session, EMAIL)
    await make_token(session, user)

    assert await repository.get_by_token_hash(hash_mcp_token(generate_mcp_token())) is None


async def test_the_returned_row_carries_no_digest(
    session: AsyncSession, repository: SQLMcpIdentityRepository
) -> None:
    """`token_hash` is a WHERE clause, not a field anything downstream can leak."""
    user = await make_user(session, EMAIL)
    plaintext, _ = await make_token(session, user)

    row = await repository.get_by_token_hash(hash_mcp_token(plaintext))

    assert row is not None
    assert not hasattr(row, "token_hash")
    assert hash_mcp_token(plaintext) not in str(row)


# --- TOFU binding is a compare-and-set in SQL ---


async def test_bind_pins_an_unbound_token(
    session: AsyncSession, repository: SQLMcpIdentityRepository
) -> None:
    user = await make_user(session, EMAIL)
    _, view = await make_token(session, user)

    assert await repository.bind_librechat_user(view.id, LIBRECHAT_USER) == LIBRECHAT_USER

    stored = await session.execute(
        sa.text("SELECT librechat_user_id FROM mcp_tokens WHERE id = :id"), {"id": str(view.id)}
    )
    assert stored.scalar_one() == LIBRECHAT_USER


async def test_bind_will_not_overwrite_an_existing_binding(
    session: AsyncSession, repository: SQLMcpIdentityRepository
) -> None:
    """The compare-and-set: `WHERE librechat_user_id IS NULL` is what closes the race.

    The loser of two concurrent first calls gets the winner's value back and the resolver
    turns that into a mismatch — a read-then-write in Python could not.
    """
    user = await make_user(session, EMAIL)
    _, view = await make_token(session, user, librechat_user_id=LIBRECHAT_USER)

    returned = await repository.bind_librechat_user(view.id, OTHER_LIBRECHAT_USER)

    assert returned == LIBRECHAT_USER


async def test_bind_of_a_vanished_token_returns_no_match(
    session: AsyncSession, repository: SQLMcpIdentityRepository
) -> None:
    """A row deleted mid-request must not resolve to the caller's own header value."""
    returned = await repository.bind_librechat_user(uuid4(), LIBRECHAT_USER)

    assert returned == ""
    assert returned != LIBRECHAT_USER


# --- Usage and revalidation stamps ---


async def test_touches_write_the_timestamps(
    session: AsyncSession, repository: SQLMcpIdentityRepository
) -> None:
    user = await make_user(session, EMAIL)
    _, view = await make_token(session, user)
    moment = datetime.now(UTC).replace(microsecond=0)

    await repository.touch_last_used(view.id, now=moment)
    await repository.touch_ldap_check(view.id, now=moment)

    row = (
        (
            await session.execute(
                sa.text("SELECT last_used_at, last_ldap_check_at FROM mcp_tokens WHERE id = :id"),
                {"id": str(view.id)},
            )
        )
        .mappings()
        .one()
    )
    assert row["last_used_at"] == moment
    assert row["last_ldap_check_at"] == moment


# --- Cascade revoke ---


async def test_delete_takes_every_token_of_one_operator(
    session: AsyncSession, repository: SQLMcpIdentityRepository
) -> None:
    """Revocation fires on the *operator* leaving, so a second token cannot survive the
    first."""
    user = await make_user(session, EMAIL)
    colleague = await make_user(session, OTHER_EMAIL)
    await make_token(session, user)
    await make_token(session, user)
    await make_token(session, colleague)

    assert await repository.delete_tokens_for_user(user.id) == 2

    remaining = await session.execute(sa.text("SELECT user_id FROM mcp_tokens"))
    assert [row[0] for row in remaining.all()] == [colleague.id]


async def test_delete_for_an_operator_with_no_tokens_is_zero(
    session: AsyncSession, repository: SQLMcpIdentityRepository
) -> None:
    user = await make_user(session, EMAIL)

    assert await repository.delete_tokens_for_user(user.id) == 0


async def test_commit_makes_a_binding_visible_to_another_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The verify path owns its transaction, so `commit()` has to be real.

    Without it a binding would live only inside `verify_token`'s session and vanish, and
    every request would look like a first use — TOFU that never pins anything.
    """
    async with session_factory() as writer:
        user = await make_user(writer, EMAIL)
        _, view = await make_token(writer, user)
        repository = SQLMcpIdentityRepository(writer)
        await repository.bind_librechat_user(view.id, LIBRECHAT_USER)
        await repository.commit()

    async with session_factory() as reader:
        stored = await reader.execute(
            sa.text("SELECT librechat_user_id FROM mcp_tokens WHERE id = :id"),
            {"id": str(view.id)},
        )
        assert stored.scalar_one() == LIBRECHAT_USER


# --- End to end over real SQL: mint then verify ---


async def test_a_minted_token_resolves_and_binds_over_real_sql(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The mint and verify paths agree on the digest, and TOFU pins on the first call."""
    async with session_factory() as session:
        user = await make_user(session, EMAIL)
        minted = await McpTokenService(
            repository=SQLMcpTokenRepository(session),
            audit_sink=RecordingAuditSink(),
        ).mint(user.id, label=LABEL, actor_email=EMAIL)
        await session.commit()

    async with session_factory() as session:
        resolver = McpIdentityResolver(
            repository=SQLMcpIdentityRepository(session),
            directory=FakeDirectory(),
            ldap_revalidate_seconds=900,
        )
        identity = await resolver.resolve(
            presented_token=minted.plaintext, librechat_user_id=LIBRECHAT_USER
        )

    assert identity.user_id == user.id
    assert identity.token_id == minted.token.id
    assert identity.email == EMAIL
    assert identity.librechat_user_id == LIBRECHAT_USER

    async with session_factory() as session:
        listed = await SQLMcpTokenRepository(session).list_for_user(user.id)
    assert listed[0].librechat_user_id == LIBRECHAT_USER
    assert listed[0].last_used_at is not None
    assert listed[0].last_ldap_check_at is not None


async def test_a_departed_operator_loses_every_token_over_real_sql(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Cascade revoke, committed: the rows are gone in a fresh session."""
    async with session_factory() as session:
        user = await make_user(session, EMAIL)
        plaintext, _ = await make_token(session, user, librechat_user_id=LIBRECHAT_USER)
        await make_token(session, user)
        await session.commit()

    async with session_factory() as session:
        resolver = McpIdentityResolver(
            repository=SQLMcpIdentityRepository(session),
            directory=FakeDirectory(present=False),
            ldap_revalidate_seconds=900,
        )
        with pytest.raises(McpUserNotInDirectoryError):
            await resolver.resolve(presented_token=plaintext, librechat_user_id=LIBRECHAT_USER)

    async with session_factory() as session:
        count = await session.execute(sa.text("SELECT count(*) FROM mcp_tokens"))
        assert count.scalar_one() == 0


async def test_a_fresh_check_skips_the_directory_over_real_sql(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`last_ldap_check_at` inside the staleness interval means no LDAP round trip at all."""
    async with session_factory() as session:
        user = await make_user(session, EMAIL)
        plaintext, _ = await make_token(
            session,
            user,
            librechat_user_id=LIBRECHAT_USER,
            last_ldap_check_at=datetime.now(UTC) - timedelta(seconds=60),
        )
        await session.commit()

    directory = FakeDirectory()
    async with session_factory() as session:
        resolver = McpIdentityResolver(
            repository=SQLMcpIdentityRepository(session),
            directory=directory,
            ldap_revalidate_seconds=900,
        )
        await resolver.resolve(presented_token=plaintext, librechat_user_id=LIBRECHAT_USER)

    assert directory.call_count == 0
