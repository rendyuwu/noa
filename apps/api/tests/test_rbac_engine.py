"""`AuthorizationService` policy (T9 — V6, V10, V11, V12, V13, V14).

Postgres is not required: the service runs against `support.rbac`'s in-memory repository,
so every assertion here is about policy rather than SQL. `SQLAuthorizationRepository` has
its own coverage in `test_rbac_repository.py`.

Two invariants are about *when* the row is read, not what it says — V6 (permission
resolution reads the row, never the cookie's claims) and V14 (permission updates take
effect immediately). Those tests mutate the store behind a held reference and assert the
next call disagrees with the last one.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from core.audit.admin_events import (
    EVENT_ROLE_CREATED,
    EVENT_ROLE_DELETED,
    EVENT_ROLE_TOOLS_UPDATED,
    EVENT_USER_DELETED,
    EVENT_USER_ROLES_UPDATED,
    EVENT_USER_STATUS_UPDATED,
)
from core.auth.authorization_errors import (
    InternalRoleError,
    InvalidRoleNameError,
    LastActiveAdminError,
    ReservedRoleError,
    RoleNotFoundError,
    SelfDeactivateAdminError,
    SelfDeleteAdminError,
    SelfDeleteError,
    SelfRemoveAdminRoleError,
    UnknownRoleError,
    UnknownToolError,
    UserNotFoundError,
)
from core.auth.tool_catalog import NEVER_IMPLEMENT_TOOLS, TOOL_CATALOG, is_known_tool
from core.db.models import ADMIN_ROLE_NAME
from support.rbac import (
    INTERNAL_ROLE,
    ROLE_NOC,
    ROLE_SUPPORT,
    TOOL_CHANGE,
    TOOL_READ,
    TOOL_UNKNOWN,
    RbacFixture,
    build_service,
)

ADMIN_EMAIL = "admin@example.com"
OTHER_ADMIN_EMAIL = "second-admin@example.com"
OPERATOR_EMAIL = "operator@example.com"

# §I.mcp's exposed list, transcribed independently of `core.auth.tool_catalog` so the
# conformance test compares the spec against the code rather than the code against itself.
SPEC_EXPOSED_TOOLS = {
    "whm_suspend_account",
    "whm_unsuspend_account",
    "whm_list_accounts",
    "whm_search_accounts",
    "whm_preflight_firewall_entries",
    "whm_firewall_release_and_allow",
    "whm_firewall_allowlist_remove",
    "proxmox_reset_vm_password",
    "proxmox_vm_nic",
    "pmg_whitelist",
    "pmg_whitelist_list",
    "pmg_whitelist_search",
    "whm_list_servers",
    "noa_get_action_result",
}


@pytest.fixture
def rbac() -> RbacFixture:
    return build_service()


# --- Catalog (V10, C22, I.mcp) ---


def test_catalog_matches_spec_exposed_tools() -> None:
    """§I.mcp lists 14 exposed tools; the catalog is that list, exactly.

    Fails when a tool lands in code without a spec row or vice versa — the drift T13 will
    otherwise inherit silently when the catalog becomes registry-derived.
    """
    assert TOOL_CATALOG == SPEC_EXPOSED_TOOLS
    assert len(TOOL_CATALOG) == 14


def test_catalog_excludes_never_implement_tools() -> None:
    """C22 is a policy boundary: those names must not be grantable."""
    assert TOOL_CATALOG.isdisjoint(NEVER_IMPLEMENT_TOOLS)
    for tool_name in NEVER_IMPLEMENT_TOOLS:
        assert not is_known_tool(tool_name)


def test_catalog_membership_is_exact_not_normalized() -> None:
    """A grant is stored as written; case-folding it would break dispatch (V10)."""
    assert is_known_tool(TOOL_READ)
    assert not is_known_tool(TOOL_READ.upper())
    assert not is_known_tool(f" {TOOL_READ} ")


async def test_list_tools_answers_the_services_own_catalog(rbac: RbacFixture) -> None:
    """T52: the vocabulary a grant may name, off the same set that validates a write (V10).

    Read against a *narrowed* `known_tools`, not the default: an implementation that returned
    `TOOL_CATALOG` directly would pass an equality with the default and hand a caller names
    `set_role_tools` refuses.
    """
    narrowed = build_service(known_tools=frozenset({TOOL_READ}))

    assert await rbac.service.list_tools() == sorted(TOOL_CATALOG)
    assert await narrowed.service.list_tools() == [TOOL_READ]


async def test_list_tools_commits_nothing(rbac: RbacFixture) -> None:
    """A read ends no transaction, for the same reason it logs no event (V14, V100)."""
    await rbac.service.list_tools()

    assert rbac.repository.commits == 0
    assert rbac.audit.events == []


# --- Permission resolution (V10, V11) ---


async def test_role_grant_resolves_to_permitted_tools(rbac: RbacFixture) -> None:
    rbac.repository.grant(ROLE_SUPPORT, TOOL_READ)
    user = rbac.repository.add_user(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,))

    assert await rbac.service.get_permitted_tools(user.id) == {TOOL_READ}


async def test_admin_role_gets_every_known_tool(rbac: RbacFixture) -> None:
    """V10: `admin` bypasses the grant table entirely — no rows needed."""
    admin = rbac.repository.add_user(ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))

    assert await rbac.service.get_permitted_tools(admin.id) == set(TOOL_CATALOG)
    assert rbac.repository.role_tools == {}


async def test_admin_authorize_rejects_unregistered_tool(rbac: RbacFixture) -> None:
    """V10's other half: the bypass covers known tools only."""
    admin = rbac.repository.add_user(ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))

    assert await rbac.service.authorize_tool(admin.id, TOOL_READ)
    assert not await rbac.service.authorize_tool(admin.id, TOOL_UNKNOWN)


