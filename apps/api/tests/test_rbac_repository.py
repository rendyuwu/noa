"""`SQLAuthorizationRepository` against a live database (T9).

`test_rbac_engine.py` covers policy with in-memory doubles; this covers the SQL. Anything
asserted here is something a fake cannot tell you: that replacing a role's grants really
deletes the old rows, that `ON DELETE CASCADE` really removes a deleted role's grants and
assignments, that the internal-role subquery really spares `user:` assignments, and that
`count_active_admin_users` really counts distinct users.

A scratch database is created, migrated with `alembic upgrade head`, and dropped — skipped
(never failed) when Postgres is unreachable, exactly as `test_auth_repository.py` does, so
the suite still runs without Docker.

One end-to-end case sits at the bottom: the real `AuthorizationService` over this
repository, so V10/V11 are proved once against real SQL and not only against the double.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.auth.auth_repository import SQLAuthRepository
from core.auth.authorization_repository import SQLAuthorizationRepository
from core.auth.authorization_service import AuthorizationService
from core.auth.mcp_token_repository import SQLMcpTokenRepository
from core.auth.mcp_token_service import generate_mcp_token, hash_mcp_token
from core.db.models import ADMIN_ROLE_NAME, INTERNAL_ROLE_PREFIX, User
from support.database import MUTATED_TABLES, migrated_database, truncate
from support.rbac import (
    ROLE_NOC,
    ROLE_SUPPORT,
    TOOL_CHANGE,
    TOOL_READ,
    TOOL_UNKNOWN,
    RecordingAuditSink,
)

SCRATCH_DB = "noa_rbac_repository_test"

EMAIL = "operator@example.com"
OTHER_EMAIL = "colleague@example.com"
ADMIN_EMAIL = "admin@example.com"


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    """A scratch database at `head`. Skips when Postgres is unreachable."""
    with migrated_database(SCRATCH_DB) as url:
        yield url


@pytest.fixture
async def session_factory(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Per-test engine, with the mutated tables emptied first.

    Function-scoped because an asyncpg connection belongs to the event loop that opened it,
    and each test gets its own loop.
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
def repository(session: AsyncSession) -> SQLAuthorizationRepository:
    return SQLAuthorizationRepository(session)


async def make_user(
    session: AsyncSession, email: str, *, is_active: bool = True, roles: tuple[str, ...] = ()
) -> User:
    """Create a user with roles through T8's repository — the same path login uses."""
    auth_repository = SQLAuthRepository(session)
    user = await auth_repository.create_user(
        email=email, ldap_dn=f"CN={email}", display_name=email, is_active=is_active
    )
    for role in roles:
        await auth_repository.ensure_role(role)
        await auth_repository.assign_role(user.id, role)
    return user


# --- Grant reads (V10) ---


async def test_get_role_tool_names_unions_across_roles(
    repository: SQLAuthorizationRepository,
) -> None:
    await repository.ensure_role(ROLE_SUPPORT)
    await repository.ensure_role(ROLE_NOC)
    await repository.replace_role_tool_permissions(ROLE_SUPPORT, [TOOL_READ])
    await repository.replace_role_tool_permissions(ROLE_NOC, [TOOL_CHANGE, TOOL_READ])

    assert await repository.get_role_tool_names([ROLE_SUPPORT, ROLE_NOC]) == sorted(
        [TOOL_READ, TOOL_CHANGE]
    )


async def test_get_role_tool_names_for_no_roles_is_empty(
    repository: SQLAuthorizationRepository,
) -> None:
    """Short-circuits instead of emitting `IN ()`."""
    assert await repository.get_role_tool_names([]) == []


async def test_get_role_tool_names_ignores_unknown_role_names(
    repository: SQLAuthorizationRepository,
) -> None:
    assert await repository.get_role_tool_names(["role-that-never-existed"]) == []


# --- Role CRUD (V13) ---


