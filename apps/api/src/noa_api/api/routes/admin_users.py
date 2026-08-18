"""Admin user management: list, enable/disable, delete, assign roles (T51, I.admin-api).

**Four routes and no policy.** Every guard these handlers rely on lives in
`core.auth.authorization_service` — the last-active-admin refusal, the self-deactivate and
self-delete refusals (V12), the reserved `admin` role and the internal-role rules (V13). That is
deliberate and it is T9's own rule: those are invariants about the data, so they must hold for a
future CLI or a migration script too, not only for whoever calls these paths. A handler here
resolves the actor, calls one service method, and shapes the answer.

**There is no `POST /users`.** A NOA operator is born at login: `AuthService._provision` writes
the row `is_active=False` on the first successful LDAP bind and an admin enables it from here
(V7, C4). That is where "create" belongs, because LDAP — not this API — is the source of truth
for who is employed, and a row minted here would be one NOA invented. `noa-old` shipped no create
route either, and the ported panel has no create control.

**`admin` is checked per handler, not once on the router.** `AdminUserDep` is a parameter on all
four, so the actor object the V12 guards need (`actor_email`, `actor_user_id`) and the gate that
authorises the call are the same read — there is no way to have one without the other. It also
inherits V6 through `require_session_user`: a disabled or demoted admin loses these routes on
their next request rather than at cookie expiry, which is what
`test_admin_user_routes.py::test_a_disabled_admin_loses_the_routes_on_the_next_request` asserts.

**Nothing here builds an `HTTPException`.** Refusals are `AuthorizationError` subclasses and
`noa_api.api.errors` owns status, body and `request_id` (V8, V73), so the same condition cannot
answer 400 on one route and 409 on another — which is exactly what `noa-old` did, in ~40 lines
per endpoint.

**`_to_user_response` is the only mapper**, and it drops two things on purpose:

- internal `user:` roles (V13, V75). The panel renders `roles` as the assignable set and PUTs
  that set back, so shipping an internal role would make the UI ask for something the API
  refuses with 400. They survive replacement server-side either way, in the repository's
  subquery.
- `direct_tools`. `noa-old`'s response carried it; V75 disables direct per-user grants (410,
  T65), so there is no field to fill and no key to send.

**The fifth route refuses rather than acts.** `PUT /admin/users/{user_id}/tools` answers 410
`direct_tool_grants_disabled` (V75, T65). It exists because `noa-old` shipped it and the ported
panel knew the address; it refuses because permissions flow role → user only and NOA's schema
holds no user-level grant table. See `noa_api.api.admin_errors` for why the error class is not
in the engine's taxonomy.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel

from core.auth.authorization_types import AuthorizedUser
from core.db.models import is_internal_role
from noa_api.api.admin_errors import DirectGrantsDisabledError
from noa_api.api.deps import AdminUserDep, AuthorizationServiceDep
from noa_api.api.serialization import iso_or_none

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminUserResponse(BaseModel):
    """One user as the admin panel reads them (T51 — V10, V11, V13).

    `tools` is the *effective* set the RBAC engine resolved for this read: every known tool for
    an admin (V10), empty for a disabled account whatever its roles say (V11). A snapshot for a
    body, never a permission cache — the execution gate re-resolves per call (V1, V74).
    """

    id: str
    email: str
    display_name: str | None
    is_active: bool
    created_at: str | None
    last_login_at: str | None
    roles: list[str]
    tools: list[str]


class AdminUsersResponse(BaseModel):
    """`GET /admin/users`. Ordered by email in the statement, so the list is stable."""

    users: list[AdminUserResponse]


class UpdateUserRequest(BaseModel):
    """`PATCH /admin/users/{id}`. One field, and it is the only one an admin may flip.

    Not a general-purpose patch: `email`, `display_name` and `ldap_dn` are mirrored from the
    directory on every login (C4), so editing them here would write a value the next bind
    overwrites — a control that appears to work and does not.
    """

    is_active: bool


class SetUserRolesRequest(BaseModel):
    """`PUT /admin/users/{id}/roles`. The full desired set, not a delta.

    Replacement rather than add/remove, matching the service: a delta would have to guess
    whether an absent name means "leave it" or "revoke it". Internal `user:` roles are refused
    (400 `internal_role_forbidden`) and preserved (V13, V75) — the caller neither sends them nor
    can clear them.
    """

    roles: list[str]


class UpdateUserResponse(BaseModel):
    """The user as they now are. The panel threads this back into its list rather than
    optimistically guessing what the write did."""

    user: AdminUserResponse


class DeleteUserResponse(BaseModel):
    """`{ok: true}`. The deleted user is gone, so there is no row to return."""

    ok: bool


def _to_user_response(user: AuthorizedUser) -> AdminUserResponse:
    """Shape one `AuthorizedUser` for the wire. See the module docstring for the two omissions.

    `iso_or_none` keeps `null` as `null` rather than collapsing it to a string, which the panel
    depends on: `last_login_at: null` is where its "Pending" status comes from
    (`apps/admin-web/src/lib/admin/users/user-status.ts`).
    """
    return AdminUserResponse(
        id=str(user.user_id),
        email=user.email,
        display_name=user.display_name,
        is_active=user.is_active,
        created_at=iso_or_none(user.created_at),
        last_login_at=iso_or_none(user.last_login_at),
        roles=[role for role in user.roles if not is_internal_role(role)],
        tools=user.tools,
    )


@router.get("/users", response_model=AdminUsersResponse)
async def list_users(
    admin_user: AdminUserDep,
    authorization: AuthorizationServiceDep,
) -> AdminUsersResponse:
    """Every user with their roles and effective tools (T51 — V10, V11, V13).

    Read fresh on every call, with no cache anywhere behind it (V14): what this shows is what the
    execution gate would decide right now, which is the property that makes the panel's display
    of a permission worth trusting.
    """
    users = await authorization.list_users()
    return AdminUsersResponse(users=[_to_user_response(user) for user in users])


@router.patch("/users/{user_id}", response_model=UpdateUserResponse)
async def update_user_active(
    user_id: UUID,
    payload: UpdateUserRequest,
    admin_user: AdminUserDep,
    authorization: AuthorizationServiceDep,
) -> UpdateUserResponse:
    """Enable or disable one user (T51 — V7, V11, V12, V4).

    Disabling is the operation V6 leans on: there is no session revocation, so `is_active=False`
    takes effect through the per-request row re-read. It also deletes every `mcp_tokens` row the
    operator holds (V4) — inside the service, so no route can forget it, and only on a genuine
    True→False transition.

    Refusals: 404 `admin_user_not_found`, 409 `last_active_admin` (V12), 409
    `self_deactivate_admin` when an admin aims at their own account (V12) — a refusal nobody
    else could undo for them.

    `actor_user_id` is passed so that second guard is reachable at all: without it the service
    cannot tell "an admin disabled someone" from "an admin disabled themselves".
    """
    user = await authorization.set_user_active(
        user_id,
        is_active=payload.is_active,
        actor_email=admin_user.email,
        actor_user_id=admin_user.user_id,
    )
    return UpdateUserResponse(user=_to_user_response(user))


@router.delete("/users/{user_id}", response_model=DeleteUserResponse)
async def delete_user(
    user_id: UUID,
    admin_user: AdminUserDep,
    authorization: AuthorizationServiceDep,
) -> DeleteUserResponse:
    """Delete one user (T51 — V12).

    Role assignments and MCP tokens go with the row through `ON DELETE CASCADE`; the session
    cookie does not, which is why a deleted operator's next request is `session_invalid` rather
    than a working session for a row that no longer exists (V6).

    Refusals: 404, 409 `last_active_admin`, 409 `self_delete` / `self_delete_admin` — V12 names
    the admin case explicitly and self-delete is refused for non-admins too.
    """
    await authorization.delete_user(
        user_id,
        actor_email=admin_user.email,
        actor_user_id=admin_user.user_id,
    )
    return DeleteUserResponse(ok=True)


@router.put("/users/{user_id}/roles", response_model=UpdateUserResponse)
async def set_user_roles(
    user_id: UUID,
    payload: SetUserRolesRequest,
    admin_user: AdminUserDep,
    authorization: AuthorizationServiceDep,
) -> UpdateUserResponse:
    """Replace one user's assignable roles (T51 — V13, V14, V75).

    The response carries the effective tools the new roles resolve to, read after the write, so
    the panel renders the server's answer instead of its own guess about what a role grants.

    Refusals: 404 `admin_user_not_found`, 400 `internal_role_forbidden`, 400 `invalid_role_name`,
    400 `unknown_roles` (a missing role is refused, never silently skipped), 409
    `self_remove_admin_role`, 409 `last_active_admin`. Malformed before well-formed-and-refused,
    which is the service's own ordering: the caller learns about their mistake before the
    deployment's.
    """
    user = await authorization.set_user_roles(
        user_id,
        payload.roles,
        actor_email=admin_user.email,
        actor_user_id=admin_user.user_id,
    )
    return UpdateUserResponse(user=_to_user_response(user))


@router.put("/users/{user_id}/tools", status_code=status.HTTP_410_GONE)
async def set_user_tools(
    user_id: UUID,
    admin_user: AdminUserDep,
) -> None:
    """Direct per-user tool grants are gone: 410 `direct_tool_grants_disabled` (T65 — V75).

    **No request body, deliberately.** A handler that declared `SetUserToolsRequest` — as
    `noa-old`'s did, only to discard it — would let FastAPI validate before the refusal, so a
    caller who sent `{"tools": 3}` would get a 422 saying their *field* was wrong about a route
    that will never accept any field. The refusal is about the route, and it has to be
    unconditional to read that way.

    **No `AuthorizationServiceDep` either.** Nothing is looked up: `user_id` is not checked for
    existence, so an unknown id answers 410 rather than 404. That is not laziness about the
    404 — it is the same rule V27 spells out for a different table. A status that varied with
    whether the row existed would make this route an existence oracle for `users`, and it would
    make a caller believe the grant might have worked for a *real* user.

    `AdminUserDep` stays, so `require_admin` runs first and a non-admin gets 403 (V13). Ordering
    matters in one direction only: the 410 is public knowledge, but the routes around it are
    admin-only, and a surface that answered 410 to anyone would say which paths exist here.

    `status_code=410` is declared on the decorator as well as raised, so the OpenAPI schema the
    panel reads names it. The body is the shared envelope (V8, V73) — the raise is what produces
    it, and the annotated `None` return is unreachable.
    """
    raise DirectGrantsDisabledError(
        f"direct tool grants refused for `{user_id}` (V75: permissions flow role → user)"
    )


__all__ = [
    "AdminUserResponse",
    "AdminUsersResponse",
    "DeleteUserResponse",
    "SetUserRolesRequest",
    "UpdateUserRequest",
    "UpdateUserResponse",
    "delete_user",
    "list_users",
    "router",
    "set_user_roles",
    "set_user_tools",
    "update_user_active",
]