async def test_grant_for_unknown_tool_is_not_permitted(rbac: RbacFixture) -> None:
    """A stale grant — a tool renamed or dropped — resolves to no permission (V10).

    `role_tool_permissions.tool_name` is a plain string (T4), so nothing at the database
    level stops the row from outliving the tool.
    """
    rbac.repository.grant(ROLE_SUPPORT, TOOL_UNKNOWN, TOOL_READ)
    user = rbac.repository.add_user(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,))

    assert await rbac.service.get_permitted_tools(user.id) == {TOOL_READ}
    assert not await rbac.service.authorize_tool(user.id, TOOL_UNKNOWN)


async def test_unknown_tool_is_refused_without_reading_the_row(rbac: RbacFixture) -> None:
    """`authorize_tool` checks the catalog first, so an injected name costs no query."""
    user = rbac.repository.add_user(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,))
    reads_before = rbac.repository.user_reads

    assert not await rbac.service.authorize_tool(user.id, TOOL_UNKNOWN)
    assert rbac.repository.user_reads == reads_before


async def test_permitted_tools_union_across_roles(rbac: RbacFixture) -> None:
    rbac.repository.grant(ROLE_SUPPORT, TOOL_READ)
    rbac.repository.grant(ROLE_NOC, TOOL_CHANGE)
    user = rbac.repository.add_user(OPERATOR_EMAIL, roles=(ROLE_SUPPORT, ROLE_NOC))

    assert await rbac.service.get_permitted_tools(user.id) == {TOOL_READ, TOOL_CHANGE}


async def test_user_without_roles_has_no_permitted_tools(rbac: RbacFixture) -> None:
    user = rbac.repository.add_user(OPERATOR_EMAIL)

    assert await rbac.service.get_permitted_tools(user.id) == set()


async def test_disabled_user_has_zero_permitted_tools(rbac: RbacFixture) -> None:
    """V11: `is_active=False` means zero permissions regardless of roles."""
    rbac.repository.grant(ROLE_SUPPORT, TOOL_READ, TOOL_CHANGE)
    user = rbac.repository.add_user(OPERATOR_EMAIL, is_active=False, roles=(ROLE_SUPPORT,))

    assert await rbac.service.get_permitted_tools(user.id) == set()
    assert not await rbac.service.authorize_tool(user.id, TOOL_READ)


async def test_disabled_admin_has_zero_permitted_tools(rbac: RbacFixture) -> None:
    """V11 is checked before V10's bypass, so a disabled admin gets nothing."""
    admin = rbac.repository.add_user(ADMIN_EMAIL, is_active=False, roles=(ADMIN_ROLE_NAME,))

    assert await rbac.service.get_permitted_tools(admin.id) == set()


async def test_permitted_tools_raise_for_a_deleted_user(rbac: RbacFixture) -> None:
    """A missing row is `UserNotFoundError`, not an empty tool set.

    Fail-closed either way, but the caller owes a deleted operator a 401 (T12) rather than
    an empty list that reads like a role misconfiguration.
    """
    with pytest.raises(UserNotFoundError):
        await rbac.service.get_permitted_tools(uuid4())


