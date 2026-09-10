"""`/admin/users` over HTTP.

`test_rbac_engine.py` owns the policy: what the last-active-admin guard decides, what an
internal role does to a replacement, how many tokens a disable revokes. This file owns what
only a request can prove — that every route is behind `require_admin`, that a refusal
arrives with the status and `error_code` the shared handler assigns, that the body carries the
shape the ported panel already parses, and that a successful write ends its transaction while a
refused one does not.

**T65's fifth route lives here too**, because it is a property of this surface and nothing else:
`PUT /admin/users/{id}/tools` answers 410 `direct_tool_grants_disabled`. It has no engine
half to test — there is no user-level grant table and no service method — so the route *is* the
whole implementation.

`support.admin.admin_harness` runs the real router, the real `require_admin`, the real
`AuthorizationService` and the shared error handler; only SQL and LDAP are faked. So a 403 here
is the shipped 403.

**One refusal is deliberately not tested at this level: `last_active_admin` on these routes.**
Every caller here holds `admin` and is active, so `count_active_admin_users()` is at least two
whenever the target is a *different* active admin, and when the target is the caller the
self-guards raise first. That makes the 409 unreachable through HTTP for anything but a
non-request caller (`actor_user_id=None` — a script or a future CLI). It is covered where it is
reachable: `test_rbac_engine.py::test_cannot_disable_last_active_admin` and its two siblings,
plus the status mapping in `test_rbac_routes.py`. Asserting it here would mean building a caller
production cannot produce.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import status

from core.audit.admin_events import (
    EVENT_USER_DELETED,
    EVENT_USER_ROLES_UPDATED,
    EVENT_USER_STATUS_UPDATED,
)
from core.auth.tool_catalog import TOOL_CATALOG
from core.db.models import ADMIN_ROLE_NAME
from noa_api.api.request_context import REQUEST_ID_HEADER
from support.admin import ADMIN_EMAIL, OPERATOR_EMAIL, USERS_PATH, AdminHarness, admin_harness
from support.rbac import INTERNAL_ROLE, ROLE_NOC, ROLE_SUPPORT, TOOL_CHANGE, TOOL_READ

# The four routes T51 ships, as (method, path suffix, body). Parametrized rather than repeated
# so a route added without its own gate test fails the ones below.
ROUTES: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
    ("GET", "", None),
    ("PATCH", "/{user_id}", {"is_active": False}),
    ("DELETE", "/{user_id}", None),
    ("PUT", "/{user_id}/roles", {"roles": []}),
)

# T65's route, kept separate from `ROUTES` because it never answers 200: direct per-user grants
# are withdrawn, so an admin gets 410. It shares the *gate* assertions below — being
# refused is not being ungated, and a 410 served to a non-admin would say this path exists.
REFUSING_ROUTES: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
    ("PUT", "/{user_id}/tools", {"tools": []}),
)

# Every route on the router, for the two gate tests that hold regardless of the answer.
ALL_ROUTES = ROUTES + REFUSING_ROUTES

USER_KEYS = {
    "id",
    "email",
    "display_name",
    "is_active",
    "created_at",
    "last_login_at",
    "roles",
    "tools",
}


def _call(harness: AdminHarness, method: str, suffix: str, body: dict[str, Any] | None, uid: UUID):
    """Issue one of `ROUTES` against `uid`."""
    return harness.client.request(method, USERS_PATH + suffix.format(user_id=uid), json=body)


# --- V13 + V6: the gate, on every route ---


@pytest.mark.parametrize(("method", "suffix", "body"), ROUTES)
def test_an_admin_reaches_every_user_route(
    method: str, suffix: str, body: dict[str, Any] | None
) -> None:
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()

        response = _call(harness, method, suffix, body, target.id)

    assert response.status_code == status.HTTP_200_OK, response.text


@pytest.mark.parametrize(("method", "suffix", "body"), ALL_ROUTES)
def test_every_user_route_refuses_a_non_admin(
    method: str, suffix: str, body: dict[str, Any] | None
) -> None:
    """V13: authenticated, holds a role, still refused — on all five.

    The target id is random on purpose: the gate must decide before anything is looked up, so a
    non-admin cannot use the 403/404 split to learn which ids exist.
    """
    with admin_harness() as harness:
        harness.sign_in(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,))

        response = _call(harness, method, suffix, body, uuid4())

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["error_code"] == "admin_access_required"


@pytest.mark.parametrize(("method", "suffix", "body"), ALL_ROUTES)
def test_every_user_route_refuses_a_missing_cookie(
    method: str, suffix: str, body: dict[str, Any] | None
) -> None:
    """401, not 403: no session at all, so the browser should sign in rather than give up."""
    with admin_harness() as harness:
        response = _call(harness, method, suffix, body, uuid4())

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["error_code"] == "session_invalid"


def test_a_disabled_admin_loses_the_routes_on_the_next_request() -> None:
    """V6: the row re-read runs before the role check, and the cookie is still valid.

    The session JWT has no revocation path before `exp`, so this re-read is the only thing that
    bounds a disabled admin's live session — `require_admin` inherits it by depending on
    `require_session_user` instead of reading a role claim.
    """
    with admin_harness() as harness:
        admin = harness.sign_in()
        assert harness.client.get(USERS_PATH).status_code == status.HTTP_200_OK

        admin.session.is_active = False  # what another admin's disable does to the row

        response = harness.client.get(USERS_PATH)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["error_code"] == "user_pending_approval"


def test_the_admin_role_is_read_from_the_row_not_the_cookie() -> None:
    """V14: a demotion lands on the next request — the cookie carries no role claim."""
    with admin_harness() as harness:
        admin = harness.sign_in()
        assert harness.client.get(USERS_PATH).status_code == status.HTTP_200_OK

        harness.auth_repository.user_roles[admin.id].discard(ADMIN_ROLE_NAME)

        response = harness.client.get(USERS_PATH)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["error_code"] == "admin_access_required"


# --- GET /admin/users ---


def test_the_list_carries_the_shape_the_panel_parses() -> None:
    """Exact keys: the ported panel reads these names, and a stray one is a contract
    change nobody asked for. `direct_tools` is absent by decision."""
    with admin_harness() as harness:
        harness.sign_in()
        harness.add_target(roles=(ROLE_SUPPORT,))
        harness.grant(ROLE_SUPPORT, TOOL_READ)

        user = harness.user_in_list(OPERATOR_EMAIL)

    assert set(user) == USER_KEYS
    assert user["email"] == OPERATOR_EMAIL
    assert user["is_active"] is True
    assert user["roles"] == [ROLE_SUPPORT]
    assert user["tools"] == [TOOL_READ]
    # ISO-8601, the format `formatDate` / `formatRelativeTime` in the panel expect.
    assert str(user["created_at"]).startswith("2026-")
    assert user["last_login_at"] is None


def test_the_list_shows_no_tools_for_a_disabled_user() -> None:
    """V11: `is_active=False` → zero permissions, whatever the roles say.

    Displayed state and enforced state stay equal: the panel must not show a grant the
    execution gate would refuse.
    """
    with admin_harness() as harness:
        harness.sign_in()
        harness.add_target(is_active=False, roles=(ROLE_SUPPORT,))
        harness.grant(ROLE_SUPPORT, TOOL_READ, TOOL_CHANGE)

        user = harness.user_in_list(OPERATOR_EMAIL)

    assert user["roles"] == [ROLE_SUPPORT]
    assert user["tools"] == []


def test_the_list_shows_the_whole_catalog_for_an_admin() -> None:
    """V10: `admin` bypasses the grant table, bounded by the catalog."""
    with admin_harness() as harness:
        harness.sign_in()

        user = harness.user_in_list(ADMIN_EMAIL)

    assert user["tools"] == sorted(TOOL_CATALOG)


def test_the_list_hides_internal_roles() -> None:
    """V13: internal `user:` roles are NOA's bookkeeping and are never assignable.

    The panel renders `roles` as the assignable set and PUTs that set back, so shipping one
    here would make the UI ask for exactly what the API answers 400 to.
    """
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target(roles=(ROLE_SUPPORT,))
        harness.repository.assign_internal_role(target.id, INTERNAL_ROLE)

        user = harness.user_in_list(OPERATOR_EMAIL)

    assert user["roles"] == [ROLE_SUPPORT]


def test_the_list_is_ordered_by_email() -> None:
    with admin_harness() as harness:
        harness.sign_in()
        harness.add_target("zoe@example.com")
        harness.add_target("aaron@example.com")

        emails = [user["email"] for user in harness.list_users()]

    assert emails == sorted(emails)


# --- PATCH /admin/users/{id} ---


def test_disabling_a_user_flips_the_row_and_returns_it() -> None:
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()

        response = harness.client.patch(f"{USERS_PATH}/{target.id}", json={"is_active": False})

        assert response.json()["user"]["is_active"] is False
        assert harness.repository.users[target.id].is_active is False
        assert harness.repository.commits == 1

    assert response.status_code == status.HTTP_200_OK


def test_enabling_a_pending_user_is_how_a_login_provisioned_row_goes_live() -> None:
    """V7: a first LDAP login writes the row `is_active=False`; this route is what enables it.

    That is also why T51 ships no create route — the row already exists by the time an admin
    sees it.
    """
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target(is_active=False)

        response = harness.client.patch(f"{USERS_PATH}/{target.id}", json={"is_active": True})

        assert harness.repository.users[target.id].is_active is True

    assert response.json()["user"]["is_active"] is True


def test_disabling_a_user_revokes_every_mcp_token() -> None:
    """V4: the credential itself dies with the disable, and the trail says how many went."""
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target(mcp_tokens=3)

        harness.client.patch(f"{USERS_PATH}/{target.id}", json={"is_active": False})

        assert target.id not in harness.repository.mcp_tokens
        assert harness.audit.events[-1].metadata["revoked_mcp_tokens"] == 3


def test_an_admin_disabling_their_own_account_is_409() -> None:
    """V12: nobody else could undo it for them, so it is refused — and nothing persists."""
    with admin_harness() as harness:
        admin = harness.sign_in(mcp_tokens=2)

        response = harness.client.patch(f"{USERS_PATH}/{admin.id}", json={"is_active": False})

        assert harness.repository.users[admin.id].is_active is True
        assert harness.repository.mcp_tokens[admin.id] == 2
        assert harness.repository.commits == 0
        assert harness.audit.events == []

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["error_code"] == "self_deactivate_admin"


def test_patching_an_unknown_user_is_404() -> None:
    with admin_harness() as harness:
        harness.sign_in()

        response = harness.client.patch(f"{USERS_PATH}/{uuid4()}", json={"is_active": False})

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error_code"] == "admin_user_not_found"


def test_patching_without_is_active_is_a_422_in_the_shared_envelope() -> None:
    """A malformed body answers through the one seam, so it still carries `request_id`."""
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()

        response = harness.client.patch(f"{USERS_PATH}/{target.id}", json={})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["error_code"] == "request_validation_error"


# --- DELETE /admin/users/{id} ---


def test_deleting_a_user_answers_ok_and_removes_the_row() -> None:
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()

        response = harness.client.delete(f"{USERS_PATH}/{target.id}")

        assert target.id not in harness.repository.users
        assert harness.repository.commits == 1

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"ok": True}


def test_an_admin_deleting_their_own_account_is_409() -> None:
    """V12 names this one explicitly. It would also leave a valid cookie for a missing row."""
    with admin_harness() as harness:
        admin = harness.sign_in()

        response = harness.client.delete(f"{USERS_PATH}/{admin.id}")

        assert admin.id in harness.repository.users
        assert harness.repository.commits == 0

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["error_code"] == "self_delete_admin"


def test_deleting_an_unknown_user_is_404() -> None:
    with admin_harness() as harness:
        harness.sign_in()

        response = harness.client.delete(f"{USERS_PATH}/{uuid4()}")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error_code"] == "admin_user_not_found"


# --- PUT /admin/users/{id}/roles ---


def test_putting_roles_replaces_the_set_and_returns_the_effective_tools() -> None:
    """The response is the server's answer about what those roles grant, not the panel's guess."""
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target(roles=(ROLE_SUPPORT,))
        harness.grant(ROLE_SUPPORT, TOOL_READ)
        harness.grant(ROLE_NOC, TOOL_CHANGE)

        response = harness.client.put(f"{USERS_PATH}/{target.id}/roles", json={"roles": [ROLE_NOC]})

        assert harness.repository.commits == 1

    user = response.json()["user"]
    assert user["roles"] == [ROLE_NOC]
    assert user["tools"] == [TOOL_CHANGE]


def test_putting_an_internal_role_is_400() -> None:
    """V13: `user:` roles are assigned by NOA only."""
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()

        response = harness.client.put(
            f"{USERS_PATH}/{target.id}/roles", json={"roles": [INTERNAL_ROLE]}
        )

        assert harness.repository.commits == 0

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error_code"] == "internal_role_forbidden"


def test_putting_roles_preserves_an_internal_role_the_caller_never_sent() -> None:
    """V13/V75: replacement clears assignable roles only. Even an empty list keeps `user:`."""
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target(roles=(ROLE_SUPPORT,))
        harness.repository.assign_internal_role(target.id, INTERNAL_ROLE)

        response = harness.client.put(f"{USERS_PATH}/{target.id}/roles", json={"roles": []})

        assert INTERNAL_ROLE in harness.repository.user_roles[target.id]

    # Preserved in the store, still hidden on the wire.
    assert response.json()["user"]["roles"] == []


def test_putting_an_unknown_role_is_400() -> None:
    """A missing role is refused, never silently skipped: `noa-old` let an admin believe they
    had granted something."""
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()

        response = harness.client.put(f"{USERS_PATH}/{target.id}/roles", json={"roles": ["nope"]})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error_code"] == "unknown_roles"


def test_an_admin_removing_their_own_admin_role_is_409() -> None:
    """V12: the one demotion nobody else can undo for them, and it can empty the admin set."""
    with admin_harness() as harness:
        admin = harness.sign_in()
        harness.repository.grant(ROLE_SUPPORT)

        response = harness.client.put(
            f"{USERS_PATH}/{admin.id}/roles", json={"roles": [ROLE_SUPPORT]}
        )

        assert ADMIN_ROLE_NAME in harness.repository.user_roles[admin.id]
        assert harness.repository.commits == 0

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["error_code"] == "self_remove_admin_role"


def test_putting_roles_for_an_unknown_user_is_404() -> None:
    with admin_harness() as harness:
        harness.sign_in()

        response = harness.client.put(f"{USERS_PATH}/{uuid4()}/roles", json={"roles": []})

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error_code"] == "admin_user_not_found"


def test_a_role_change_is_visible_on_the_very_next_list() -> None:
    """V14: permission updates take effect immediately — nothing behind these routes caches."""
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()
        harness.grant(ROLE_SUPPORT, TOOL_READ)
        assert harness.user_in_list(OPERATOR_EMAIL)["tools"] == []

        harness.client.put(f"{USERS_PATH}/{target.id}/roles", json={"roles": [ROLE_SUPPORT]})

        assert harness.user_in_list(OPERATOR_EMAIL)["tools"] == [TOOL_READ]


# --- PUT /admin/users/{id}/tools: withdrawn ---


def _put_tools(harness: AdminHarness, user_id: UUID, **kwargs: Any):
    return harness.client.put(f"{USERS_PATH}/{user_id}/tools", **kwargs)


def test_setting_user_tools_directly_is_410_direct_tool_grants_disabled() -> None:
    """V75: permissions flow role → user, so there is no per-user grant to write.

    410 rather than 404 or 403 — the route existed in `noa-old` and the capability behind it is
    withdrawn permanently, which is the one thing neither of the others says. The `error_code` is
    `noa-old`'s string verbatim, because the ported panel branches on it.
    """
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()

        response = _put_tools(harness, target.id, json={"tools": [TOOL_READ]})

        # Nothing was written and nothing was ended: a refusal, not a no-op write.
        assert harness.repository.commits == 0
        assert harness.audit.events == []

    assert response.status_code == status.HTTP_410_GONE
    assert response.json()["error_code"] == "direct_tool_grants_disabled"


def test_the_410_holds_for_a_user_that_does_not_exist() -> None:
    """No existence oracle, and no "it might have worked for a real user" either.

    Every other route on this router answers 404 for an unknown id. This one must not: a status
    that varied with whether the row existed would make the withdrawn route a probe for `users`,
    and would tell a caller their request was refused for the *target* rather than at all.
    """
    with admin_harness() as harness:
        harness.sign_in()

        known = harness.add_target()
        for_known = _put_tools(harness, known.id, json={"tools": []})
        for_unknown = _put_tools(harness, uuid4(), json={"tools": []})

    assert for_unknown.status_code == for_known.status_code == status.HTTP_410_GONE
    assert for_unknown.json()["error_code"] == for_known.json()["error_code"]


@pytest.mark.parametrize(
    ("kwargs", "why"),
    [
        ({"json": {"tools": ["nope"]}}, "a tool outside the catalog"),
        ({"json": {"tools": "not-a-list"}}, "the wrong type for the field"),
        ({"json": {"unexpected": True}}, "no `tools` field at all"),
        ({"content": b"{not json"}, "a body that is not JSON"),
        ({}, "no body"),
    ],
)
def test_the_410_needs_no_valid_body(kwargs: dict[str, Any], why: str) -> None:
    """The refusal is about the route, so it cannot be dodged into a 422.

    `noa-old`'s handler declared a request model and then discarded it, which means FastAPI
    validated first: `{"tools": 3}` answered 422 about a *field* on a route that will never
    accept any field. Every body here is 410, including none.
    """
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()

        response = _put_tools(harness, target.id, **kwargs)

    assert response.status_code == status.HTTP_410_GONE, f"{why}: {response.text}"
    assert response.json()["error_code"] == "direct_tool_grants_disabled"


def test_the_410_body_carries_the_shared_envelope() -> None:
    """V8, V73: the withdrawn route answers in the same shape as every other failure.

    It raises rather than returning a response of its own, so `request_id` and `x-request-id`
    come from the shared handler — a route that built its own body is how one surface ends up
    without the id an operator quotes.
    """
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()

        response = _put_tools(harness, target.id, json={"tools": []})

    body = response.json()
    assert set(body) == {"error_code", "message", "request_id"}
    assert response.headers[REQUEST_ID_HEADER] == body["request_id"]
    # V8: the diagnostic names the id, the body does not.
    assert str(target.id) not in response.text


def test_the_410_leaves_the_users_effective_tools_alone() -> None:
    """The refusal changes nothing — asserted on the next read rather than on the 410.

    Without this the route could answer 410 *after* writing, and every assertion above would
    still pass.
    """
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target(roles=(ROLE_SUPPORT,))
        harness.grant(ROLE_SUPPORT, TOOL_READ)

        _put_tools(harness, target.id, json={"tools": [TOOL_CHANGE]})

        assert harness.user_in_list(OPERATOR_EMAIL)["tools"] == [TOOL_READ]


# --- V14: one audit event per change, from the route ---


@pytest.mark.parametrize(
    ("method", "suffix", "body", "event_type"),
    [
        ("PATCH", "/{user_id}", {"is_active": False}, EVENT_USER_STATUS_UPDATED),
        ("PUT", "/{user_id}/roles", {"roles": []}, EVENT_USER_ROLES_UPDATED),
        ("DELETE", "/{user_id}", None, EVENT_USER_DELETED),
    ],
)
def test_every_mutating_route_records_one_audit_event(
    method: str, suffix: str, body: dict[str, Any] | None, event_type: str
) -> None:
    """The actor is the signed-in admin, resolved from the cookie and never from the body."""
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()

        _call(harness, method, suffix, body, target.id)

        events = harness.audit.events

    assert [event.event_type for event in events] == [event_type]
    assert events[0].actor_email == ADMIN_EMAIL
    assert events[0].target == str(target.id)


def test_the_read_route_records_nothing() -> None:
    """V14 is about changes. A trail of list calls is V45's job, on another surface."""
    with admin_harness() as harness:
        harness.sign_in()
        harness.add_target()

        harness.list_users()

        assert harness.audit.events == []


# --- V8 + V73: the error envelope, from a real route ---


def test_a_404_body_carries_no_internal_detail() -> None:
    """V8: `detail` names the row that vanished. Body gets `error_code`, `message`,
    `request_id` — nothing else."""
    missing = uuid4()
    with admin_harness() as harness:
        harness.sign_in()

        response = harness.client.delete(f"{USERS_PATH}/{missing}")

    body = response.json()
    assert set(body) == {"error_code", "message", "request_id"}
    assert str(missing) not in response.text


def test_an_error_body_and_header_share_one_request_id() -> None:
    """V73: same value in the body and in `x-request-id`, so an operator can quote either."""
    with admin_harness() as harness:
        harness.sign_in()

        response = harness.client.delete(f"{USERS_PATH}/{uuid4()}")

    assert response.headers[REQUEST_ID_HEADER] == response.json()["request_id"]