async def test_ensure_role_is_idempotent(repository: SQLAuthorizationRepository) -> None:
    assert await repository.ensure_role(ROLE_SUPPORT) == ROLE_SUPPORT
    assert await repository.ensure_role(ROLE_SUPPORT) == ROLE_SUPPORT
    assert await repository.list_assignable_role_names() == [ROLE_SUPPORT]


async def test_list_assignable_role_names_excludes_internal_roles(
    repository: SQLAuthorizationRepository,
) -> None:
    """V13: `user:`-prefixed roles are NOA's own and never offered to an admin."""
    await repository.ensure_role(ROLE_SUPPORT)
    await repository.ensure_role(ADMIN_ROLE_NAME)
    await repository.ensure_role(f"{INTERNAL_ROLE_PREFIX}{uuid4()}")

    assert await repository.list_assignable_role_names() == [ADMIN_ROLE_NAME, ROLE_SUPPORT]


async def test_role_exists_reflects_the_row(repository: SQLAuthorizationRepository) -> None:
    assert await repository.role_exists(ROLE_SUPPORT) is False
    await repository.ensure_role(ROLE_SUPPORT)
    assert await repository.role_exists(ROLE_SUPPORT) is True


async def test_delete_role_cascades_grants_and_assignments(
    session: AsyncSession, repository: SQLAuthorizationRepository
) -> None:
    """The FK cascades from T4 do the work — no orphan grant can resolve later."""
    user = await make_user(session, EMAIL, roles=(ROLE_SUPPORT,))
    await repository.replace_role_tool_permissions(ROLE_SUPPORT, [TOOL_READ])

    assert await repository.delete_role(ROLE_SUPPORT) is True

    assert await repository.get_role_names(user.id) == []
    grant_count = await session.execute(sa.text("SELECT count(*) FROM role_tool_permissions"))
    assert grant_count.scalar_one() == 0


async def test_delete_missing_role_returns_false(repository: SQLAuthorizationRepository) -> None:
    assert await repository.delete_role(ROLE_SUPPORT) is False


async def test_list_existing_role_names_names_only_what_exists(
    repository: SQLAuthorizationRepository,
) -> None:
    await repository.ensure_role(ROLE_SUPPORT)

    assert await repository.list_existing_role_names([ROLE_SUPPORT, ROLE_NOC]) == [ROLE_SUPPORT]
    assert await repository.list_existing_role_names([]) == []


# --- Grant writes (V14) ---


async def test_replace_role_tool_permissions_deletes_the_old_rows(
    repository: SQLAuthorizationRepository,
) -> None:
    await repository.ensure_role(ROLE_SUPPORT)
    await repository.replace_role_tool_permissions(ROLE_SUPPORT, [TOOL_READ, TOOL_CHANGE])

    await repository.replace_role_tool_permissions(ROLE_SUPPORT, [TOOL_CHANGE])

    assert await repository.get_role_tool_names_for_role(ROLE_SUPPORT) == [TOOL_CHANGE]


async def test_replace_role_tool_permissions_strips_and_deduplicates(
    repository: SQLAuthorizationRepository,
) -> None:
    """Otherwise the unique constraint on `(role_id, tool_name)` would reject the batch."""
    await repository.ensure_role(ROLE_SUPPORT)

    await repository.replace_role_tool_permissions(
        ROLE_SUPPORT, [TOOL_READ, f" {TOOL_READ} ", "", "   "]
    )

    assert await repository.get_role_tool_names_for_role(ROLE_SUPPORT) == [TOOL_READ]


async def test_replace_role_tool_permissions_for_missing_role_is_a_no_op(
    session: AsyncSession, repository: SQLAuthorizationRepository
) -> None:
    """The 404 decision belongs to the service; duplicating it here would split it."""
    await repository.replace_role_tool_permissions(ROLE_SUPPORT, [TOOL_READ])

    grant_count = await session.execute(sa.text("SELECT count(*) FROM role_tool_permissions"))
    assert grant_count.scalar_one() == 0