# --- V6: resolution reads the row, not the claims ---


async def test_permitted_tools_reread_row_after_disable(rbac: RbacFixture) -> None:
    """V6: the row is re-read per call, so a disable lands on the next request.

    The session JWT has no revocation path before `exp`, so this re-read is the only thing
    bounding a disabled operator's live session. A cached tool set would defeat it.
    """
    rbac.repository.grant(ROLE_SUPPORT, TOOL_READ)
    user = rbac.repository.add_user(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,))

    assert await rbac.service.get_permitted_tools(user.id) == {TOOL_READ}

    user.is_active = False  # what an admin's disable does to the row

    assert await rbac.service.get_permitted_tools(user.id) == set()


async def test_each_permission_check_reads_the_user_row(rbac: RbacFixture) -> None:
    """No memoization: two checks, two reads (V6, V14)."""
    rbac.repository.grant(ROLE_SUPPORT, TOOL_READ)
    user = rbac.repository.add_user(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,))

    await rbac.service.authorize_tool(user.id, TOOL_READ)
    reads_after_first = rbac.repository.user_reads
    await rbac.service.authorize_tool(user.id, TOOL_READ)

    assert rbac.repository.user_reads == reads_after_first + 1


async def test_grant_change_takes_effect_on_next_call(rbac: RbacFixture) -> None:
    """V14: permission updates are effective immediately, with no cache to invalidate."""
    rbac.repository.grant(ROLE_SUPPORT, TOOL_READ)
    user = rbac.repository.add_user(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,))

    assert await rbac.service.authorize_tool(user.id, TOOL_CHANGE) is False

    await rbac.service.set_role_tools(ROLE_SUPPORT, [TOOL_READ, TOOL_CHANGE])

    assert await rbac.service.authorize_tool(user.id, TOOL_CHANGE) is True

    await rbac.service.set_role_tools(ROLE_SUPPORT, [])

    assert await rbac.service.get_permitted_tools(user.id) == set()


# --- Resolved user shape ---


async def test_resolve_user_carries_roles_and_effective_tools(rbac: RbacFixture) -> None:
    rbac.repository.grant(ROLE_SUPPORT, TOOL_READ)
    user = rbac.repository.add_user(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,))

    resolved = await rbac.service.resolve_user(user.id)

    assert resolved.email == OPERATOR_EMAIL
    assert resolved.roles == [ROLE_SUPPORT]
    assert resolved.tools == [TOOL_READ]
    assert resolved.is_active is True


async def test_list_users_is_ordered_and_carries_effective_tools(rbac: RbacFixture) -> None:
    rbac.repository.grant(ROLE_SUPPORT, TOOL_READ)
    rbac.repository.add_user(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,))
    rbac.repository.add_user(ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))

    users = await rbac.service.list_users()

    assert [user.email for user in users] == [ADMIN_EMAIL, OPERATOR_EMAIL]
    assert users[0].tools == sorted(TOOL_CATALOG)
    assert users[1].tools == [TOOL_READ]


# --- Role CRUD (V13) ---


async def test_create_role_then_list_includes_it(rbac: RbacFixture) -> None:
    await rbac.service.create_role(ROLE_SUPPORT, actor_email=ADMIN_EMAIL)

    assert await rbac.service.list_roles() == [ROLE_SUPPORT]


async def test_create_role_is_idempotent_and_records_one_event(rbac: RbacFixture) -> None:
    """Re-creating an existing role changes nothing, so it logs nothing (V14)."""
    await rbac.service.create_role(ROLE_SUPPORT, actor_email=ADMIN_EMAIL)
    await rbac.service.create_role(ROLE_SUPPORT, actor_email=ADMIN_EMAIL)

    assert rbac.audit.event_types == [EVENT_ROLE_CREATED]


async def test_internal_roles_are_not_listed_as_assignable(rbac: RbacFixture) -> None:
    """V13: internal `user:` roles are NOA's bookkeeping, never offered to an admin."""
    user = rbac.repository.add_user(OPERATOR_EMAIL)
    rbac.repository.assign_internal_role(user.id, INTERNAL_ROLE)
    await rbac.service.create_role(ROLE_SUPPORT, actor_email=ADMIN_EMAIL)

    assert await rbac.service.list_roles() == [ROLE_SUPPORT]


