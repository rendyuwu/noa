"""`SQLMcpTokenRepository` against a live database (T10 — C5, V2).

`test_mcp_token_service.py` covers policy with in-memory doubles; this covers the SQL.
Anything asserted here is something a fake cannot tell you: that the row really holds only
a digest, that the scoped delete really deletes, that `ON DELETE CASCADE` from `users`
(T4) really takes the tokens with it, and that ordering really comes from the column.

A scratch database is created, migrated with `alembic upgrade head`, and dropped — skipped
(never failed) when Postgres is unreachable, exactly as the other repository tests do.

One end-to-end case sits at the bottom: the real `McpTokenService` over this repository, so
V2's mint→list→revoke cycle is proved once against real SQL and not only against a double.

Below that, the two cases T53 added (V100). They are here rather than with the route tests
because the property is invisible to a double: an in-memory repository cannot roll back, so
"flushed" and "committed" read identically. Only a second connection separates them, and that
is what B10 turned out to hinge on.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.auth.auth_repository import SQLAuthRepository
from core.auth.mcp_token_repository import SQLMcpTokenRepository
from core.auth.mcp_token_service import (
    McpTokenService,
    generate_mcp_token,
    hash_mcp_token,
)
from core.db.models import McpToken, User
from support.database import MUTATED_TABLES, migrated_database, truncate
from support.mcp_tokens import LABEL, OTHER_LABEL
from support.rbac import RecordingAuditSink

SCRATCH_DB = "noa_mcp_token_repository_test"

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
def repository(session: AsyncSession) -> SQLMcpTokenRepository:
    return SQLMcpTokenRepository(session)


async def make_user(session: AsyncSession, email: str) -> User:
    """Create a user through T8's repository — the same path login uses."""
    return await SQLAuthRepository(session).create_user(
        email=email, ldap_dn=f"CN={email}", display_name=email, is_active=True
    )


# --- V2: what the row holds ---


async def test_insert_stores_only_the_sha256_hash(
    session: AsyncSession, repository: SQLMcpTokenRepository
) -> None:
    """V2: hashed at rest. Read back with raw SQL, so no ORM mapping can hide a column."""
    user = await make_user(session, EMAIL)
    plaintext = generate_mcp_token()

    view = await repository.insert(
        user_id=user.id,
        token_hash=hash_mcp_token(plaintext),
        token_prefix=plaintext[:12],
        label=LABEL,
        expires_at=None,
    )

    row = (
        (
            await session.execute(
                sa.text("SELECT * FROM mcp_tokens WHERE id = :id"), {"id": str(view.id)}
            )
        )
        .mappings()
        .one()
    )
    assert row["token_hash"] == hash_mcp_token(plaintext)
    assert len(row["token_hash"]) == 64
    # The plaintext appears in no column, not even truncated into the prefix.
    assert plaintext not in " ".join(str(value) for value in row.values())


async def test_insert_leaves_the_tofu_and_usage_columns_null(
    session: AsyncSession, repository: SQLMcpTokenRepository
) -> None:
    """C20/V3: `librechat_user_id` NULL at mint is what makes first-use binding possible."""
    user = await make_user(session, EMAIL)

    view = await repository.insert(
        user_id=user.id,
        token_hash=hash_mcp_token(generate_mcp_token()),
        token_prefix=PREFIX,
        label=None,
        expires_at=None,
    )

    assert view.librechat_user_id is None
    assert view.last_used_at is None
    assert view.last_ldap_check_at is None
    assert view.expires_at is None
    # Server default, readable in the same transaction because `insert` flushes.
    assert view.created_at is not None


async def test_duplicate_hash_is_refused_by_the_unique_constraint(
    session: AsyncSession, repository: SQLMcpTokenRepository
) -> None:
    """`uq_mcp_tokens_token_hash` (T4): one digest resolves to at most one operator."""
    user = await make_user(session, EMAIL)
    digest = hash_mcp_token(generate_mcp_token())
    await repository.insert(
        user_id=user.id, token_hash=digest, token_prefix=PREFIX, label=None, expires_at=None
    )

    with pytest.raises(sa.exc.IntegrityError):
        await repository.insert(
            user_id=user.id, token_hash=digest, token_prefix=PREFIX, label=None, expires_at=None
        )