async def test_grants_can_hold_a_tool_the_catalog_no_longer_knows(
    repository: SQLAuthorizationRepository,
) -> None:
    """`tool_name` is a plain string (T4), which is why V10's filter lives in the service."""
    await repository.ensure_role(ROLE_SUPPORT)

    await repository.replace_role_tool_permissions(ROLE_SUPPORT, [TOOL_UNKNOWN])

    assert await repository.get_role_tool_names_for_role(ROLE_SUPPORT) == [TOOL_UNKNOWN]


# --- Role assignment (V13, V75) ---


async def test_replace_user_assignable_roles_sets_exactly_those_roles(
    session: AsyncSession, repository: SQLAuthorizationRepository
) -> None:
    user = await make_user(session, EMAIL, roles=(ROLE_SUPPORT,))
    await repository.ensure_role(ROLE_NOC)

    await repository.replace_user_assignable_roles(user.id, [ROLE_NOC])

    assert await repository.get_role_names(user.id) == [ROLE_NOC]


async def test_replace_user_assignable_roles_preserves_internal_roles(
    session: AsyncSession, repository: SQLAuthorizationRepository
) -> None:
    """V13/V75 at the SQL level: the delete is scoped by role name, not by the input list."""
    internal_role = f"{INTERNAL_ROLE_PREFIX}legacy"
    user = await make_user(session, EMAIL, roles=(ROLE_SUPPORT, internal_role))

    await repository.replace_user_assignable_roles(user.id, [])

    assert await repository.get_role_names(user.id) == [internal_role]


async def test_replace_user_assignable_roles_is_repeatable(
    session: AsyncSession, repository: SQLAuthorizationRepository
) -> None:
    """Re-applying the same set must not trip the `(user_id, role_id)` unique constraint."""
    user = await make_user(session, EMAIL, roles=(ROLE_SUPPORT,))

    await repository.replace_user_assignable_roles(user.id, [ROLE_SUPPORT])
    await repository.replace_user_assignable_roles(user.id, [ROLE_SUPPORT])

    assert await repository.get_role_names(user.id) == [ROLE_SUPPORT]


async def test_replace_user_assignable_roles_skips_names_with_no_role(
    session: AsyncSession, repository: SQLAuthorizationRepository
) -> None:
    """A dangling assignment is worse than a dropped one; the service rejects these first."""
    user = await make_user(session, EMAIL)

    await repository.replace_user_assignable_roles(user.id, ["role-that-never-existed"])

    assert await repository.get_role_names(user.id) == []


# --- User administration (V12) ---


async def test_update_user_active_writes_the_row(
    session: AsyncSession, repository: SQLAuthorizationRepository
) -> None:
    user = await make_user(session, EMAIL, is_active=False)

    updated = await repository.update_user_active(user.id, is_active=True)

    assert updated is not None and updated.is_active is True
    reread = await repository.get_user_by_id(user.id)
    assert reread is not None and reread.is_active is True


async def test_update_user_active_for_missing_user_returns_none(
    repository: SQLAuthorizationRepository,
) -> None:
    assert await repository.update_user_active(uuid4(), is_active=True) is None


async def test_count_active_admin_users_counts_only_active_admins(
    session: AsyncSession, repository: SQLAuthorizationRepository
) -> None:
    await make_user(session, ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))
    await make_user(session, OTHER_EMAIL, is_active=False, roles=(ADMIN_ROLE_NAME,))
    await make_user(session, EMAIL, roles=(ROLE_SUPPORT,))

    assert await repository.count_active_admin_users() == 1


async def test_count_active_admin_users_counts_a_user_once(
    session: AsyncSession, repository: SQLAuthorizationRepository
) -> None:
    """`DISTINCT` on the user id: extra roles multiply the join, not the count."""
    await make_user(session, ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME, ROLE_SUPPORT, ROLE_NOC))

    assert await repository.count_active_admin_users() == 1