@pytest.mark.parametrize("name", ["", "   ", "a" * 101, "has space", "user:legacy", "../admin"])
async def test_invalid_role_names_are_rejected(rbac: RbacFixture, name: str) -> None:
    with pytest.raises(InvalidRoleNameError):
        await rbac.service.create_role(name, actor_email=ADMIN_EMAIL)


async def test_admin_role_cannot_be_created(rbac: RbacFixture) -> None:
    with pytest.raises(ReservedRoleError):
        await rbac.service.create_role(ADMIN_ROLE_NAME, actor_email=ADMIN_EMAIL)


async def test_admin_role_cannot_be_deleted(rbac: RbacFixture) -> None:
    """V13: `admin` is reserved."""
    with pytest.raises(ReservedRoleError):
        await rbac.service.delete_role(ADMIN_ROLE_NAME, actor_email=ADMIN_EMAIL)


async def test_admin_role_tools_cannot_be_set(rbac: RbacFixture) -> None:
    """V13: editing `admin`'s grants would imply a limit V10 does not enforce."""
    with pytest.raises(ReservedRoleError):
        await rbac.service.set_role_tools(ADMIN_ROLE_NAME, [TOOL_READ], actor_email=ADMIN_EMAIL)


async def test_admin_role_tools_read_as_the_whole_catalog(rbac: RbacFixture) -> None:
    """Displayed state equals enforced state: `admin` permits everything (V10)."""
    assert await rbac.service.get_role_tools(ADMIN_ROLE_NAME) == sorted(TOOL_CATALOG)


async def test_delete_role_removes_it_and_its_grants(rbac: RbacFixture) -> None:
    rbac.repository.grant(ROLE_SUPPORT, TOOL_READ)
    user = rbac.repository.add_user(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,))

    await rbac.service.delete_role(ROLE_SUPPORT, actor_email=ADMIN_EMAIL)

    assert await rbac.service.list_roles() == []
    assert await rbac.service.get_permitted_tools(user.id) == set()


async def test_delete_missing_role_raises_not_found(rbac: RbacFixture) -> None:
    with pytest.raises(RoleNotFoundError):
        await rbac.service.delete_role(ROLE_SUPPORT, actor_email=ADMIN_EMAIL)


async def test_get_role_tools_for_missing_role_raises_not_found(rbac: RbacFixture) -> None:
    with pytest.raises(RoleNotFoundError):
        await rbac.service.get_role_tools(ROLE_SUPPORT)


async def test_set_role_tools_replaces_the_whole_set(rbac: RbacFixture) -> None:
    rbac.repository.grant(ROLE_SUPPORT, TOOL_READ)

    stored = await rbac.service.set_role_tools(ROLE_SUPPORT, [TOOL_CHANGE], actor_email=ADMIN_EMAIL)

    assert stored == [TOOL_CHANGE]


async def test_set_role_tools_rejects_unknown_tools_as_a_set(rbac: RbacFixture) -> None:
    """V10: unknown names are named back, all of them, so a typo is one round trip."""
    rbac.repository.grant(ROLE_SUPPORT)

    with pytest.raises(UnknownToolError) as excinfo:
        await rbac.service.set_role_tools(
            ROLE_SUPPORT, [TOOL_READ, TOOL_UNKNOWN, "pmg_nope"], actor_email=ADMIN_EMAIL
        )

    assert excinfo.value.unknown_tools == ["pmg_nope", TOOL_UNKNOWN]
    # Nothing was written: the grant set is unchanged.
    assert await rbac.service.get_role_tools(ROLE_SUPPORT) == []


async def test_set_role_tools_for_missing_role_raises_not_found(rbac: RbacFixture) -> None:
    with pytest.raises(RoleNotFoundError):
        await rbac.service.set_role_tools(ROLE_SUPPORT, [TOOL_READ], actor_email=ADMIN_EMAIL)


# --- Role assignment (V13) ---


async def test_set_user_roles_replaces_assignable_roles(rbac: RbacFixture) -> None:
    rbac.repository.grant(ROLE_SUPPORT, TOOL_READ)
    rbac.repository.grant(ROLE_NOC, TOOL_CHANGE)
    user = rbac.repository.add_user(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,))

    updated = await rbac.service.set_user_roles(user.id, [ROLE_NOC], actor_email=ADMIN_EMAIL)

    assert updated.roles == [ROLE_NOC]
    assert updated.tools == [TOOL_CHANGE]


