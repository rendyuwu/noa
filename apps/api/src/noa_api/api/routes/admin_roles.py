"""Admin role management: list, create, delete, read and set tool grants.

**Six routes and no policy**, for the reason `admin_users.py` gives one file over: every rule
these handlers lean on lives in `core.auth.authorization_service` — the reserved `admin` role,
the role-name validator, the catalog check on a grant. They are invariants about the
data, so they must hold for a future CLI or a migration script too, not only for whoever calls
these paths. A handler here resolves the actor, calls one service method, and shapes the answer.

**`admin` is visible in the list and refused everywhere else.** It is a real `roles` row —
`AuthService._provision` writes it for a bootstrap admin — so filtering it out of `GET /roles`
would hide the one role an operator most wants to see who holds. `DELETE` and `PUT .../tools`
answer 403 `reserved_role`, and `GET /roles/admin/tools` answers the *whole catalog*
rather than its empty grant rows: `admin` gets every known tool by bypassing the table, so
`[]` would render a role that appears to permit nothing while permitting everything. Displayed
state equals enforced state, which is the property that makes the panel worth reading.

**`GET /admin/tools` sits here, not with the users routes.** It is the vocabulary of
`PUT /roles/{name}/tools` — the set an allowlist editor picks from — and it answers off the same
`AuthorizationService` that validates the write, so the offered names and the accepted names
cannot drift. `noa-old` kept it on its user router; nothing about it belongs there.

**Nothing here builds an `HTTPException`.** Refusals are `AuthorizationError` subclasses and
`noa_api.api.errors` owns status, body and `request_id`. `noa-old` spelled the same
five refusals out per handler, ~35 lines each, and had to keep four `error_code` strings in step
by hand; those strings now live on the error class.

**`{name}` is not a `UUID` path param**, unlike every route in `admin_users.py`. So the
validator runs on reads as well as writes: `GET /roles/..%2Fadmin/tools` is 400
`invalid_role_name`, not 404, which keeps the 400/404 split from telling a caller what the
validator accepts.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from noa_api.api.deps import AdminUserDep, AuthorizationServiceDep

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminRolesResponse(BaseModel):
    """`GET /admin/roles`. Assignable roles, internal `user:` roles excluded.

    Bare strings rather than objects: a role has no attribute the panel renders except its
    name, and its grants are a separate read (`/roles/{name}/tools`) because the list view
    shows a count, not the set.
    """

    roles: list[str]


class CreateRoleRequest(BaseModel):
    """`POST /admin/roles`. The name, validated by the service."""

    name: str


class AdminRoleResponse(BaseModel):
    """The role as stored — the *normalized* name, which is what a later path segment must be.

    `create_role` strips the input, so echoing the caller's string back would hand the panel a
    key its own next request would spell differently.
    """

    name: str


class DeleteRoleResponse(BaseModel):
    """`{ok: true}`. The role is gone, so there is no row to return."""

    ok: bool


class SetRoleToolsRequest(BaseModel):
    """`PUT /admin/roles/{name}/tools`. The full desired grant set, not a delta.

    Replacement rather than add/remove, matching the service: a delta would have to guess
    whether an absent name means "leave it" or "revoke it", and a permission the operator
    thinks they revoked is the expensive half of that guess.
    """

    tools: list[str]


class RoleToolsResponse(BaseModel):
    """A role's tool grants, as stored after the write — never as sent.

    The service normalizes (strip, de-duplicate, sort) and then re-reads, so the panel renders
    what a permission check would now resolve instead of its own echo of the request.
    """

    tools: list[str]


class AdminToolsResponse(BaseModel):
    """`GET /admin/tools`. Every tool name a grant may name."""

    tools: list[str]


@router.get("/roles", response_model=AdminRolesResponse)
async def list_roles(
    admin_user: AdminUserDep,
    authorization: AuthorizationServiceDep,
) -> AdminRolesResponse:
    """Assignable roles.

    Read fresh on every call with nothing cached behind it. Internal `user:` roles are
    excluded in the statement, not filtered here: they are NOA's own bookkeeping, the API
    refuses to assign them, and a list that offered one would ask the panel for something the
    write path rejects with 400.
    """
    return AdminRolesResponse(roles=await authorization.list_roles())


@router.post("/roles", response_model=AdminRoleResponse)
async def create_role(
    payload: CreateRoleRequest,
    admin_user: AdminUserDep,
    authorization: AuthorizationServiceDep,
) -> AdminRoleResponse:
    """Create a role.

    Idempotent, and deliberately so: re-creating an existing role changes nothing, records no
    audit event and commits nothing, so a double-submitted dialog is not an error an operator
    has to interpret. 200 with the name either way.

    A new role starts with zero grants. `PUT /roles/{name}/tools` is the second step, which is
    also why creation cannot hand out a permission by itself.

    Refusals: 400 `invalid_role_name` (blank, over 100 characters, or outside
    `[A-Za-z0-9_-]` — which is what rejects a `user:` prefix), 403 `reserved_role` for `admin`
    (it exists implicitly through the grant-table bypass, and a second definition of it would
    carry grants that mean nothing).
    """
    created = await authorization.create_role(payload.name, actor_email=admin_user.email)
    return AdminRoleResponse(name=created)


@router.delete("/roles/{name}", response_model=DeleteRoleResponse)
async def delete_role(
    name: str,
    admin_user: AdminUserDep,
    authorization: AuthorizationServiceDep,
) -> DeleteRoleResponse:
    """Delete a role.

    Its grants and its assignments go with it through `ON DELETE CASCADE`, so every operator
    who held it loses those tools on their next request — there is no orphaned grant row
    left to resolve.

    Refusals: 400 `invalid_role_name`, 403 `reserved_role`, 404 `admin_role_not_found`. The 404
    is why a second delete of the same role is not a silent success: the panel would otherwise
    show a stale row disappearing twice and neither disappearance would mean anything.
    """
    await authorization.delete_role(name, actor_email=admin_user.email)
    return DeleteRoleResponse(ok=True)


@router.get("/roles/{name}/tools", response_model=RoleToolsResponse)
async def get_role_tools(
    name: str,
    admin_user: AdminUserDep,
    authorization: AuthorizationServiceDep,
) -> RoleToolsResponse:
    """One role's tool grants.

    `admin` answers with the whole catalog rather than its (empty) grant rows — see the module
    docstring. Every other name answers with its stored grants, filtered to nothing: a grant
    written before a tool was renamed is still a row, and it is the *permission resolution*
    that drops it, so this read shows what is stored and `GET /admin/users` shows what it
    resolves to.

    Refusals: 400 `invalid_role_name`, 404 `admin_role_not_found`.
    """
    return RoleToolsResponse(tools=await authorization.get_role_tools(name))


@router.put("/roles/{name}/tools", response_model=RoleToolsResponse)
async def set_role_tools(
    name: str,
    payload: SetRoleToolsRequest,
    admin_user: AdminUserDep,
    authorization: AuthorizationServiceDep,
) -> RoleToolsResponse:
    """Replace one role's tool grants.

    This is the write "permission updates take effect immediately" is about: nothing
    behind it caches, so the next `tools/list` and the next execution-gate check both resolve
    from these rows. A client holding a stale catalog may still *show* a revoked tool;
    calling it 403s.

    Refusals: 400 `invalid_role_name`, 403 `reserved_role` (`admin` bypasses the grant table, so
    rows here would imply a limit NOA does not enforce), 404 `admin_role_not_found`, 400
    `unknown_tools` for any name outside the catalog. Unknown names are refused as a set, so an
    admin who mistyped one of twenty does not bisect the list by hand — though the error envelope
    keeps the offenders in `detail` and out of the body, so the panel sees the code and the operator
    re-checks their input.
    """
    stored = await authorization.set_role_tools(name, payload.tools, actor_email=admin_user.email)
    return RoleToolsResponse(tools=stored)


@router.get("/tools", response_model=AdminToolsResponse)
async def list_tools(
    admin_user: AdminUserDep,
    authorization: AuthorizationServiceDep,
) -> AdminToolsResponse:
    """Every tool name a grant may name.

    The vocabulary the allowlist editor offers, read off the same set that validates a write — see
    the module docstring. Not an existence oracle over anything hidden: the refusal-folding rule is
    about the MCP surface refusing all causes identically, while this is the admin surface, behind
    `require_admin`, and an admin is precisely who may know which tools exist. The never-implement
    names are absent from the catalog, so they are absent here.
    """
    return AdminToolsResponse(tools=await authorization.list_tools())
