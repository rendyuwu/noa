"""Authorization error taxonomy.

Ported from `noa-old` branch `MCP` (`core/auth/authorization_errors.py` — port, never
import) with two changes, both so every error carries `request_id` in body and header:

1. Every class derives from `core.errors.NoaError`, so it carries `error_code` and an
   operator-facing `message`. `noa-old` raised bare `Exception` subclasses and let each
   route translate them, which is how the same condition ends up as 400 in one handler
   and 409 in another. Here `noa_api.api.errors` maps class → status, once.
2. The `error_code` strings are lifted verbatim from `noa-old`'s
   `api/error_codes.py` — `last_active_admin`, `self_delete_admin`, `unknown_tools`, and
   the rest. The ported admin panel branches on these strings, so inventing new
   spellings would break a client that already exists.

`DirectGrantsDisabledError` is deliberately NOT here: the 410 on direct grants owns
that status, and the engine has no direct-grant path to refuse.

Statuses the handler assigns, and the reasoning:

- 403 `admin_access_required` — authenticated, not an admin. Re-authenticating
  changes nothing, so 401 would loop a login redirect.
- 403 `reserved_role` — the `admin` role exists and the caller may not edit or delete it
  (admin endpoints refuse a non-admin). Not 404: pretending it is absent would be a
  lie the UI then renders.
- 404 — the user or role does not exist.
- 400 — the request itself is malformed: bad role name, unknown tool, unknown role, or an
  internal role the API may not assign.
- 409 — the request is well-formed and refused because it would break an invariant: the
  last active admin, or an admin acting on their own account.
"""

from __future__ import annotations

from core.errors import NoaError


class AuthorizationError(NoaError):
    """Base for every RBAC refusal.

    Separate from `AuthError`: those mean "we do not know who you are", these mean "we
    know, and no". The handler's fallback for an unclassified `AuthError` is 503, which
    would be wrong for every class below — so a test asserts each of these is mapped
    explicitly instead of inheriting that fallback.
    """

    error_code: str = "authorization_failed"
    message: str = "That action is not allowed."
    # Bare `AuthorizationError` is still a refusal, so 403 rather than the 503 fallback. A
    # test asserts every subclass is mapped above, so reaching this line means a new class
    # arrived without a decision.
    status_code = 403


class AdminAccessRequiredError(AuthorizationError):
    """Caller is authenticated but holds no `admin` role.

    Message names the remedy — ask an admin — because the operator cannot fix this
    themselves and a bare "forbidden" sends them retrying.
    """

    error_code: str = "admin_access_required"
    message: str = "This area is for NOA administrators. Ask an admin if you need access."
    status_code = 403


class UserNotFoundError(AuthorizationError):
    """No `users` row for the given id."""

    error_code: str = "admin_user_not_found"
    message: str = "That user no longer exists."
    status_code = 404


class RoleNotFoundError(AuthorizationError):
    """No `roles` row with the given name."""

    error_code: str = "admin_role_not_found"
    message: str = "That role no longer exists."
    status_code = 404


class InvalidRoleNameError(AuthorizationError):
    """Role name is blank, too long, or carries characters the API refuses.

    Validated on *read* paths too, not only writes: `get_role_tools("../admin")` should
    be rejected as a malformed name rather than answered with "no such role", so a
    caller cannot use the 404/400 split to probe what the validator allows.
    """

    error_code: str = "invalid_role_name"
    message: str = (
        "Role names may use letters, numbers, hyphens and underscores only, up to 100 characters."
    )
    status_code = 400


class ReservedRoleError(AuthorizationError):
    """The `admin` role is reserved: never edit its tools, never delete it.

    `admin` holds every known tool by bypassing the grant table entirely, so its
    `role_tool_permissions` rows would be decoration that implies a limit NOA does not
    enforce. Refusing the edit keeps the displayed state and the enforced state equal.
    """

    error_code: str = "reserved_role"
    message: str = "The admin role is built in and cannot be edited or deleted."
    status_code = 403


class InternalRoleError(AuthorizationError):
    """Caller tried to assign a `user:`-prefixed role through the API.

    Internal roles are NOA's own bookkeeping. They are preserved across role replacement
    and never appear in the assignable list.
    """

    error_code: str = "internal_role_forbidden"
    message: str = "Internal roles are managed by NOA and cannot be assigned."
    status_code = 400


class UnknownToolError(AuthorizationError):
    """Grant requested for a tool that is not in the catalog.

    Carries `unknown_tools` so the response can name them — an admin who mistyped one
    name in a list of twenty otherwise has to bisect it by hand.
    """

    error_code: str = "unknown_tools"
    message: str = "One or more of those tools does not exist."
    status_code = 400

    def __init__(self, unknown_tools: list[str], detail: str | None = None) -> None:
        self.unknown_tools = sorted({name.strip() for name in unknown_tools if name.strip()})
        super().__init__(detail or f"unknown tools: {', '.join(self.unknown_tools)}")


class UnknownRoleError(AuthorizationError):
    """Assignment requested for roles that do not exist.

    Refused rather than silently skipped: `noa-old`'s `assign_role` treated a missing
    role as a no-op, which let an admin believe they had granted something.
    """

    error_code: str = "unknown_roles"
    message: str = "One or more of those roles does not exist."
    status_code = 400

    def __init__(self, unknown_roles: list[str], detail: str | None = None) -> None:
        self.unknown_roles = sorted({name.strip() for name in unknown_roles if name.strip()})
        super().__init__(detail or f"unknown roles: {', '.join(self.unknown_roles)}")


class LastActiveAdminError(AuthorizationError):
    """Refused: the change would leave NOA with no active admin.

    Covers three paths — disable, delete, and removing the `admin` role — because all
    three reach the same end state. Recovery from that state needs
    `AUTH_BOOTSTRAP_ADMIN_EMAILS` and a redeploy, so the guard is worth the 409.
    """

    error_code: str = "last_active_admin"
    message: str = (
        "This is the last active admin. Give another user the admin role first, then retry."
    )
    status_code = 409


class SelfDeactivateAdminError(AuthorizationError):
    """An admin tried to disable their own account."""

    error_code: str = "self_deactivate_admin"
    message: str = "You cannot disable your own admin account. Ask another admin."
    status_code = 409


class SelfDeleteError(AuthorizationError):
    """A user tried to delete their own account.

    Applies to non-admins too: account lifecycle is an admin action, and a self-delete
    would leave the caller holding a valid session cookie for a row that no longer exists
    (the cookie's claims are not revocable before `exp`).
    """

    error_code: str = "self_delete"
    message: str = "You cannot delete your own account. Ask another admin."
    # `SelfDeleteAdminError` inherits this 409 rather than declaring its own.
    status_code = 409


class SelfDeleteAdminError(SelfDeleteError):
    """An admin tried to delete their own account.

    Subclass, so a caller that only knows `SelfDeleteError` still catches it and the
    status mapping is inherited. Distinct `error_code` because the admin panel shows a
    different hint.
    """

    error_code: str = "self_delete_admin"
    message: str = "You cannot delete your own admin account. Ask another admin."


class SelfRemoveAdminRoleError(AuthorizationError):
    """An admin tried to strip `admin` from themselves.

    Same shape as self-deactivate: it is the one demotion nobody else can undo for them
    if it was a mistake, and it can empty the admin set silently.
    """

    error_code: str = "self_remove_admin_role"
    message: str = "You cannot remove your own admin role. Ask another admin."
    status_code = 409