async def test_role_replacement_preserves_internal_roles(rbac: RbacFixture) -> None:
    """V13/V75: internal roles survive an admin replacing a user's roles."""
    rbac.repository.grant(ROLE_SUPPORT, TOOL_READ)
    user = rbac.repository.add_user(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,))
    rbac.repository.assign_internal_role(user.id, INTERNAL_ROLE)

    updated = await rbac.service.set_user_roles(user.id, [], actor_email=ADMIN_EMAIL)

    assert updated.roles == [INTERNAL_ROLE]


async def test_internal_role_cannot_be_assigned(rbac: RbacFixture) -> None:
    """V13: `internal_role_forbidden`, not a generic name-validation error."""
    user = rbac.repository.add_user(OPERATOR_EMAIL)

    with pytest.raises(InternalRoleError):
        await rbac.service.set_user_roles(user.id, [INTERNAL_ROLE], actor_email=ADMIN_EMAIL)


async def test_set_user_roles_rejects_unknown_roles(rbac: RbacFixture) -> None:
    """Refused, not silently skipped: `noa-old` no-opped and looked successful."""
    user = rbac.repository.add_user(OPERATOR_EMAIL)

    with pytest.raises(UnknownRoleError) as excinfo:
        await rbac.service.set_user_roles(user.id, [ROLE_NOC], actor_email=ADMIN_EMAIL)

    assert excinfo.value.unknown_roles == [ROLE_NOC]


async def test_set_user_roles_for_missing_user_raises_not_found(rbac: RbacFixture) -> None:
    with pytest.raises(UserNotFoundError):
        await rbac.service.set_user_roles(uuid4(), [], actor_email=ADMIN_EMAIL)


# --- V12 guards ---


async def test_cannot_disable_last_active_admin(rbac: RbacFixture) -> None:
    """V12, and the disabled admin alongside proves the count ignores inactive rows.

    `actor_user_id=None` on purpose: through the admin routes an *active* admin is always
    the actor, so the count includes them and only the self-guard can fire. This guard is
    the backstop for callers with no acting operator — a bootstrap script or a migration —
    which is precisely where an emptied admin set would go unnoticed.
    """
    admin = rbac.repository.add_user(ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))
    rbac.repository.add_user(OTHER_ADMIN_EMAIL, is_active=False, roles=(ADMIN_ROLE_NAME,))

    with pytest.raises(LastActiveAdminError):
        await rbac.service.set_user_active(admin.id, is_active=False, actor_email=None)


async def test_can_disable_an_admin_while_another_stays_active(rbac: RbacFixture) -> None:
    """The guard is about the last one, not about admins in general."""
    admin = rbac.repository.add_user(ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))
    other_admin = rbac.repository.add_user(OTHER_ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))
    rbac.repository.add_user("third-admin@example.com", roles=(ADMIN_ROLE_NAME,))

    updated = await rbac.service.set_user_active(
        admin.id, is_active=False, actor_email=OTHER_ADMIN_EMAIL, actor_user_id=other_admin.id
    )

    assert updated.is_active is False
    assert updated.tools == []


async def test_admin_cannot_self_deactivate(rbac: RbacFixture) -> None:
    """V12, and it fires even with other admins around: nobody can undo it for them."""
    admin = rbac.repository.add_user(ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))
    rbac.repository.add_user(OTHER_ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))

    with pytest.raises(SelfDeactivateAdminError):
        await rbac.service.set_user_active(
            admin.id, is_active=False, actor_email=ADMIN_EMAIL, actor_user_id=admin.id
        )


async def test_non_admin_can_be_disabled_by_themselves_guard_free(rbac: RbacFixture) -> None:
    """The self-deactivate guard is admin-specific; a non-admin has no such trap."""
    user = rbac.repository.add_user(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,))

    updated = await rbac.service.set_user_active(
        user.id, is_active=False, actor_email=OPERATOR_EMAIL, actor_user_id=user.id
    )

    assert updated.is_active is False


async def test_enabling_a_user_is_never_guarded(rbac: RbacFixture) -> None:
    """Guards protect the admin set from shrinking; enabling only grows it (V7)."""
    user = rbac.repository.add_user(OPERATOR_EMAIL, is_active=False, roles=(ADMIN_ROLE_NAME,))

    updated = await rbac.service.set_user_active(
        user.id, is_active=True, actor_email=ADMIN_EMAIL, actor_user_id=user.id
    )

    assert updated.is_active is True