# --- Reads ---


async def test_user_exists_reflects_the_row(
    session: AsyncSession, repository: SQLMcpTokenRepository
) -> None:
    user = await make_user(session, EMAIL)

    assert await repository.user_exists(user.id) is True
    assert await repository.user_exists(uuid4()) is False


async def test_list_for_user_is_newest_first_and_scoped(
    session: AsyncSession, repository: SQLMcpTokenRepository
) -> None:
    user = await make_user(session, EMAIL)
    other = await make_user(session, OTHER_EMAIL)
    first = await repository.insert(
        user_id=user.id,
        token_hash=hash_mcp_token(generate_mcp_token()),
        token_prefix=PREFIX,
        label=LABEL,
        expires_at=None,
    )
    second = await repository.insert(
        user_id=user.id,
        token_hash=hash_mcp_token(generate_mcp_token()),
        token_prefix=PREFIX,
        label=OTHER_LABEL,
        expires_at=None,
    )
    await repository.insert(
        user_id=other.id,
        token_hash=hash_mcp_token(generate_mcp_token()),
        token_prefix=PREFIX,
        label=None,
        expires_at=None,
    )

    listed = await repository.list_for_user(user.id)

    # Same-transaction inserts share `now()`, so the `id` tie-break decides — the point is
    # that the order is total, not which of the two comes first by clock.
    assert {view.id for view in listed} == {first.id, second.id}
    assert listed == sorted(listed, key=lambda view: (view.created_at, view.id.int), reverse=True)


async def test_list_for_a_user_with_no_tokens_is_empty(
    session: AsyncSession, repository: SQLMcpTokenRepository
) -> None:
    user = await make_user(session, EMAIL)

    assert await repository.list_for_user(user.id) == []


# --- V2: revoke = delete row ---


async def test_delete_removes_the_row(
    session: AsyncSession, repository: SQLMcpTokenRepository
) -> None:
    user = await make_user(session, EMAIL)
    view = await repository.insert(
        user_id=user.id,
        token_hash=hash_mcp_token(generate_mcp_token()),
        token_prefix=PREFIX,
        label=None,
        expires_at=None,
    )

    assert await repository.delete_for_user(user.id, view.id) is True

    count = await session.execute(sa.text("SELECT count(*) FROM mcp_tokens"))
    assert count.scalar_one() == 0


async def test_delete_will_not_reach_another_users_token(
    session: AsyncSession, repository: SQLMcpTokenRepository
) -> None:
    """Both ids are in the WHERE clause, so ownership is enforced by the query itself."""
    user = await make_user(session, EMAIL)
    other = await make_user(session, OTHER_EMAIL)
    theirs = await repository.insert(
        user_id=other.id,
        token_hash=hash_mcp_token(generate_mcp_token()),
        token_prefix=PREFIX,
        label=None,
        expires_at=None,
    )

    assert await repository.delete_for_user(user.id, theirs.id) is False

    assert len(await repository.list_for_user(other.id)) == 1


async def test_delete_of_a_missing_token_returns_false(
    session: AsyncSession, repository: SQLMcpTokenRepository
) -> None:
    user = await make_user(session, EMAIL)

    assert await repository.delete_for_user(user.id, uuid4()) is False


async def test_deleting_the_user_deletes_their_tokens(
    session: AsyncSession, repository: SQLMcpTokenRepository
) -> None:
    """`ON DELETE CASCADE` (T4): no token may outlive the operator it authenticates."""
    user = await make_user(session, EMAIL)
    await repository.insert(
        user_id=user.id,
        token_hash=hash_mcp_token(generate_mcp_token()),
        token_prefix=PREFIX,
        label=None,
        expires_at=None,
    )

    await session.delete(user)
    await session.flush()

    count = await session.execute(sa.text("SELECT count(*) FROM mcp_tokens"))
    assert count.scalar_one() == 0


# --- End to end over real SQL (V2) ---


