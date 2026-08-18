"""`require_admin` and the authorization status mapping (T9, T65 — V12, V13, V73, V75).

T9 ships no `/admin` routes (T51-T55 own those), but two of its cited invariants are HTTP
properties: V13's "non-admin users → 403 on admin endpoints" and V12's "admin self-delete →
409". Both are decided by `noa_api.api.deps.require_admin` and
`noa_api.api.errors.STATUS_BY_ERROR`, so they are tested at that level rather than deferred
to the first route that happens to use them.

`support.rbac.admin_probe_app` mounts one throwaway route behind `require_admin`. Everything
else on the path is production code — `require_session_user`, the real `AuthService`, the
real `JWTService`, the shared error handler — so a 403 here is the 403 T51 will return.
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
from noa_api.api.errors import FALLBACK_STATUS, STATUS_BY_ERROR, error_body, status_for
from support.rbac import PROBE_PATH, ROLE_SUPPORT, admin_probe_app

ADMIN_EMAIL = "admin@example.com"
OPERATOR_EMAIL = "operator@example.com"


# --- V13: the admin gate ---


def test_admin_reaches_an_admin_route() -> None:
    with admin_probe_app() as harness:
        harness.sign_in(ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))

        response = harness.client.get(PROBE_PATH)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"email": ADMIN_EMAIL}


def test_non_admin_gets_403_admin_access_required() -> None:
    """V13: authenticated, holds roles, still refused — and told why."""
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
    """V6 + V13: the row re-read runs before the role check.

    A valid, unexpired cookie stops working the moment the row flips, because the session
    JWT itself is not revocable (V6) — `require_admin` inherits that guarantee by depending
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
    """The cookie carries no role claim, so a demotion lands on the next request (V14)."""
    with admin_probe_app() as harness:
        admin = harness.sign_in(ADMIN_EMAIL, roles=(ADMIN_ROLE_NAME,))
        assert harness.client.get(PROBE_PATH).status_code == status.HTTP_200_OK

        harness.repository.user_roles[admin.id].discard(ADMIN_ROLE_NAME)

        response = harness.client.get(PROBE_PATH)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["error_code"] == "admin_access_required"


# --- V73: status mapping ---


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
        # V12 names this one explicitly: admin self-delete → 409, inherited through the MRO.
        (SelfDeleteAdminError(), status.HTTP_409_CONFLICT),
        (SelfRemoveAdminRoleError(), status.HTTP_409_CONFLICT),
        (AuthorizationError(), status.HTTP_403_FORBIDDEN),
    ],
)
def test_status_for_every_authorization_error(
    error: AuthorizationError, expected_status: int
) -> None:
    assert status_for(error) == expected_status


def test_every_authorization_error_is_mapped_explicitly() -> None:
    """No authorization error may reach the 503 fallback — that is an auth answer.

    Walks the subclass tree, so a class added later without a `STATUS_BY_ERROR` entry fails
    here instead of returning "service unavailable" for a permission problem.
    """

    def subclasses(klass: type[AuthorizationError]) -> set[type[AuthorizationError]]:
        found = {klass}
        for child in klass.__subclasses__():
            found |= subclasses(child)
        return found

    for klass in subclasses(AuthorizationError):
        status_code = status_for(klass.__new__(klass))
        assert status_code != FALLBACK_STATUS, f"{klass.__name__} falls back to 503"
        assert status_code in {
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_409_CONFLICT,
        }


def test_direct_grants_disabled_is_410_and_is_not_an_authorization_error() -> None:
    """T65/V75's refusal, and the reason it sits outside the tree above.

    Two assertions, and the second is what keeps the first honest. `status_for` must answer 410
    — a withdrawn capability, not a missing row and not a permission the caller lacks. But the
    tree test above asserts every `AuthorizationError` maps into {400, 403, 404, 409}, and that
    closed set is the assertion: it is what stops a permission problem answering "service
    unavailable". Had this class been added to that tree, the set would have had to open to
    admit 410 and the guard would have been weakened to fit one error. So it derives from
    `NoaError` directly, and this test pins that — a later edit that "tidies" it into the
    taxonomy fails here rather than quietly loosening the tree.
    """
    assert status_for(DirectGrantsDisabledError()) == status.HTTP_410_GONE
    assert not issubclass(DirectGrantsDisabledError, AuthorizationError)
    assert status.HTTP_410_GONE not in {
        status_for(klass.__new__(klass))
        for klass in STATUS_BY_ERROR
        if issubclass(klass, AuthorizationError)
    }


def test_authorization_error_codes_are_unique() -> None:
    """Clients branch on `error_code`, so two classes sharing one string is a bug."""
    codes = [
        klass.error_code
        for klass in STATUS_BY_ERROR
        if issubclass(klass, AuthorizationError) and klass is not AuthorizationError
    ]
    assert len(codes) == len(set(codes))


def test_error_body_omits_internal_detail_for_authorization_errors() -> None:
    """V8: `detail` names the row that vanished or the roles held. Logs only."""
    error = UserNotFoundError("no `users` row for `deadbeef`")

    body = error_body(error)

    assert body == {"error_code": "admin_user_not_found", "message": error.message}
    assert "deadbeef" not in str(body)


def test_unknown_tool_error_names_the_offenders() -> None:
    """The list is deduplicated, stripped and sorted, so a response can render it."""
    error = UnknownToolError([" pmg_nope ", "whm_nope", "pmg_nope", "  "])

    assert error.unknown_tools == ["pmg_nope", "whm_nope"]