# --- V4 cascade revoke on disable (T11) ---


async def test_disabling_a_user_revokes_their_mcp_tokens(rbac: RbacFixture) -> None:
    """V4: disable is the admin-side half of cascade revoke, alongside the LDAP path.

    Not what stops them calling tools — V1's per-request `is_active` re-check already
    does. This is what kills the credential, so a token in a LibreChat config cannot come
    back to life when the row is re-enabled later.
    """
    user = rbac.repository.add_user(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,), mcp_tokens=2)

    await rbac.service.set_user_active(user.id, is_active=False, actor_email=ADMIN_EMAIL)

    assert rbac.repository.mcp_tokens.get(user.id, 0) == 0


async def test_the_disable_audit_event_reports_how_many_tokens_went(
    rbac: RbacFixture,
) -> None:
    """V14: "disabled, held none" and "disabled, lost three" are different facts."""
    user = rbac.repository.add_user(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,), mcp_tokens=3)

    await rbac.service.set_user_active(user.id, is_active=False, actor_email=ADMIN_EMAIL)

    assert rbac.audit.event_types == [EVENT_USER_STATUS_UPDATED]
    assert rbac.audit.events[0].metadata["revoked_mcp_tokens"] == 3


async def test_disabling_a_user_with_no_tokens_reports_zero(rbac: RbacFixture) -> None:
    user = rbac.repository.add_user(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,))

    await rbac.service.set_user_active(user.id, is_active=False, actor_email=ADMIN_EMAIL)

    assert rbac.audit.events[0].metadata["revoked_mcp_tokens"] == 0


async def test_enabling_a_user_revokes_nothing(rbac: RbacFixture) -> None:
    """Only a True→False transition revokes. Enabling must not delete a fresh token."""
    user = rbac.repository.add_user(OPERATOR_EMAIL, is_active=False, mcp_tokens=1)

    await rbac.service.set_user_active(user.id, is_active=True, actor_email=ADMIN_EMAIL)

    assert rbac.repository.mcp_tokens[user.id] == 1
    assert rbac.audit.events[0].metadata["revoked_mcp_tokens"] == 0


async def test_redisabling_an_already_disabled_user_revokes_nothing(
    rbac: RbacFixture,
) -> None:
    """No transition, so nothing to revoke — and an audit line claiming otherwise
    would be false."""
    user = rbac.repository.add_user(OPERATOR_EMAIL, is_active=False, mcp_tokens=2)

    await rbac.service.set_user_active(user.id, is_active=False, actor_email=ADMIN_EMAIL)

    assert rbac.repository.mcp_tokens[user.id] == 2


async def test_a_refused_disable_revokes_nothing(rbac: RbacFixture) -> None:
    """The revoke runs after the write, so a guard that fires leaves the tokens alive."""
    admin = rbac.repository.add_user(ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,), mcp_tokens=2)

    with pytest.raises(LastActiveAdminError):
        await rbac.service.set_user_active(admin.id, is_active=False, actor_email=None)

    assert rbac.repository.mcp_tokens[admin.id] == 2


async def test_admin_self_delete_conflicts(rbac: RbacFixture) -> None:
    """V12: admin self-delete → 409, carried by `SelfDeleteAdminError`."""
    admin = rbac.repository.add_user(ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))
    rbac.repository.add_user(OTHER_ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))

    with pytest.raises(SelfDeleteAdminError):
        await rbac.service.delete_user(admin.id, actor_email=ADMIN_EMAIL, actor_user_id=admin.id)


async def test_self_delete_conflicts_for_non_admin(rbac: RbacFixture) -> None:
    """Also refused: it would leave a live cookie for a row that no longer exists (V6)."""
    user = rbac.repository.add_user(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,))

    with pytest.raises(SelfDeleteError):
        await rbac.service.delete_user(user.id, actor_email=OPERATOR_EMAIL, actor_user_id=user.id)


async def test_cannot_delete_last_active_admin(rbac: RbacFixture) -> None:
    """Same backstop as the disable path — see `test_cannot_disable_last_active_admin`."""
    admin = rbac.repository.add_user(ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))

    with pytest.raises(LastActiveAdminError):
        await rbac.service.delete_user(admin.id, actor_email=None)