async def test_service_mints_lists_and_revokes_over_real_sql(session: AsyncSession) -> None:
    """The whole V2 cycle against Postgres rather than a double."""
    service = McpTokenService(
        repository=SQLMcpTokenRepository(session),
        audit_sink=RecordingAuditSink(),
        ttl_seconds=3600,
    )
    user = await make_user(session, EMAIL)

    minted = await service.mint(user.id, label=LABEL, actor_email=EMAIL)

    listed = await service.list_for_user(user.id)
    assert [view.id for view in listed] == [minted.token.id]
    assert listed[0].label == LABEL
    assert listed[0].expires_at is not None

    stored_hash = (
        await session.execute(
            sa.text("SELECT token_hash FROM mcp_tokens WHERE id = :id"),
            {"id": str(minted.token.id)},
        )
    ).scalar_one()
    assert stored_hash == hash_mcp_token(minted.plaintext)

    await service.revoke(user.id, minted.token.id, actor_email=EMAIL)

    assert await service.list_for_user(user.id) == []


# --- T53: the transaction boundary, with a witness (V100) ---


async def test_commit_makes_a_mint_outlive_the_request(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """V100: flushed is not persisted, and only a second connection can tell the difference.

    The repository flushes; `commit()` is what `POST /admin/users/{id}/tokens` needs in order to
    mean anything, because `noa_api.api.deps.get_db_session` never commits. Inside this session
    both look identical — the flushed row reads back either way — so the assertion is made from
    a *separate* session. Without the boundary the route would answer 200 with a plaintext and
    the row would vanish at teardown: the operator holds a credential that authenticates
    nothing, and nothing anywhere reports an error.

    The direct `insert` is the negative control (V87, and B10's own shape): the same write
    through the repository alone, checked before anything commits, so a green result here
    cannot come from an observer that reads its own session or one that can see nothing at all.
    """
    service = McpTokenService(
        repository=SQLMcpTokenRepository(session), audit_sink=RecordingAuditSink()
    )
    user = await make_user(session, EMAIL)
    colleague = await make_user(session, OTHER_EMAIL)
    # Commit the two users, so the token writes below are the only ones under test.
    await session.commit()

    async def observed_token_ids(user_id: object) -> set[object]:
        async with session_factory() as observer:
            result = await observer.execute(
                sa.select(McpToken.id).where(McpToken.user_id == user_id)
            )
            return set(result.scalars().all())

    # Negative control: flush only.
    flushed = await SQLMcpTokenRepository(session).insert(
        user_id=colleague.id,
        token_hash=hash_mcp_token(generate_mcp_token()),
        token_prefix=PREFIX,
        label=OTHER_LABEL,
        expires_at=None,
    )
    assert await observed_token_ids(colleague.id) == set()

    minted = await service.mint(user.id, label=LABEL, actor_email=EMAIL)

    assert await observed_token_ids(user.id) == {minted.token.id}
    # The control's row is now visible too — the mint's commit ended the shared transaction.
    # Asserted so the control's silence above is read as "not yet committed", never as "this
    # observer cannot see inserts".
    assert flushed.id in await observed_token_ids(colleague.id)


async def test_commit_makes_a_revoke_outlive_the_request(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """V100(b): the same boundary on the write that *removes* a credential.

    The dangerous direction. An uncommitted revoke answers 200, the panel stops listing the
    token, and the row — with every request it authenticates — survives the request that
    deleted it.
    """
    service = McpTokenService(
        repository=SQLMcpTokenRepository(session), audit_sink=RecordingAuditSink()
    )
    user = await make_user(session, EMAIL)
    colleague = await make_user(session, OTHER_EMAIL)
    minted = await service.mint(user.id, label=LABEL, actor_email=EMAIL)
    theirs = await service.mint(colleague.id, label=OTHER_LABEL, actor_email=EMAIL)

    async def observed_token_ids(user_id: object) -> set[object]:
        async with session_factory() as observer:
            result = await observer.execute(
                sa.select(McpToken.id).where(McpToken.user_id == user_id)
            )
            return set(result.scalars().all())

    assert await observed_token_ids(user.id) == {minted.token.id}

    # Negative control: the identical delete through the repository alone, flush only.
    assert await SQLMcpTokenRepository(session).delete_for_user(colleague.id, theirs.token.id)
    assert await observed_token_ids(colleague.id) == {theirs.token.id}

    await service.revoke(user.id, minted.token.id, actor_email=EMAIL)

    assert await observed_token_ids(user.id) == set()
    assert await observed_token_ids(colleague.id) == set()
