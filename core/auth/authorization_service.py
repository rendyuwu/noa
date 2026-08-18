"""The RBAC engine (T9, V6, V10, V11, V12, V13, V14).

Ported from `noa-old` branch `MCP` (`core/auth/authorization_service.py`, C13). One class
answers two different kinds of question, and keeping them together is deliberate — the
write side must not be able to produce a state the read side interprets differently:

- **Read** — `get_permitted_tools(user_id)` and `authorize_tool(user_id, tool)`. Called on
  every MCP request: `tools/list` filters by the first, the execution gate re-checks with
  the second (V1). Both start from a fresh `users` row.
- **Write** — role CRUD, grant replacement, role assignment, enable/disable, delete.
  Called by the admin routes (T51-T53). Every mutation records an audit event (V14).

Three rules the implementation is shaped around:

1. **No cache, anywhere.** V14 says permission updates take effect immediately and V6 says
   the session JWT cannot be revoked before `exp`. Together they mean the row is the only
   trustworthy source: memoizing an operator's tool set — even for one request — would let
   a revoked grant or a disabled account keep working. `AuthorizedUser.tools` is a snapshot
   for a response body, never an input to a later check.
2. **Admin bypass is bounded by the catalog** (V10). `admin` skips the grant table, not the
   "is this a real tool?" question. An unregistered name is refused for everyone, which is
   what stops a prompt-injected tool name from resolving because an admin happened to ask.
3. **Guards live here, not in the routes** (V12, V13). The last-active-admin check, the
   self-deactivate refusal, the reserved `admin` role and the internal-role rules are
   invariants about the data, so they hold for any caller — a future CLI or migration
   script included. `noa-old` had them here too, but re-derived each HTTP status in every
   route; here the error class carries it (V73).
4. **Every mutation commits, and the commit is the last thing it does** (T51). V14 says a
   permission update takes effect immediately, which is only true of a write that ended its
   transaction: the repository flushes and `noa_api.api.deps.get_db_session` never commits,
   so a service that left the boundary to its caller would have five routes answering 200
   over a rollback. Here, as in `AuthService.authenticate` and
   `ActionDecisionService.approve`, the service owns it — and because every guard raises
   *before* the commit, a refused change persists nothing. The audit event is recorded first,
   so the change and the record of it land in the same transaction (once a `SQLAdminAuditSink`
   exists — the structlog sink is outside it by nature).

Departures from `noa-old`, each with a test:

- **Direct per-user grants are gone.** `migrate_legacy_direct_grants` and the
  `user:<uuid>` grant reads went with them (V75/T65 makes direct grants a 410). The
  internal-role *preservation* rule stays, because internal roles are still NOA's
  bookkeeping.
- **Missing rows raise instead of returning `None`.** `noa-old` returned `None` and each
  route turned it into a 404, which is how one route ends up answering 200 with an empty
  body. `UserNotFoundError` / `RoleNotFoundError` carry the status once.
- **An idempotent `create_role` records no audit event.** Re-creating an existing role
  changes nothing, and an audit trail that logs non-changes is one nobody reads.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final
from uuid import UUID

from core.audit.admin_events import (
    EVENT_ROLE_CREATED,
    EVENT_ROLE_DELETED,
    EVENT_ROLE_TOOLS_UPDATED,
    EVENT_USER_DELETED,
    EVENT_USER_ROLES_UPDATED,
    EVENT_USER_STATUS_UPDATED,
    AdminAuditEvent,
    AdminAuditSink,
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
from core.auth.authorization_types import (
    AuthorizationRepository,
    AuthorizationUserRecord,
    AuthorizedUser,
)
from core.auth.tool_catalog import TOOL_CATALOG
from core.db.models import ADMIN_ROLE_NAME, INTERNAL_ROLE_PREFIX, is_internal_role

# `roles.name` is `String(100)` (T4). Validated here so an over-long name is a 400 rather
# than a database error surfacing as a 500.
MAX_ROLE_NAME_LENGTH: Final = 100

# Deliberately narrow: role names appear in URLs (`/admin/roles/{name}/tools`) and in
# `LIKE` patterns for the internal-role check, so `%`, `/` and `:` stay out.
ROLE_NAME_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]+")

DETAIL_ROLE_NAME_BLANK = "role name is blank after stripping"
DETAIL_ROLE_NAME_TOO_LONG = f"role name exceeds {MAX_ROLE_NAME_LENGTH} characters"
DETAIL_ROLE_NAME_CHARS = "role name has characters outside [A-Za-z0-9_-]"


class AuthorizationService:
    """Permission resolution and role administration."""

    def __init__(
        self,
        *,
        repository: AuthorizationRepository,
        audit_sink: AdminAuditSink,
        known_tools: Iterable[str] = TOOL_CATALOG,
    ) -> None:
        self._repository = repository
        self._audit = audit_sink
        # Snapshotted at construction, which is safe because the catalog is static for the
        # process (see `core.auth.tool_catalog`). When T13 makes it registry-derived, this
        # becomes a call instead — the service is constructed per request either way.
        self._known_tools = frozenset(known_tools)

    # --- Read path (V1, V6, V10, V11) ---

    async def get_permitted_tools(self, user_id: UUID) -> set[str]:
        """Tools `user_id` may call, resolved from the database on every call.

        The three branches are V10 and V11 in order:

        - `is_active=False` → empty, whatever roles say (V11). Checked first, so a disabled
          admin gets nothing rather than everything.
        - holds `admin` → every known tool, grant table skipped (V10).
        - otherwise → their roles' grants, filtered to the catalog. The filter matters:
          `role_tool_permissions.tool_name` is a plain string (T4), so a grant written
          before a tool was renamed must resolve to "no permission", not to a dangling
          name the dispatcher might still accept.

        Raises `UserNotFoundError` when the row is gone. Not an empty set: a deleted
        operator is not an operator with no permissions, and the caller owes them a 401
        (T12) rather than an empty tool list that reads like a misconfigured role.
        """
        user = await self._require_user(user_id)
        roles = await self._repository.get_role_names(user_id)
        return await self._effective_tools(is_active=user.is_active, roles=roles)

    async def authorize_tool(self, user_id: UUID, tool_name: str) -> bool:
        """Whether `user_id` may call `tool_name` right now (V1).

        Re-resolved rather than read off an earlier `tools/list`: V74 accepts that a client
        may hold a stale catalog and leans on exactly this check as the backstop, so a
        revoked tool still 403s at execution even while it is displayed.
        """
        if tool_name not in self._known_tools:
            # Cheapest branch first, and it is also the V10 half that applies to admins:
            # an unregistered name is refused before any row is read.
            return False

        return tool_name in await self.get_permitted_tools(user_id)

    async def resolve_user(self, user_id: UUID) -> AuthorizedUser:
        """One user with their roles and effective tools, read fresh (V6)."""
        user = await self._require_user(user_id)
        return await self._to_authorized_user(user)

    async def list_users(self) -> list[AuthorizedUser]:
        """Every user with roles and effective tools, for the admin list (T51)."""
        users = await self._repository.list_users()
        return [await self._to_authorized_user(user) for user in users]

    # --- Roles (V13) ---

    async def list_roles(self) -> list[str]:
        """Assignable roles. Internal `user:` roles are excluded (V13, V75).

        `admin` is included: it is a real `roles` row (`AuthService._provision` writes it for
        a bootstrap admin) and hiding it would leave the panel unable to show who holds the
        one role it cannot grant tools to. Editing and deleting it are refused instead (V13).
        """
        return await self._repository.list_assignable_role_names()

    async def list_tools(self) -> list[str]:
        """Every tool name a grant may name (V10, T52).

        The vocabulary of `set_role_tools`, read off the same `_known_tools` that validates a
        write — so what the panel offers and what the service accepts cannot drift apart. A
        route asking `core.auth.tool_catalog` directly would be a second answer to the
        question this service already owns (V66), and would miss a construction-time
        `known_tools` override.

        A read: no event, no commit.
        """
        return sorted(self._known_tools)

    async def create_role(self, name: str, *, actor_email: str | None = None) -> str:
        """Create a role. Idempotent, and records an event only when it created one.

        `admin` is refused (V13): it already exists implicitly through the bypass, and
        letting an admin create a second definition of it invites grants that do nothing.
        """
        role_name = self._validate_role_name(name)
        self._reject_reserved_role(role_name)

        if await self._repository.role_exists(role_name):
            # No write, so no event and no commit: re-creating an existing role changes
            # nothing, and a commit here would be a transaction boundary around a read.
            return role_name

        created = await self._repository.ensure_role(role_name)
        await self._record(EVENT_ROLE_CREATED, actor_email, created, {"role": created})
        await self._repository.commit()
        return created

    async def delete_role(self, name: str, *, actor_email: str | None = None) -> None:
        """Delete a role and, by cascade, its grants and assignments.

        Refuses `admin` (V13). `RoleNotFoundError` for anything else that is absent, so a
        second delete of the same role is a 404 rather than a silent success — the admin
        panel would otherwise show a stale row disappearing twice.
        """
        role_name = self._validate_role_name(name)
        self._reject_reserved_role(role_name)

        if not await self._repository.delete_role(role_name):
            raise RoleNotFoundError(f"role `{role_name}` does not exist")

        await self._record(EVENT_ROLE_DELETED, actor_email, role_name, {"role": role_name})
        await self._repository.commit()

    async def get_role_tools(self, name: str) -> list[str]:
        """Tool grants held by a role.

        `admin` answers with the whole catalog rather than its (empty) grant rows. V10
        gives it every known tool by bypassing the table, so returning `[]` would render an
        admin role that appears to permit nothing while permitting everything. Displayed
        state and enforced state stay equal.
        """
        role_name = self._validate_role_name(name)
        if role_name == ADMIN_ROLE_NAME:
            return sorted(self._known_tools)

        await self._require_role(role_name)
        return await self._repository.get_role_tool_names_for_role(role_name)

    async def set_role_tools(
        self, name: str, tool_names: Iterable[str], *, actor_email: str | None = None
    ) -> list[str]:
        """Replace a role's grants with exactly `tool_names` (V14 — effective at once).

        Refuses `admin` (V13) and any name outside the catalog (V10). Unknown names are
        rejected as a set, not one at a time, so an admin fixing a typo in a list of twenty
        sees all the bad ones at once.
        """
        role_name = self._validate_role_name(name)
        self._reject_reserved_role(role_name)
        await self._require_role(role_name)

        normalized = sorted({tool.strip() for tool in tool_names if tool.strip()})
        unknown = [tool for tool in normalized if tool not in self._known_tools]
        if unknown:
            raise UnknownToolError(unknown)

        await self._repository.replace_role_tool_permissions(role_name, normalized)
        stored = await self._repository.get_role_tool_names_for_role(role_name)
        await self._record(
            EVENT_ROLE_TOOLS_UPDATED, actor_email, role_name, {"role": role_name, "tools": stored}
        )
        await self._repository.commit()
        return stored

    # --- User administration (V12, V13) ---

    async def set_user_roles(
        self,
        user_id: UUID,
        role_names: Iterable[str],
        *,
        actor_email: str | None = None,
        actor_user_id: UUID | None = None,
    ) -> AuthorizedUser:
        """Replace the user's assignable roles, preserving internal ones (V13, V75).

        Guard order is chosen so the caller learns about their own mistake before the
        deployment's: an internal role or an unknown name is a malformed request (400),
        while removing the last admin is a well-formed request refused (409).
        """
        user = await self._require_user(user_id)
        normalized = self._normalize_assignable_roles(role_names)

        existing = await self._repository.list_existing_role_names(normalized)
        missing = sorted(set(normalized) - set(existing))
        if missing:
            raise UnknownRoleError(missing)

        current_roles = await self._repository.get_role_names(user_id)
        if ADMIN_ROLE_NAME in current_roles and ADMIN_ROLE_NAME not in normalized:
            await self._guard_admin_removal(user, actor_user_id=actor_user_id)

        await self._repository.replace_user_assignable_roles(user_id, normalized)

        authorized = await self._to_authorized_user(user)
        await self._record(
            EVENT_USER_ROLES_UPDATED,
            actor_email,
            str(user_id),
            {"target_user_id": str(user_id), "roles": authorized.roles},
        )
        await self._repository.commit()
        return authorized

    async def set_user_active(
        self,
        user_id: UUID,
        *,
        is_active: bool,
        actor_email: str | None = None,
        actor_user_id: UUID | None = None,
    ) -> AuthorizedUser:
        """Enable or disable a user (V7, V11, V12), cascade-revoking their tokens (V4).

        Disabling is the operation V6 leans on: there is no session revocation, so
        `is_active=False` takes effect through the per-request row re-read in
        `AuthService.resolve_session_user` and through `get_permitted_tools` here. That is
        also why both guards below are refusals rather than warnings — an admin who
        disables themselves cannot undo it from inside the app.

        Disabling also deletes every `mcp_tokens` row the operator holds (V4, T11). Note
        what that is and is not: it is not what stops them calling tools — V1's per-request
        `is_active` re-check already does, and it does so without waiting for anything to
        propagate. It is what makes the credential itself dead, so a token sitting in a
        LibreChat config cannot start working again the day the row is re-enabled for some
        unrelated reason. The cost is real and one-way: re-enabling an operator means
        minting a fresh token and updating their `customUserVars`, because a deleted row
        cannot be un-deleted (V2 — revocation *is* the absence).

        Runs here rather than in a route so no caller can forget it, and only on a genuine
        True→False transition: re-disabling an already-disabled account revokes nothing,
        because there is nothing left to revoke and an audit line claiming otherwise would
        be false.
        """
        user = await self._require_user(user_id)
        roles = await self._repository.get_role_names(user_id)
        is_disabling = user.is_active and not is_active

        if is_disabling and ADMIN_ROLE_NAME in roles:
            if actor_user_id is not None and actor_user_id == user_id:
                raise SelfDeactivateAdminError("actor is the target admin account")
            if await self._repository.count_active_admin_users() <= 1:
                raise LastActiveAdminError("target holds the only active admin role")

        updated = await self._repository.update_user_active(user_id, is_active=is_active)
        if updated is None:
            # Row vanished between the read above and the write: another admin deleted it.
            raise UserNotFoundError(f"user `{user_id}` disappeared mid-request")

        # After the write, so a refused disable revokes nothing. Same transaction as the
        # write and the audit event, so the three cannot disagree.
        revoked_tokens = (
            await self._repository.delete_mcp_tokens_for_user(user_id) if is_disabling else 0
        )

        authorized = await self._to_authorized_user(updated)
        await self._record(
            EVENT_USER_STATUS_UPDATED,
            actor_email,
            str(user_id),
            {
                "target_user_id": str(user_id),
                "is_active": is_active,
                # Recorded even when zero: "disabled, held no tokens" and "disabled, lost
                # four" are different facts for whoever reads the trail later (V14).
                "revoked_mcp_tokens": revoked_tokens,
            },
        )
        # The status flip, the token revoke and the event are one transaction, ended here
        # (T51). Both guards above raise before it, so a refused disable revokes nothing and
        # persists nothing.
        await self._repository.commit()
        return authorized

    async def delete_user(
        self,
        user_id: UUID,
        *,
        actor_email: str | None = None,
        actor_user_id: UUID | None = None,
    ) -> AuthorizedUser:
        """Delete a user; return what they were (V12).

        The snapshot is taken before the delete because the response has to name whom it
        removed, and after the row is gone there is nothing to read. Self-delete is refused
        for everyone, admin or not: it would leave the caller holding a valid session cookie
        for a row that no longer exists (V6).
        """
        user = await self._require_user(user_id)
        roles = await self._repository.get_role_names(user_id)
        snapshot = await self._to_authorized_user(user, roles=roles)
        is_admin_user = ADMIN_ROLE_NAME in roles

        if actor_user_id is not None and actor_user_id == user_id:
            if is_admin_user:
                raise SelfDeleteAdminError("actor is the target admin account")
            raise SelfDeleteError("actor is the target account")

        if (
            user.is_active
            and is_admin_user
            and await self._repository.count_active_admin_users() <= 1
        ):
            raise LastActiveAdminError("target holds the only active admin role")

        if not await self._repository.delete_user(user_id):
            raise UserNotFoundError(f"user `{user_id}` disappeared mid-request")

        await self._record(
            EVENT_USER_DELETED,
            actor_email,
            str(user_id),
            {"target_user_id": str(user_id), "email": snapshot.email},
        )
        await self._repository.commit()
        return snapshot

    # --- Internals ---

    @staticmethod
    def _validate_role_name(role_name: str) -> str:
        """Normalize and validate a role name, on reads as well as writes.

        Validated on reads too so `get_role_tools("user:%")` is a 400 about the name rather
        than a 404 about existence — otherwise the two statuses together describe what the
        validator accepts.
        """
        normalized = role_name.strip()
        if not normalized:
            raise InvalidRoleNameError(DETAIL_ROLE_NAME_BLANK)
        if len(normalized) > MAX_ROLE_NAME_LENGTH:
            raise InvalidRoleNameError(DETAIL_ROLE_NAME_TOO_LONG)
        if not ROLE_NAME_PATTERN.fullmatch(normalized):
            # Also rejects the `user:` prefix, since `:` is outside the pattern. Callers
            # that want the clearer `internal_role_forbidden` code check the prefix first.
            raise InvalidRoleNameError(DETAIL_ROLE_NAME_CHARS)
        return normalized

    @staticmethod
    def _reject_reserved_role(role_name: str) -> None:
        """`admin` is built in: ⊥ edit its tools, ⊥ delete it (V13)."""
        if role_name == ADMIN_ROLE_NAME:
            raise ReservedRoleError(f"role `{ADMIN_ROLE_NAME}` is reserved")

    def _normalize_assignable_roles(self, role_names: Iterable[str]) -> list[str]:
        """Validate a role-assignment list, refusing internal roles by name (V13)."""
        normalized: list[str] = []
        for role_name in role_names:
            raw = role_name.strip()
            if is_internal_role(raw):
                raise InternalRoleError(f"`{INTERNAL_ROLE_PREFIX}` roles are assigned by NOA only")
            normalized.append(self._validate_role_name(raw))
        return sorted(dict.fromkeys(normalized))

    async def _require_user(self, user_id: UUID) -> AuthorizationUserRecord:
        user = await self._repository.get_user_by_id(user_id)
        if user is None:
            raise UserNotFoundError(f"no `users` row for `{user_id}`")
        return user

    async def _guard_admin_removal(
        self, user: AuthorizationUserRecord, *, actor_user_id: UUID | None
    ) -> None:
        """Refuse an `admin` removal that nobody could undo (V12).

        Two cases, and the last-admin one only applies to an *active* target: stripping
        `admin` from an already-disabled account cannot empty the active-admin set, because
        `count_active_admin_users` never counted them.
        """
        if actor_user_id is not None and actor_user_id == user.id:
            raise SelfRemoveAdminRoleError("actor is removing their own admin role")
        if user.is_active and await self._repository.count_active_admin_users() <= 1:
            raise LastActiveAdminError("target holds the only active admin role")

    async def _require_role(self, role_name: str) -> None:
        if not await self._repository.role_exists(role_name):
            raise RoleNotFoundError(f"role `{role_name}` does not exist")

    async def _effective_tools(self, *, is_active: bool, roles: list[str]) -> set[str]:
        """The V10/V11 resolution, in one place so reads cannot disagree."""
        if not is_active:
            return set()
        if ADMIN_ROLE_NAME in roles:
            return set(self._known_tools)

        granted = await self._repository.get_role_tool_names(roles)
        return {tool for tool in granted if tool in self._known_tools}

    async def _to_authorized_user(
        self, user: AuthorizationUserRecord, *, roles: list[str] | None = None
    ) -> AuthorizedUser:
        """Build the response shape, resolving effective tools from the same read."""
        resolved_roles = (
            roles if roles is not None else await self._repository.get_role_names(user.id)
        )
        tools = await self._effective_tools(is_active=user.is_active, roles=resolved_roles)
        return AuthorizedUser(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            is_active=user.is_active,
            roles=resolved_roles,
            tools=sorted(tools),
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )

    async def _record(
        self,
        event_type: str,
        actor_email: str | None,
        target: str | None,
        metadata: dict[str, object],
    ) -> None:
        """Record one admin audit event (V14)."""
        await self._audit.record(
            AdminAuditEvent(
                event_type=event_type,
                actor_email=actor_email,
                target=target,
                metadata=metadata,
            )
        )


__all__ = [
    "MAX_ROLE_NAME_LENGTH",
    "ROLE_NAME_PATTERN",
    "AuthorizationService",
]