async def test_delete_user_returns_a_pre_delete_snapshot(rbac: RbacFixture) -> None:
    rbac.repository.grant(ROLE_SUPPORT, TOOL_READ)
    user = rbac.repository.add_user(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,))
    admin = rbac.repository.add_user(ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))

    snapshot = await rbac.service.delete_user(
        user.id, actor_email=ADMIN_EMAIL, actor_user_id=admin.id
    )

    assert snapshot.email == OPERATOR_EMAIL
    assert snapshot.tools == [TOOL_READ]
    with pytest.raises(UserNotFoundError):
        await rbac.service.resolve_user(user.id)


async def test_delete_missing_user_raises_not_found(rbac: RbacFixture) -> None:
    with pytest.raises(UserNotFoundError):
        await rbac.service.delete_user(uuid4(), actor_email=ADMIN_EMAIL)


async def test_cannot_remove_own_admin_role(rbac: RbacFixture) -> None:
    """V12: the one demotion nobody else can undo for them."""
    admin = rbac.repository.add_user(ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))
    rbac.repository.add_user(OTHER_ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))

    with pytest.raises(SelfRemoveAdminRoleError):
        await rbac.service.set_user_roles(
            admin.id, [], actor_email=ADMIN_EMAIL, actor_user_id=admin.id
        )


async def test_cannot_remove_admin_from_last_active_admin(rbac: RbacFixture) -> None:
    """Third path to an empty admin set, same backstop (V12)."""
    admin = rbac.repository.add_user(ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))

    with pytest.raises(LastActiveAdminError):
        await rbac.service.set_user_roles(admin.id, [], actor_email=None)


async def test_removing_admin_from_a_disabled_admin_is_allowed(rbac: RbacFixture) -> None:
    """A disabled admin was never in the active-admin count, so removing it empties nothing."""
    disabled_admin = rbac.repository.add_user(
        ADMIN_EMAIL, is_active=False, roles=(ADMIN_ROLE_NAME,)
    )
    other_admin = rbac.repository.add_user(OTHER_ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))

    updated = await rbac.service.set_user_roles(
        disabled_admin.id, [], actor_email=OTHER_ADMIN_EMAIL, actor_user_id=other_admin.id
    )

    assert updated.roles == []


# --- V14: audit events ---


async def test_every_mutating_operation_records_an_audit_event(rbac: RbacFixture) -> None:
    """V14: admin changes produce audit events — one per change, none for reads.

    All six mutations in sequence, so a new operation added without a `_record` call shows
    up as a missing event rather than as nothing at all.
    """
    admin = rbac.repository.add_user(ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))
    rbac.repository.add_user(OTHER_ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))
    target = rbac.repository.add_user(OPERATOR_EMAIL)

    await rbac.service.create_role(ROLE_SUPPORT, actor_email=ADMIN_EMAIL)
    await rbac.service.set_role_tools(ROLE_SUPPORT, [TOOL_READ], actor_email=ADMIN_EMAIL)
    await rbac.service.set_user_roles(
        target.id, [ROLE_SUPPORT], actor_email=ADMIN_EMAIL, actor_user_id=admin.id
    )
    await rbac.service.set_user_active(
        target.id, is_active=False, actor_email=ADMIN_EMAIL, actor_user_id=admin.id
    )
    await rbac.service.delete_user(target.id, actor_email=ADMIN_EMAIL, actor_user_id=admin.id)
    await rbac.service.delete_role(ROLE_SUPPORT, actor_email=ADMIN_EMAIL)

    # Reads must not log: an audit trail of `tools/list` calls is V45's job, not V14's.
    await rbac.service.list_roles()
    await rbac.service.get_permitted_tools(admin.id)

    assert rbac.audit.event_types == [
        EVENT_ROLE_CREATED,
        EVENT_ROLE_TOOLS_UPDATED,
        EVENT_USER_ROLES_UPDATED,
        EVENT_USER_STATUS_UPDATED,
        EVENT_USER_DELETED,
        EVENT_ROLE_DELETED,
    ]


async def test_audit_event_names_the_actor_and_target(rbac: RbacFixture) -> None:
    admin = rbac.repository.add_user(ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))
    target = rbac.repository.add_user(OPERATOR_EMAIL)

    await rbac.service.set_user_active(
        target.id, is_active=True, actor_email=ADMIN_EMAIL, actor_user_id=admin.id
    )

    event = rbac.audit.events[-1]
    assert event.actor_email == ADMIN_EMAIL
    assert event.target == str(target.id)
    # `revoked_mcp_tokens` joined the payload with T11's cascade revoke (V4); an enable
    # never revokes, so it reports zero. Asserted as an exact dict on purpose — a field
    # appearing here without a decision is what this equality is for.
    assert event.metadata == {
        "target_user_id": str(target.id),
        "is_active": True,
        "revoked_mcp_tokens": 0,
    }


