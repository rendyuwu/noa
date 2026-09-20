"""`require_admin` and the authorization status mapping.

The RBAC engine ships no `/admin` routes (the user/role/token routes own those), but two of its
cited invariants are HTTP properties: "non-admin users → 403 on admin endpoints" and "admin
self-delete →
409". Both are decided by `noa_api.api.deps.require_admin` and
each class's own `status_code`, so they are tested at that level rather than deferred
to the first route that happens to use them.

`support.rbac.admin_probe_app` mounts one throwaway route behind `require_admin`. Everything
else on the path is production code — `require_session_user`, the real `AuthService`, the
real `JWTService`, the shared error handler — so a 403 here is the 403 the user routes return.
"""

from __future__ import annotations

import pytest
from fastapi import status

from core.auth.authorization_errors import (
    AdminAccessRequiredError,
    AuthorizationError,
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
from core.db.models import ADMIN_ROLE_NAME
from noa_api.api.admin_errors import DirectGrantsDisabledError
from noa_api.api.errors import error_body
from support.errors import error_subclasses
from support.rbac import PROBE_PATH, ROLE_SUPPORT, admin_probe_app

ADMIN_EMAIL = "admin@example.com"
OPERATOR_EMAIL = "operator@example.com"


# --- the admin gate: non-admin refused ---


def test_admin_reaches_an_admin_route() -> None:
    with admin_probe_app() as harness:
        harness.sign_in(ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))

        response = harness.client.get(PROBE_PATH)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"email": ADMIN_EMAIL}


def test_non_admin_gets_403_admin_access_required() -> None:
    """Authenticated, holds roles, still refused — and told why."""
    with admin_probe_app() as harness:
        harness.sign_in(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,))

        response = harness.client.get(PROBE_PATH)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["error_code"] == "admin_access_required"


def test_user_with_no_roles_gets_403() -> None:
    with admin_probe_app() as harness:
        harness.sign_in(OPERATOR_EMAIL)

        response = harness.client.get(PROBE_PATH)

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_missing_cookie_gets_401_not_403() -> None:
    """No session → 401 `session_invalid`, so the browser signs in instead of giving up."""
    with admin_probe_app() as harness:
        response = harness.client.get(PROBE_PATH)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["error_code"] == "session_invalid"


def test_disabled_admin_loses_the_admin_route_on_the_next_request() -> None:
    """The session row re-read runs before the role check.

    A valid, unexpired cookie stops working the moment the row flips, because the session
    JWT itself is not revocable — `require_admin` inherits that guarantee by depending
    on `require_session_user` rather than reading a role claim.
    """
    with admin_probe_app() as harness:
        admin = harness.sign_in(ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))
        assert harness.client.get(PROBE_PATH).status_code == status.HTTP_200_OK

        admin.is_active = False  # what an admin's disable does to the row

        response = harness.client.get(PROBE_PATH)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["error_code"] == "user_pending_approval"


def test_admin_role_lost_between_requests_stops_working() -> None:
    """The cookie carries no role claim, so a demotion lands on the next request."""
    with admin_probe_app() as harness:
        admin = harness.sign_in(ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))
        assert harness.client.get(PROBE_PATH).status_code == status.HTTP_200_OK

        harness.repository.user_roles[admin.id].discard(ADMIN_ROLE_NAME)

        response = harness.client.get(PROBE_PATH)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["error_code"] == "admin_access_required"


# --- status mapping ---


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (AdminAccessRequiredError(), status.HTTP_403_FORBIDDEN),
        (ReservedRoleError(), status.HTTP_403_FORBIDDEN),
        (UserNotFoundError(), status.HTTP_404_NOT_FOUND),
        (RoleNotFoundError(), status.HTTP_404_NOT_FOUND),
        (InvalidRoleNameError(), status.HTTP_400_BAD_REQUEST),
        (InternalRoleError(), status.HTTP_400_BAD_REQUEST),
        (UnknownToolError(["nope"]), status.HTTP_400_BAD_REQUEST),
        (UnknownRoleError(["nope"]), status.HTTP_400_BAD_REQUEST),
        (LastActiveAdminError(), status.HTTP_409_CONFLICT),
        (SelfDeactivateAdminError(), status.HTTP_409_CONFLICT),
        (SelfDeleteError(), status.HTTP_409_CONFLICT),
        # The last-admin guards name this one explicitly: admin self-delete → 409, inherited from
        # `SelfDeleteError` rather than declared.
        (SelfDeleteAdminError(), status.HTTP_409_CONFLICT),
        (SelfRemoveAdminRoleError(), status.HTTP_409_CONFLICT),
        (AuthorizationError(), status.HTTP_403_FORBIDDEN),
    ],
)
def test_status_for_every_authorization_error(
    error: AuthorizationError, expected_status: int
) -> None:
    assert error.status_code == expected_status


def test_direct_grants_disabled_is_410_and_is_not_an_authorization_error() -> None:
    """The 410 on direct grants, and the reason it sits outside the `AuthorizationError` tree.

    Two assertions, and the second is what keeps the first honest. The class must answer 410
    — a withdrawn capability, not a missing row and not a permission the caller lacks. But
    `test_error_status_taxonomy.py` pins every `AuthorizationError` to {400, 403, 404, 409},
    and that closed set is the assertion: it is what stops a permission problem answering
    "service unavailable". Had this class been added to that tree, the set would have had to
    open to admit 410 and the guard would have been weakened to fit one error. So it derives
    from `NoaError` directly, and this test pins that — a later edit that "tidies" it into the
    taxonomy fails here rather than quietly loosening the tree.
    """
    assert DirectGrantsDisabledError.status_code == status.HTTP_410_GONE
    assert not issubclass(DirectGrantsDisabledError, AuthorizationError)
    assert status.HTTP_410_GONE not in {
        klass.status_code for klass in error_subclasses(AuthorizationError)
    }


def test_authorization_error_codes_are_unique() -> None:
    """Clients branch on `error_code`, so two classes sharing one string is a bug."""
    codes = [
        klass.error_code
        for klass in error_subclasses(AuthorizationError)
        if klass is not AuthorizationError
    ]
    assert len(codes) == len(set(codes))


def test_error_body_omits_internal_detail_for_authorization_errors() -> None:
    """`detail` names the row that vanished or the roles held. Logs only."""
    error = UserNotFoundError("no `users` row for `deadbeef`")

    body = error_body(error)

    assert body == {"error_code": "admin_user_not_found", "message": error.message}
    assert "deadbeef" not in str(body)


def test_unknown_tool_error_names_the_offenders() -> None:
    """The list is deduplicated, stripped and sorted, so a response can render it."""
    error = UnknownToolError([" pmg_nope ", "whm_nope", "pmg_nope", "  "])

    assert error.unknown_tools == ["pmg_nope", "whm_nope"]