async def test_delete_user_removes_the_row_and_its_assignments(
    session: AsyncSession, repository: SQLAuthorizationRepository
) -> None:
    user = await make_user(session, EMAIL, roles=(ROLE_SUPPORT,))

    assert await repository.delete_user(user.id) is True

    assert await repository.get_user_by_id(user.id) is None
    assignment_count = await session.execute(sa.text("SELECT count(*) FROM user_roles"))
    assert assignment_count.scalar_one() == 0


async def test_delete_missing_user_returns_false(repository: SQLAuthorizationRepository) -> None:
    assert await repository.delete_user(uuid4()) is False


# --- V4 cascade revoke on disable (T11) ---


async def test_delete_mcp_tokens_removes_only_that_users_rows(
    session: AsyncSession, repository: SQLAuthorizationRepository
) -> None:
    """V4: disabling one operator must not touch a colleague's credentials."""
    user = await make_user(session, EMAIL)
    colleague = await make_user(session, OTHER_EMAIL)
    token_repository = SQLMcpTokenRepository(session)
    for owner in (user, user, colleague):
        await token_repository.insert(
            user_id=owner.id,
            token_hash=hash_mcp_token(generate_mcp_token()),
            token_prefix="noa_abcd1234",
            label=None,
            expires_at=None,
        )

    assert await repository.delete_mcp_tokens_for_user(user.id) == 2

    assert await token_repository.list_for_user(user.id) == []
    assert len(await token_repository.list_for_user(colleague.id)) == 1


async def test_delete_mcp_tokens_for_a_user_with_none_is_zero(
    session: AsyncSession, repository: SQLAuthorizationRepository
) -> None:
    user = await make_user(session, EMAIL)

    assert await repository.delete_mcp_tokens_for_user(user.id) == 0


async def test_disabling_a_user_revokes_their_tokens_over_real_sql(
    session: AsyncSession,
) -> None:
    """The whole V4 admin path: `set_user_active(False)` really empties `mcp_tokens`.

    Through the service, not the repository, because the rule is that no caller can
    forget the revoke — a route calling `update_user_active` directly is the bug this
    guards.
    """
    service = AuthorizationService(
        repository=SQLAuthorizationRepository(session), audit_sink=RecordingAuditSink()
    )
    user = await make_user(session, EMAIL, roles=(ROLE_SUPPORT,))
    await SQLMcpTokenRepository(session).insert(
        user_id=user.id,
        token_hash=hash_mcp_token(generate_mcp_token()),
        token_prefix="noa_abcd1234",
        label=None,
        expires_at=None,
    )

    updated = await service.set_user_active(user.id, is_active=False, actor_email=ADMIN_EMAIL)

    assert updated.is_active is False
    count = await session.execute(sa.text("SELECT count(*) FROM mcp_tokens"))
    assert count.scalar_one() == 0


async def test_list_users_is_ordered_by_email(
    session: AsyncSession, repository: SQLAuthorizationRepository
) -> None:
    await make_user(session, EMAIL)
    await make_user(session, ADMIN_EMAIL)

    assert [user.email for user in await repository.list_users()] == [ADMIN_EMAIL, EMAIL]


# --- End to end over real SQL (V10, V11) ---


async def test_service_resolves_permissions_over_real_sql(session: AsyncSession) -> None:
    """The engine's V10/V11 branches, proved once against Postgres rather than a double."""
    repository = SQLAuthorizationRepository(session)
    service = AuthorizationService(repository=repository, audit_sink=RecordingAuditSink())

    operator = await make_user(session, EMAIL, roles=(ROLE_SUPPORT,))
    admin = await make_user(session, ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))
    await service.set_role_tools(ROLE_SUPPORT, [TOOL_READ], actor_email=ADMIN_EMAIL)

    assert await service.get_permitted_tools(operator.id) == {TOOL_READ}
    assert await service.authorize_tool(admin.id, TOOL_CHANGE) is True

    await repository.update_user_active(operator.id, is_active=False)

    assert await service.get_permitted_tools(operator.id) == set()