async def test_refused_mutations_record_no_audit_event(rbac: RbacFixture) -> None:
    """A refusal is not a change, so it must not appear in the trail (V14)."""
    admin = rbac.repository.add_user(ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))

    with pytest.raises(SelfDeactivateAdminError):
        await rbac.service.set_user_active(
            admin.id, is_active=False, actor_email=ADMIN_EMAIL, actor_user_id=admin.id
        )
    with pytest.raises(ReservedRoleError):
        await rbac.service.delete_role(ADMIN_ROLE_NAME, actor_email=ADMIN_EMAIL)

    assert rbac.audit.events == []


async def test_audit_event_carries_no_credentials(rbac: RbacFixture) -> None:
    """V8: an event payload holds identifiers, roles and tool names. Nothing else."""
    rbac.repository.grant(ROLE_SUPPORT)

    await rbac.service.set_role_tools(ROLE_SUPPORT, [TOOL_READ], actor_email=ADMIN_EMAIL)

    payload = repr(rbac.audit.events[-1])
    for forbidden in ("password", "secret", "token", "hash"):
        assert forbidden not in payload.lower()


# --- T51: the transaction boundary (V14's premise) ---


async def test_every_mutation_commits_exactly_once(rbac: RbacFixture) -> None:
    """T51: a write that never ends its transaction takes effect never (V14).

    The same six mutations the audit test walks, counted instead of listed: the repository
    flushes and nothing else commits on the request path, so a mutation added without a
    `commit()` would answer 200 over a rollback. One commit each, not one per statement —
    a disable, its token revoke and its event are one unit of work.
    """
    admin = rbac.repository.add_user(ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))
    rbac.repository.add_user(OTHER_ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))
    target = rbac.repository.add_user(OPERATOR_EMAIL, mcp_tokens=2)

    await rbac.service.create_role(ROLE_SUPPORT, actor_email=ADMIN_EMAIL)
    await rbac.service.set_role_tools(ROLE_SUPPORT, [TOOL_READ], actor_email=ADMIN_EMAIL)
    await rbac.service.set_user_roles(
        target.id, [ROLE_SUPPORT], actor_email=ADMIN_EMAIL, actor_user_id=admin.id
    )
    await rbac.service.set_user_active(
        target.id, is_active=False, actor_email=ADMIN_EMAIL, actor_user_id=admin.id
    )
    await rbac.service.delete_user(target.id, actor_email=ADMIN_EMAIL, actor_user_id=admin.id)
    await rbac.service.delete_role(ROLE_SUPPORT, actor_email=ADMIN_EMAIL)

    assert rbac.repository.commits == 6

    # Reads end no transaction, for the same reason they log no event.
    await rbac.service.list_roles()
    await rbac.service.get_permitted_tools(admin.id)

    assert rbac.repository.commits == 6


async def test_an_idempotent_create_role_commits_nothing(rbac: RbacFixture) -> None:
    """Re-creating an existing role changes nothing, so there is nothing to commit."""
    await rbac.service.create_role(ROLE_SUPPORT, actor_email=ADMIN_EMAIL)
    assert rbac.repository.commits == 1

    await rbac.service.create_role(ROLE_SUPPORT, actor_email=ADMIN_EMAIL)

    assert rbac.repository.commits == 1


async def test_a_refused_mutation_commits_nothing(rbac: RbacFixture) -> None:
    """Every guard raises before the commit, so a refusal leaves the row as it was.

    The committed snapshot is the assertion surface: the disable flips nothing here, but a
    future refusal ordered *after* a write would pass a "row unchanged" check on the mutable
    dict alone and fail this one (`support.rbac`'s snapshot, `FakeAuthRepository`'s trick).
    """
    admin = rbac.repository.add_user(ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,), mcp_tokens=3)

    with pytest.raises(SelfDeactivateAdminError):
        await rbac.service.set_user_active(
            admin.id, is_active=False, actor_email=ADMIN_EMAIL, actor_user_id=admin.id
        )

    assert rbac.repository.commits == 0
    assert rbac.repository.committed_users == {}
    assert rbac.repository.users[admin.id].is_active is True
    assert rbac.repository.mcp_tokens[admin.id] == 3
