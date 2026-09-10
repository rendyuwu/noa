"""`/admin/roles` and `/admin/tools` over HTTP.

`test_rbac_engine.py` owns the policy: what the role-name validator accepts, what the reserved
`admin` role refuses, how a grant set is normalized. This file owns what only a request can
prove — that all six routes are behind `require_admin`, that a refusal arrives with the status
and `error_code` the shared handler assigns, that the bodies carry the shape the ported panel
already parses (`apps/admin-web/src/lib/admin/roles/roles-api.ts`), and that a successful write
ends its transaction while a refused one does not.

`support.admin.admin_harness` runs the real routers, the real `require_admin`, the real
`AuthorizationService` and the shared error handler; only SQL and LDAP are faked. So a 403 here
is the shipped 403. The live `commit()` — the half a double cannot witness (B10, V100c) — is
asserted against Postgres in
`test_rbac_repository.py::test_commit_makes_a_role_grant_outlive_the_request`.

**`last_active_admin` cannot reach these routes at all**, unlike T51's: no role route touches a
user's admin status. The nearest thing, deleting the `admin` role itself, is refused earlier by
`reserved_role`.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from fastapi import status

from core.audit.admin_events import (
    EVENT_ROLE_CREATED,
    EVENT_ROLE_DELETED,
    EVENT_ROLE_TOOLS_UPDATED,
)
from core.auth.tool_catalog import TOOL_CATALOG
from core.db.models import ADMIN_ROLE_NAME
from noa_api.api.request_context import REQUEST_ID_HEADER
from support.admin import (
    ADMIN_EMAIL,
    OPERATOR_EMAIL,
    ROLES_PATH,
    TOOLS_PATH,
    USERS_PATH,
    AdminHarness,
    admin_harness,
)
from support.rbac import INTERNAL_ROLE, ROLE_NOC, ROLE_SUPPORT, TOOL_CHANGE, TOOL_READ, TOOL_UNKNOWN

# The six routes T52 ships, as (method, path template, body). Parametrized rather than repeated
# so a seventh route added without its own gate test fails the ones below.
ROUTES: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
    ("GET", ROLES_PATH, None),
    ("POST", ROLES_PATH, {"name": ROLE_NOC}),
    ("DELETE", ROLES_PATH + "/{role}", None),
    ("GET", ROLES_PATH + "/{role}/tools", None),
    ("PUT", ROLES_PATH + "/{role}/tools", {"tools": []}),
    ("GET", TOOLS_PATH, None),
)

# The three writes, with the role each needs to exist (or not) beforehand and the event it owes.
MUTATIONS: tuple[tuple[str, str, dict[str, Any] | None, str, str], ...] = (
    ("POST", ROLES_PATH, {"name": ROLE_NOC}, EVENT_ROLE_CREATED, ROLE_NOC),
    (
        "PUT",
        f"{ROLES_PATH}/{ROLE_SUPPORT}/tools",
        {"tools": [TOOL_READ]},
        EVENT_ROLE_TOOLS_UPDATED,
        ROLE_SUPPORT,
    ),
    ("DELETE", f"{ROLES_PATH}/{ROLE_SUPPORT}", None, EVENT_ROLE_DELETED, ROLE_SUPPORT),
)

GHOST_ROLE = "ghostrole"


def _call(harness: AdminHarness, method: str, path: str, body: dict[str, Any] | None, role: str):
    """Issue one of `ROUTES` against `role`."""
    return harness.client.request(method, path.format(role=role), json=body)


# --- V13 + V6: the gate, on every route ---


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_an_admin_reaches_every_role_route(
    method: str, path: str, body: dict[str, Any] | None
) -> None:
    """The positive control. Without it every refusal test below passes against a 404."""
    with admin_harness() as harness:
        harness.sign_in()
        harness.grant(ROLE_SUPPORT)

        response = _call(harness, method, path, body, ROLE_SUPPORT)

    assert response.status_code == status.HTTP_200_OK, response.text


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_every_role_route_refuses_a_non_admin(
    method: str, path: str, body: dict[str, Any] | None
) -> None:
    """V13: authenticated, holds a role, still refused — on all six.

    The role name is random *and well-formed* on purpose: the gate must decide before anything
    is looked up or validated, so a non-admin cannot use the 403/404/400 split to learn which
    roles exist.
    """
    with admin_harness() as harness:
        harness.sign_in(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,))

        response = _call(harness, method, path, body, uuid4().hex)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["error_code"] == "admin_access_required"


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_every_role_route_refuses_a_missing_cookie(
    method: str, path: str, body: dict[str, Any] | None
) -> None:
    """401, not 403: no session at all, so the browser should sign in rather than give up."""
    with admin_harness() as harness:
        response = _call(harness, method, path, body, uuid4().hex)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["error_code"] == "session_invalid"


def test_a_disabled_admin_loses_the_role_routes_on_the_next_request() -> None:
    """V6: the row re-read runs before the role check, and the cookie is still valid.

    The session JWT has no revocation path before `exp`, so this re-read is the only thing that
    bounds a disabled admin's live session — and a grant editor is the surface where that
    matters most.
    """
    with admin_harness() as harness:
        admin = harness.sign_in()
        assert harness.client.get(ROLES_PATH).status_code == status.HTTP_200_OK

        admin.session.is_active = False  # what another admin's disable does to the row

        response = harness.client.get(ROLES_PATH)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["error_code"] == "user_pending_approval"


# --- GET /admin/roles ---


def test_the_list_carries_assignable_roles_and_admin() -> None:
    """`admin` is a real row and stays visible; the panel's users page assigns it.

    Hiding it would make the displayed role set smaller than the assignable one, which is the
    same disagreement V10 refuses one read over (see `test_the_admin_role_reports_the_whole_
    catalog`).
    """
    with admin_harness() as harness:
        harness.sign_in()
        harness.grant(ROLE_SUPPORT)
        harness.grant(ROLE_NOC)

        assert harness.list_roles() == [ADMIN_ROLE_NAME, ROLE_NOC, ROLE_SUPPORT]


def test_the_list_omits_internal_roles() -> None:
    """V13, V75: `user:` roles are NOA's bookkeeping. Offering one would ask the panel for a
    name `PUT /admin/users/{id}/roles` refuses with 400."""
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()
        harness.repository.assign_internal_role(target.id, INTERNAL_ROLE)

        assert INTERNAL_ROLE not in harness.list_roles()


def test_the_wire_shapes_are_the_ones_the_panel_reads() -> None:
    """§I.admin-api, field for field: `{roles}`, `{name}`, `{tools}`, `{ok}`.

    The ported panel already parses these, so a renamed key is a broken page rather than
    a caught type error — its reader is `roles-api.ts`, not a generated client.
    """
    with admin_harness() as harness:
        harness.sign_in()

        assert set(harness.client.get(ROLES_PATH).json()) == {"roles"}
        assert harness.client.post(ROLES_PATH, json={"name": ROLE_NOC}).json() == {"name": ROLE_NOC}
        assert set(harness.client.get(f"{ROLES_PATH}/{ROLE_NOC}/tools").json()) == {"tools"}
        assert harness.client.put(
            f"{ROLES_PATH}/{ROLE_NOC}/tools", json={"tools": [TOOL_READ]}
        ).json() == {"tools": [TOOL_READ]}
        assert harness.client.delete(f"{ROLES_PATH}/{ROLE_NOC}").json() == {"ok": True}
        assert set(harness.client.get(TOOLS_PATH).json()) == {"tools"}


# --- POST /admin/roles ---


def test_a_created_role_is_in_the_next_list() -> None:
    with admin_harness() as harness:
        harness.sign_in()

        response = harness.client.post(ROLES_PATH, json={"name": ROLE_NOC})

        assert response.status_code == status.HTTP_200_OK, response.text
        assert ROLE_NOC in harness.list_roles()


def test_a_created_role_starts_with_no_grants() -> None:
    """Creation cannot hand out a permission by itself: the grants are a second call."""
    with admin_harness() as harness:
        harness.sign_in()
        harness.client.post(ROLES_PATH, json={"name": ROLE_NOC})

        assert harness.role_tools(ROLE_NOC) == []


def test_the_stored_name_is_returned_not_the_submitted_one() -> None:
    """The service strips; echoing the caller's string would hand the panel a key its own next
    request spells differently."""
    with admin_harness() as harness:
        harness.sign_in()

        response = harness.client.post(ROLES_PATH, json={"name": f"  {ROLE_NOC}  "})

        assert response.json() == {"name": ROLE_NOC}


def test_recreating_a_role_is_idempotent_and_records_nothing() -> None:
    """A double-submitted dialog is not an error an operator has to interpret — and an audit
    trail that logs non-changes is one nobody reads."""
    with admin_harness() as harness:
        harness.sign_in()
        harness.client.post(ROLES_PATH, json={"name": ROLE_NOC})
        harness.audit.events.clear()
        commits_before = harness.repository.commits

        response = harness.client.post(ROLES_PATH, json={"name": ROLE_NOC})

        assert response.status_code == status.HTTP_200_OK, response.text
        assert harness.audit.events == []
        assert harness.repository.commits == commits_before


@pytest.mark.parametrize("name", ["", "   ", "a" * 101, "支持", "role name", INTERNAL_ROLE])
def test_a_malformed_role_name_is_refused(name: str) -> None:
    """400 `invalid_role_name`: blank, over 100 characters, or outside `[A-Za-z0-9_-]`.

    `user:`-prefixed names land here too, because `:` is outside the pattern — the clearer
    `internal_role_forbidden` code belongs to role *assignment* (`PUT /admin/users/{id}/roles`),
    which checks the prefix first.
    """
    with admin_harness() as harness:
        harness.sign_in()

        response = harness.client.post(ROLES_PATH, json={"name": name})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error_code"] == "invalid_role_name"


# --- V13: `admin` is reserved ---


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", ROLES_PATH, {"name": ADMIN_ROLE_NAME}),
        ("DELETE", f"{ROLES_PATH}/{ADMIN_ROLE_NAME}", None),
        ("PUT", f"{ROLES_PATH}/{ADMIN_ROLE_NAME}/tools", {"tools": [TOOL_READ]}),
    ],
)
def test_the_admin_role_refuses_every_write(
    method: str, path: str, body: dict[str, Any] | None
) -> None:
    """403 `reserved_role`, not 404: pretending it is absent would be a lie the panel renders.

    V10 gives `admin` every known tool by bypassing the grant table, so rows written here would
    be decoration implying a limit NOA does not enforce.
    """
    with admin_harness() as harness:
        harness.sign_in()

        response = harness.client.request(method, path, json=body)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["error_code"] == "reserved_role"


def test_the_admin_role_reports_the_whole_catalog() -> None:
    """V10: displayed state equals enforced state.

    `admin` holds no `role_tool_permissions` rows, so a read that answered from the table alone
    would show a role permitting nothing while it permits everything.
    """
    with admin_harness() as harness:
        harness.sign_in()

        assert harness.role_tools(ADMIN_ROLE_NAME) == sorted(TOOL_CATALOG)
        assert harness.repository.role_tools.get(ADMIN_ROLE_NAME) is None


# --- DELETE /admin/roles/{name} ---


def test_deleting_a_role_removes_it_and_its_assignments() -> None:
    """V14: everyone who held it loses those tools on their next request — there is no orphaned
    grant row left to resolve."""
    with admin_harness() as harness:
        harness.sign_in()
        harness.grant(ROLE_SUPPORT, TOOL_READ)
        target = harness.add_target(roles=(ROLE_SUPPORT,))
        assert harness.user_in_list(OPERATOR_EMAIL)["tools"] == [TOOL_READ]

        response = harness.client.delete(f"{ROLES_PATH}/{ROLE_SUPPORT}")

        assert response.status_code == status.HTTP_200_OK, response.text
        assert ROLE_SUPPORT not in harness.list_roles()
        assert harness.user_in_list(OPERATOR_EMAIL)["roles"] == []
        assert harness.user_in_list(OPERATOR_EMAIL)["tools"] == []
        assert harness.repository.user_roles[target.id] == set()


def test_deleting_an_absent_role_is_404() -> None:
    """Not a silent success: the panel would otherwise show a stale row disappearing twice."""
    with admin_harness() as harness:
        harness.sign_in()

        response = harness.client.delete(f"{ROLES_PATH}/{GHOST_ROLE}")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error_code"] == "admin_role_not_found"


# --- GET/PUT /admin/roles/{name}/tools ---


def test_the_grant_set_is_read_back_as_stored() -> None:
    """Normalized by the service — stripped, de-duplicated, sorted — then re-read, so the panel
    renders what a permission check would resolve and not its own echo."""
    with admin_harness() as harness:
        harness.sign_in()
        harness.grant(ROLE_SUPPORT)

        response = harness.client.put(
            f"{ROLES_PATH}/{ROLE_SUPPORT}/tools",
            json={"tools": [TOOL_CHANGE, f" {TOOL_READ} ", TOOL_CHANGE, "  "]},
        )

        assert response.json() == {"tools": sorted([TOOL_READ, TOOL_CHANGE])}
        assert harness.role_tools(ROLE_SUPPORT) == sorted([TOOL_READ, TOOL_CHANGE])


def test_a_grant_replaces_rather_than_adds() -> None:
    """The body is the full desired set: a delta would have to guess whether an absent name
    means "leave it" or "revoke it"."""
    with admin_harness() as harness:
        harness.sign_in()
        harness.grant(ROLE_SUPPORT, TOOL_READ, TOOL_CHANGE)

        harness.client.put(f"{ROLES_PATH}/{ROLE_SUPPORT}/tools", json={"tools": [TOOL_READ]})

        assert harness.role_tools(ROLE_SUPPORT) == [TOOL_READ]


def test_a_grant_change_is_visible_on_the_very_next_user_list() -> None:
    """V14: permission updates take effect immediately — nothing between these two routes
    caches, and they are the pair an operator judges a grant by."""
    with admin_harness() as harness:
        harness.sign_in()
        harness.grant(ROLE_SUPPORT)
        harness.add_target(roles=(ROLE_SUPPORT,))
        assert harness.user_in_list(OPERATOR_EMAIL)["tools"] == []

        harness.client.put(f"{ROLES_PATH}/{ROLE_SUPPORT}/tools", json={"tools": [TOOL_READ]})

        assert harness.user_in_list(OPERATOR_EMAIL)["tools"] == [TOOL_READ]


def test_unknown_tools_are_refused_as_a_set() -> None:
    """V10: a name outside the catalog is 400 `unknown_tools`, and every offender is named in
    `detail` at once so an admin who mistyped one of twenty does not bisect the list."""
    with admin_harness() as harness:
        harness.sign_in()
        harness.grant(ROLE_SUPPORT)

        response = harness.client.put(
            f"{ROLES_PATH}/{ROLE_SUPPORT}/tools", json={"tools": [TOOL_READ, TOOL_UNKNOWN]}
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error_code"] == "unknown_tools"


@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_the_grant_routes_404_an_absent_role(method: str) -> None:
    with admin_harness() as harness:
        harness.sign_in()

        response = harness.client.request(
            method, f"{ROLES_PATH}/{GHOST_ROLE}/tools", json={"tools": []}
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error_code"] == "admin_role_not_found"


@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_a_malformed_role_name_is_400_not_404_on_the_grant_routes(method: str) -> None:
    """The validator runs on reads too, so the 400/404 split cannot be used to probe what it
    accepts. `{name}` is a free-form path segment here, unlike T51's `UUID` params, so this is
    the one place that split is reachable at all."""
    with admin_harness() as harness:
        harness.sign_in()

        response = harness.client.request(
            method, f"{ROLES_PATH}/{INTERNAL_ROLE}/tools", json={"tools": []}
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error_code"] == "invalid_role_name"


# --- GET /admin/tools ---


def test_the_tools_route_answers_the_catalog() -> None:
    """The vocabulary `PUT .../tools` validates against, read off the same set — so what the
    allowlist editor offers and what the service accepts cannot drift."""
    with admin_harness() as harness:
        harness.sign_in()

        assert harness.client.get(TOOLS_PATH).json() == {"tools": sorted(TOOL_CATALOG)}


def test_the_tools_route_follows_the_services_own_catalog() -> None:
    """A construction-time `known_tools` override moves both the offer and the check.

    Asserted because the alternative implementation — importing `TOOL_CATALOG` in the route —
    passes the test above and fails this one: it would answer names `set_role_tools` refuses.
    """
    with admin_harness(known_tools=frozenset({TOOL_READ})) as harness:
        harness.sign_in()
        harness.grant(ROLE_SUPPORT)

        assert harness.client.get(TOOLS_PATH).json() == {"tools": [TOOL_READ]}
        refused = harness.client.put(
            f"{ROLES_PATH}/{ROLE_SUPPORT}/tools", json={"tools": [TOOL_CHANGE]}
        )

    assert refused.status_code == status.HTTP_400_BAD_REQUEST
    assert refused.json()["error_code"] == "unknown_tools"


# --- V14: one audit event per change, from the route ---


@pytest.mark.parametrize(("method", "path", "body", "event_type", "target"), MUTATIONS)
def test_every_mutating_role_route_records_one_audit_event(
    method: str, path: str, body: dict[str, Any] | None, event_type: str, target: str
) -> None:
    """The actor is the signed-in admin, resolved from the cookie and never from the body."""
    with admin_harness() as harness:
        harness.sign_in()
        harness.grant(ROLE_SUPPORT)

        response = harness.client.request(method, path, json=body)
        assert response.status_code == status.HTTP_200_OK, response.text
        events = harness.audit.events

    assert [event.event_type for event in events] == [event_type]
    assert events[0].actor_email == ADMIN_EMAIL
    assert events[0].target == target


def test_the_role_read_routes_record_nothing() -> None:
    """V14 is about changes. A trail of list calls is V45's job, on another surface."""
    with admin_harness() as harness:
        harness.sign_in()
        harness.grant(ROLE_SUPPORT, TOOL_READ)

        harness.list_roles()
        harness.role_tools(ROLE_SUPPORT)
        harness.client.get(TOOLS_PATH)

        assert harness.audit.events == []


