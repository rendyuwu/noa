"""`SQLAuthRepository` / `SQLLoginRateLimitRepository` against a live database.

The doubles in `support.auth` cover policy; these cover the SQL. Anything asserted here is something
a fake cannot tell you: that `ON CONFLICT` really upserts, that a `commit()` really survives into
another session, that `flush()` really fills a server-generated `users.id`.

A scratch database is created, migrated with `alembic upgrade head`, and dropped —
skipped (never failed) when Postgres is unreachable, exactly as `test_migrations.py`
does, so the suite still runs without Docker. Tables are truncated per test rather than
wrapped in a rollback, because a rollback would hide the commit behaviour under test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.auth.auth_repository import SQLAuthRepository, SQLLoginRateLimitRepository
from core.auth.login_rate_limiter import SCOPE_EMAIL, SCOPE_IP
from core.db.models import ADMIN_ROLE_NAME
from support.database import MUTATED_TABLES, migrated_database, truncate

SCRATCH_DB = "noa_auth_repository_test"

EMAIL = "operator@example.com"
OTHER_EMAIL = "colleague@example.com"
DN = "CN=Operator,OU=Staff,dc=example,dc=com"
DISPLAY_NAME = "Example Operator"

IP = "203.0.113.7"
T0 = datetime(2026, 8, 6, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    """A scratch database at `head`. Skips when Postgres is unreachable."""
    with migrated_database(SCRATCH_DB) as url:
        yield url


@pytest.fixture
async def session_factory(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Per-test engine, with the mutated tables emptied first.

    The engine is function-scoped because an asyncpg connection belongs to the event loop
    that opened it, and each test gets its own loop.
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


# --- Users ---


async def test_create_user_fills_the_server_generated_id(session: AsyncSession) -> None:
    """`flush()` not `commit()`: the caller needs `user.id` for role assignment."""
    repository = SQLAuthRepository(session)

    user = await repository.create_user(
        email=EMAIL, ldap_dn=DN, display_name=DISPLAY_NAME, is_active=False
    )

    assert user.id is not None
    assert user.created_at is not None


async def test_created_user_defaults_to_inactive(session: AsyncSession) -> None:
    """A provisioned row waits for an admin, at the SQL level."""
    repository = SQLAuthRepository(session)

    user = await repository.create_user(
        email=EMAIL, ldap_dn=DN, display_name=DISPLAY_NAME, is_active=False
    )

    assert user.is_active is False


async def test_get_user_by_email_and_by_id_find_the_same_row(session: AsyncSession) -> None:
    repository = SQLAuthRepository(session)
    created = await repository.create_user(
        email=EMAIL, ldap_dn=DN, display_name=DISPLAY_NAME, is_active=True
    )

    by_email = await repository.get_user_by_email(EMAIL)
    by_id = await repository.get_user_by_id(created.id)

    assert by_email is not None
    assert by_id is not None
    assert by_email.id == by_id.id == created.id


async def test_get_user_by_unknown_id_returns_none(session: AsyncSession) -> None:
    """The session re-read treats this as "session gone", so it must be `None`, never a raise."""
    assert await SQLAuthRepository(session).get_user_by_id(uuid4()) is None


async def test_get_user_by_unknown_email_returns_none(session: AsyncSession) -> None:
    assert await SQLAuthRepository(session).get_user_by_email("nobody@example.com") is None


async def test_update_user_patches_only_what_is_passed(session: AsyncSession) -> None:
    """`None` means "leave alone" — a login only ever has fresher values to write."""
    repository = SQLAuthRepository(session)
    user = await repository.create_user(
        email=EMAIL, ldap_dn=DN, display_name=DISPLAY_NAME, is_active=True
    )

    await repository.update_user(user, display_name="Renamed")

    assert user.display_name == "Renamed"
    assert user.ldap_dn == DN
    assert user.is_active is True


async def test_update_user_can_set_is_active_false(session: AsyncSession) -> None:
    """`False is not None`, so disabling works — the RBAC engine depends on it."""
    repository = SQLAuthRepository(session)
    user = await repository.create_user(
        email=EMAIL, ldap_dn=DN, display_name=DISPLAY_NAME, is_active=True
    )

    await repository.update_user(user, is_active=False)

    assert user.is_active is False


async def test_duplicate_email_is_rejected_by_the_database(session: AsyncSession) -> None:
    """One row per operator, enforced below the application (the schema's unique index)."""
    repository = SQLAuthRepository(session)
    await repository.create_user(email=EMAIL, ldap_dn=DN, display_name=None, is_active=True)

    with pytest.raises(sa.exc.IntegrityError):
        await repository.create_user(email=EMAIL, ldap_dn=DN, display_name=None, is_active=True)


# --- Commit boundary ---


async def test_commit_persists_the_row_into_another_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The property the inactive-by-default rule rests on: a provisioned row survives the
    pending-approval raise.

    Only a real transaction can show this. `AuthService` commits before its activation
    gate precisely so the row an admin has to enable is already durable.
    """
    async with session_factory() as writing:
        repository = SQLAuthRepository(writing)
        await repository.create_user(
            email=EMAIL, ldap_dn=DN, display_name=DISPLAY_NAME, is_active=False
        )
        await repository.commit()

    async with session_factory() as reading:
        found = await SQLAuthRepository(reading).get_user_by_email(EMAIL)

    assert found is not None
    assert found.is_active is False


async def test_uncommitted_work_does_not_persist(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The other half: without the commit the row is gone, so the test above means something."""
    async with session_factory() as writing:
        await SQLAuthRepository(writing).create_user(
            email=EMAIL, ldap_dn=DN, display_name=DISPLAY_NAME, is_active=False
        )

    async with session_factory() as reading:
        assert await SQLAuthRepository(reading).get_user_by_email(EMAIL) is None


# --- Roles ---


async def test_ensure_role_is_idempotent(session: AsyncSession) -> None:
    repository = SQLAuthRepository(session)

    assert await repository.ensure_role(ADMIN_ROLE_NAME) == ADMIN_ROLE_NAME
    assert await repository.ensure_role(ADMIN_ROLE_NAME) == ADMIN_ROLE_NAME

    count = await session.scalar(
        sa.text("SELECT count(*) FROM roles WHERE name = :name"), {"name": ADMIN_ROLE_NAME}
    )
    assert count == 1


async def test_assign_role_is_idempotent(session: AsyncSession) -> None:
    """Every bootstrap-admin login re-assigns; the composite PK must not be tripped."""
    repository = SQLAuthRepository(session)
    user = await repository.create_user(email=EMAIL, ldap_dn=DN, display_name=None, is_active=True)
    await repository.ensure_role(ADMIN_ROLE_NAME)

    await repository.assign_role(user.id, ADMIN_ROLE_NAME)
    await repository.assign_role(user.id, ADMIN_ROLE_NAME)

    assert await repository.get_role_names(user.id) == [ADMIN_ROLE_NAME]


async def test_assign_unknown_role_is_a_no_op(session: AsyncSession) -> None:
    """A missing role means "no permission", never a 500 on the login path."""
    repository = SQLAuthRepository(session)
    user = await repository.create_user(email=EMAIL, ldap_dn=DN, display_name=None, is_active=True)

    await repository.assign_role(user.id, "role-that-does-not-exist")

    assert await repository.get_role_names(user.id) == []


async def test_get_role_names_is_sorted(session: AsyncSession) -> None:
    """Stable order so a response body never changes between identical requests."""
    repository = SQLAuthRepository(session)
    user = await repository.create_user(email=EMAIL, ldap_dn=DN, display_name=None, is_active=True)
    for role in ("zulu", "alpha", "mike"):
        await repository.ensure_role(role)
        await repository.assign_role(user.id, role)

    assert await repository.get_role_names(user.id) == ["alpha", "mike", "zulu"]


async def test_role_names_are_scoped_to_the_user(session: AsyncSession) -> None:
    repository = SQLAuthRepository(session)
    user = await repository.create_user(email=EMAIL, ldap_dn=DN, display_name=None, is_active=True)
    other = await repository.create_user(
        email=OTHER_EMAIL, ldap_dn=DN, display_name=None, is_active=True
    )
    await repository.ensure_role(ADMIN_ROLE_NAME)
    await repository.assign_role(user.id, ADMIN_ROLE_NAME)

    assert await repository.get_role_names(other.id) == []


# --- Rate-limit buckets ---


async def test_get_bucket_returns_none_when_absent(session: AsyncSession) -> None:
    assert await SQLLoginRateLimitRepository(session).get_bucket(SCOPE_IP, IP) is None


async def test_upsert_inserts_then_updates_the_same_row(session: AsyncSession) -> None:
    """`ON CONFLICT DO UPDATE` is the point: two concurrent attempts must not both insert.

    A plain `INSERT` would trip `uq_login_rate_limits_scope_key` and turn a second
    simultaneous failed login into a 500 — while leaving the counter unincremented.
    """
    repository = SQLLoginRateLimitRepository(session)

    first = await repository.upsert_bucket(
        SCOPE_IP, IP, attempt_count=1, window_started_at=T0, blocked_until=None
    )
    second = await repository.upsert_bucket(
        SCOPE_IP, IP, attempt_count=2, window_started_at=T0, blocked_until=None
    )

    assert first.attempt_count == 1
    assert second.attempt_count == 2

    count = await session.scalar(sa.text("SELECT count(*) FROM login_rate_limits"))
    assert count == 1


async def test_upsert_returns_the_stored_row_including_block_end(
    session: AsyncSession,
) -> None:
    """`RETURNING` gives the committed state, never the caller's optimistic guess."""
    repository = SQLLoginRateLimitRepository(session)
    blocked_until = T0 + timedelta(seconds=600)

    bucket = await repository.upsert_bucket(
        SCOPE_EMAIL, EMAIL, attempt_count=5, window_started_at=T0, blocked_until=blocked_until
    )

    assert bucket.blocked_until == blocked_until
    assert bucket.window_started_at == T0


async def test_ip_and_email_buckets_are_separate_rows(session: AsyncSession) -> None:
    """Same key string, different scope: two independent counters."""
    repository = SQLLoginRateLimitRepository(session)

    await repository.upsert_bucket(
        SCOPE_IP, EMAIL, attempt_count=1, window_started_at=T0, blocked_until=None
    )
    await repository.upsert_bucket(
        SCOPE_EMAIL, EMAIL, attempt_count=9, window_started_at=T0, blocked_until=None
    )

    ip_bucket = await repository.get_bucket(SCOPE_IP, EMAIL)
    email_bucket = await repository.get_bucket(SCOPE_EMAIL, EMAIL)
    assert ip_bucket is not None and ip_bucket.attempt_count == 1
    assert email_bucket is not None and email_bucket.attempt_count == 9


async def test_clear_bucket_removes_only_its_own_key(session: AsyncSession) -> None:
    repository = SQLLoginRateLimitRepository(session)
    await repository.upsert_bucket(
        SCOPE_IP, IP, attempt_count=3, window_started_at=T0, blocked_until=None
    )
    await repository.upsert_bucket(
        SCOPE_EMAIL, EMAIL, attempt_count=3, window_started_at=T0, blocked_until=None
    )

    await repository.clear_bucket(SCOPE_IP, IP)

    assert await repository.get_bucket(SCOPE_IP, IP) is None
    assert await repository.get_bucket(SCOPE_EMAIL, EMAIL) is not None


async def test_clear_absent_bucket_is_a_no_op(session: AsyncSession) -> None:
    """`record_success` clears unconditionally, so absence must not be an error."""
    await SQLLoginRateLimitRepository(session).clear_bucket(SCOPE_IP, "198.51.100.1")


async def test_bucket_survives_a_commit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The rate limiter's counters must outlive the failed request that created them.

    `AuthService` commits after `record_failure` for exactly this reason: the error path
    rolls the session back, and a rolled-back counter is a limiter that never limits.
    """
    async with session_factory() as writing:
        await SQLLoginRateLimitRepository(writing).upsert_bucket(
            SCOPE_IP, IP, attempt_count=4, window_started_at=T0, blocked_until=None
        )
        await writing.commit()

    async with session_factory() as reading:
        bucket = await SQLLoginRateLimitRepository(reading).get_bucket(SCOPE_IP, IP)

    assert bucket is not None
    assert bucket.attempt_count == 4