# --- V100: the transaction boundary, from the route ---


@pytest.mark.parametrize(("method", "path", "body", "event_type", "target"), MUTATIONS)
def test_every_mutating_role_route_commits_once(
    method: str, path: str, body: dict[str, Any] | None, event_type: str, target: str
) -> None:
    """V100: `get_db_session` never commits, so a write that does not end its transaction
    answers 200 over a rollback. One commit per request, not one per statement."""
    with admin_harness() as harness:
        harness.sign_in()
        harness.grant(ROLE_SUPPORT)

        harness.client.request(method, path, json=body)

        assert harness.repository.commits == 1
        assert harness.repository.committed_roles == harness.repository.roles


def test_a_reserved_role_refusal_commits_nothing() -> None:
    """V100(a): every guard raises before the commit, so a refused write persists nothing.

    The committed snapshot is the assertion surface, not the mutable dict: a future guard
    ordered *after* the write would pass a "no rows" check on the dict alone and fail this one.
    """
    with admin_harness() as harness:
        harness.sign_in()

        response = harness.client.put(
            f"{ROLES_PATH}/{ADMIN_ROLE_NAME}/tools", json={"tools": [TOOL_READ]}
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert harness.repository.commits == 0
        assert harness.repository.committed_role_tools == {}
        assert harness.repository.role_tools == {}


def test_an_unknown_tool_refusal_leaves_the_existing_grants_alone() -> None:
    """V100(a) again, where it can actually bite: the refusal sits between the role lookup and
    the replacement, so a guard moved after `replace_role_tool_permissions` would revoke the
    role's real grants while answering 400."""
    with admin_harness() as harness:
        harness.sign_in()
        harness.grant(ROLE_SUPPORT, TOOL_READ)

        response = harness.client.put(
            f"{ROLES_PATH}/{ROLE_SUPPORT}/tools", json={"tools": [TOOL_UNKNOWN]}
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert harness.repository.commits == 0
        assert harness.repository.role_tools[ROLE_SUPPORT] == {TOOL_READ}


# --- V8 + V73: the error envelope, from a real route ---


def test_a_404_body_carries_no_internal_detail() -> None:
    """V8: `detail` names the role that is absent. Body gets `error_code`, `message`,
    `request_id` — nothing else, so a refusal cannot echo back what was submitted."""
    with admin_harness() as harness:
        harness.sign_in()

        response = harness.client.delete(f"{ROLES_PATH}/{GHOST_ROLE}")

    body = response.json()
    assert set(body) == {"error_code", "message", "request_id"}
    assert GHOST_ROLE not in response.text


def test_an_unknown_tools_body_does_not_echo_the_submitted_names() -> None:
    """V8: `UnknownToolError` carries the offenders for the log, and the body carries the code.

    The panel branches on `unknown_tools` and re-renders the operator's own selection, which it
    already holds — so nothing here needs the names, and a body that echoed request content
    would be a second echo path beside the two T64 closed.
    """
    with admin_harness() as harness:
        harness.sign_in()
        harness.grant(ROLE_SUPPORT)

        response = harness.client.put(
            f"{ROLES_PATH}/{ROLE_SUPPORT}/tools", json={"tools": [TOOL_UNKNOWN]}
        )

    assert set(response.json()) == {"error_code", "message", "request_id"}
    assert TOOL_UNKNOWN not in response.text


def test_an_error_body_and_header_share_one_request_id() -> None:
    """V73: same value in the body and in `x-request-id`, so an operator can quote either."""
    with admin_harness() as harness:
        harness.sign_in()

        response = harness.client.delete(f"{ROLES_PATH}/{GHOST_ROLE}")

    assert response.headers[REQUEST_ID_HEADER] == response.json()["request_id"]


def test_the_users_route_still_answers_beside_the_role_routes() -> None:
    """Both admin routers on one app, one `AuthorizationService`, one repository — the wiring
    every V14 assertion above depends on."""
    with admin_harness() as harness:
        harness.sign_in()

        assert harness.client.get(USERS_PATH).status_code == status.HTTP_200_OK
        assert harness.client.get(ROLES_PATH).status_code == status.HTTP_200_OK
