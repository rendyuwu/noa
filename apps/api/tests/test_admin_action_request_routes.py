"""The three admin action-request routes, over the real service (§I.admin-api — V13, V15, V73).

`support/admin.py`'s harness mounts this router beside the rest of `/admin` with `require_admin`,
`require_session_user`, the real `JWTService`, the real `ActionRequestAdminService` and the shared
error handler — only the SQL is doubled. So a 403 here is the shipped 403, the token in a
`nextCursor` is the shipped token, and the body is the shipped body.

What this file owns, and what it deliberately does not:

- **owns** the HTTP surface: the admin gate on every route the router mounts (V13, asserted
  against `router.routes` rather than against a hand-kept list), the query string → filter
  mapping, the payload key sets, the page bound, the cursor walk, the two 404s and the difference
  between them, which of the two reads the receipt route makes to tell them apart, and the
  relocation checklist below.
- **does not own** whether a filter narrows anything. The predicates live in the SQL, so a Python
  double applying them would be a second, more forgiving judge —
  `test_action_request_admin_read.py` reads the compiled statement and
  `test_admin_action_requests_live.py` runs it against Postgres.
- **does not own** that the reader cannot write. That is a claim about the class and about the
  statements it issues, and it is asserted in `test_action_request_admin_read.py`. What is
  asserted *here* is the other half of it: the HTTP surface offers no write to attempt.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest

from core.approvals.admin_reads import MAX_PAGE_SIZE, ActionRequestAdminFilters
from core.db.lifecycle import ActionRequestStatus
from noa_api.api.request_context import REQUEST_ID_HEADER
from noa_api.api.routes.admin_action_requests import router as action_requests_router
from support.action_request_admin import (
    GATE_CONTEXT,
    FakeActionRequestAdminReader,
    build_list_item,
    build_receipt,
)
from support.admin import (
    ACTION_REQUESTS_PATH,
    AdminHarness,
    admin_harness,
    registered_routes,
)

# Every query parameter the list route accepts, with the `ActionRequestAdminFilters` field it must
# reach. One table rather than five assertions: the panel builds this exact key set, so the
# accepted names live in one place here and one place there instead of drifting apart. The
# walk below asserts the table against `ActionRequestAdminFilters`' own fields, so a sixth filter
# added there with no query parameter carrying it is caught here rather than being unreachable.
FILTER_QUERIES: tuple[tuple[str, str, str, Any], ...] = (
    ("status", "APPROVED", "status", ActionRequestStatus.APPROVED),
    ("toolName", "whm_suspend_account", "tool_name", "whm_suspend_account"),
    ("requestedByEmail", "ops@", "requested_by_email", "ops@"),
    ("from", "2026-09-01T00:00:00Z", "created_from", None),
    ("to", "2026-09-30T23:59:59Z", "created_to", None),
)

# The addresses this router registers, with the path parameter renamed so a template can be
# formatted. Compared against `router.routes` below rather than trusted: a table pinned only
# against its own `len()` says nothing about what is mounted, and the claim the admin walk makes
# is about every route that exists, not about every route someone remembered to list. The
# comparison itself is `support.admin.registered_routes`, shared with the audit route test.
ID_PARAM: str = "action_request_id"

ROUTE_TABLE: tuple[tuple[str, str], ...] = (
    ("GET", ACTION_REQUESTS_PATH),
    ("GET", f"{ACTION_REQUESTS_PATH}/{{id}}"),
    ("GET", f"{ACTION_REQUESTS_PATH}/{{id}}/receipt"),
)

# The methods a write would arrive as. Hand-kept and bound to nothing, deliberately: this is the
# set of verbs an attacker or a mistaken client would try, not a set the code declares anywhere,
# so there is no source to read it off. Stated rather than left to read as derived.
WRITE_METHODS: tuple[str, ...] = ("POST", "PUT", "PATCH", "DELETE")


def mounted() -> set[tuple[str, str]]:
    """Every `(method, path)` this router mounts, in `ROUTE_TABLE`'s spelling."""
    return registered_routes(action_requests_router, id_param=ID_PARAM)


@pytest.fixture
def harness() -> Iterator[AdminHarness]:
    """A signed-in admin and one decided request with a receipt behind it."""
    item = build_list_item()
    reader = FakeActionRequestAdminReader(
        [item],
        receipts={
            item.action_request_id: build_receipt(
                action_request_id=item.action_request_id,
                tool_run_id=item.tool_run_id,
            )
        },
    )
    with admin_harness(action_requests=reader) as built:
        built.sign_in()
        yield built


def only_item(harness: AdminHarness) -> Any:
    """The single list item this harness was built with."""
    return harness.action_requests.items[0]


# --- V13: every route is admin-only ---


def test_every_action_request_route_is_admin_only(harness: AdminHarness) -> None:
    """V13: an operator holding no `admin` role reaches none of the routes this router mounts.

    `ROUTE_TABLE` is compared against `router.routes` first, so the walk is over what is
    *registered* rather than over what someone listed. That is the difference between a fourth
    route added later without `AdminUserDep` failing here and shipping open: a table asserted only
    against its own length pins itself to itself and can never notice a new address. This surface
    carries the operator's own justification for a change, so an unguarded route here leaks more
    than a tool name.
    """
    assert mounted() == set(ROUTE_TABLE)

    harness.sign_in("nonadmin@example.com", roles=())

    for method, template in ROUTE_TABLE:
        path = template.format(id=uuid4())
        response = harness.client.request(method, path)
        assert response.status_code == 403, f"{method} {path} → {response.status_code}"
        assert response.json()["error_code"] == "admin_access_required"


def test_a_demoted_admin_loses_the_trail_on_the_next_request(harness: AdminHarness) -> None:
    """V6 through V13: the role is re-read per request, never taken from the cookie.

    `require_admin` is a per-handler parameter here; a router that acquired a `dependencies=[…]`
    gate instead would pass the walk above and could stop re-reading the row.
    """
    admin = harness.sign_in()
    assert harness.client.get(ACTION_REQUESTS_PATH).status_code == 200

    harness.auth_repository.user_roles[admin.id].discard("admin")

    response = harness.client.get(ACTION_REQUESTS_PATH)
    assert response.status_code == 403
    assert response.json()["error_code"] == "admin_access_required"


# --- Read-only, asserted at the HTTP surface ---


def test_the_surface_offers_no_write_at_any_address(harness: AdminHarness) -> None:
    """A write attempted *through the endpoint* is refused, at every address and every verb.

    The second half of the read-only claim. `test_action_request_admin_read.py` asserts the two
    properties of the repository — no `commit`, and no statement that is not a `SELECT` — and an
    attribute check alone would pass against a repository holding a session it could commit
    through. This one attacks the surface instead: twelve requests, none of which is routed to
    anything, so there is no handler for a future edit to make writable by accident.

    405 rather than 403: the address exists and the method does not, which is a stronger statement
    than "you may not". `action_requests.status` has exactly one writer for a terminal value
    and this router must never become a second.
    """
    assert mounted() == set(ROUTE_TABLE)
    assert len(ROUTE_TABLE) * len(WRITE_METHODS) == 12

    for _, template in ROUTE_TABLE:
        path = template.format(id=only_item(harness).action_request_id)
        for method in WRITE_METHODS:
            response = harness.client.request(method, path, json={"status": "APPROVED"})
            assert response.status_code == 405, f"{method} {path} → {response.status_code}"


def test_a_write_verb_reaches_no_reader_call(harness: AdminHarness) -> None:
    """The refused write does not even reach the service — a negative control for the test above.

    Without it, a 405 asserted alone would stay green against a router that ran the handler and
    then answered 405, which is the shape a middleware-level refusal would have.
    """
    harness.action_requests.calls.clear()
    harness.action_requests.detail_calls.clear()
    harness.action_requests.exists_calls.clear()

    harness.client.post(ACTION_REQUESTS_PATH, json={})
    harness.client.delete(f"{ACTION_REQUESTS_PATH}/{only_item(harness).action_request_id}")

    assert harness.action_requests.calls == []
    assert harness.action_requests.detail_calls == []
    assert harness.action_requests.exists_calls == []


# --- The list payload ---


def test_the_list_item_carries_its_field_set_and_no_more(harness: AdminHarness) -> None:
    """The key set on the wire, asserted as a set — and `reason` is deliberately absent.

    A fifty-row page carrying fifty reasons and fifty gate contexts would ship two JSONB payloads
    per row to draw six columns, and the detail route is one click away. Asserted as absence by
    name rather than by counting keys, because a count goes red for the right thing spelled wrongly
    (V38's argument).
    """
    body = harness.client.get(ACTION_REQUESTS_PATH).json()
    assert body["nextCursor"] is None
    item = body["items"][0]

    assert set(item) == {
        "actionRequestId",
        "toolName",
        "status",
        "requestedByEmail",
        "conversationRef",
        "createdAt",
        "expiresAt",
        "decidedAt",
        "toolRunId",
        "hasReceipt",
    }
    assert "reason" not in item
    assert "approvalContext" not in item

    fixture = only_item(harness)
    assert item["actionRequestId"] == str(fixture.action_request_id)
    assert item["toolName"] == fixture.tool_name
    assert item["status"] == fixture.status.value
    assert item["requestedByEmail"] == fixture.requested_by_email
    assert item["conversationRef"] == fixture.conversation_ref
    assert item["toolRunId"] == str(fixture.tool_run_id)
    assert item["hasReceipt"] is True


def test_every_filter_reaches_the_reader(harness: AdminHarness) -> None:
    """The query string maps onto `ActionRequestAdminFilters`, one parameter at a time.

    What is asserted is the *handover*, not the narrowing: the double records the filter object it
    was given and applies none of it. Whether a predicate narrows anything is a claim about SQL and
    is asserted where the SQL is.

    The table is checked against `ActionRequestAdminFilters`' own field names as well as against
    its own length: a filter the statement builder honours and no query parameter reaches is a
    filter no admin can use, and a hand-counted table cannot tell that from a complete one. The
    count stays because it is the Python half of a cross-language pin — `audit-model.test.ts`
    asserts the same five against the key set the panel sends.
    """
    assert len(FILTER_QUERIES) == 5
    assert {field for _, _, field, _ in FILTER_QUERIES} == set(
        ActionRequestAdminFilters.__dataclass_fields__
    )

    for param, value, field_name, expected in FILTER_QUERIES:
        harness.action_requests.calls.clear()
        response = harness.client.get(ACTION_REQUESTS_PATH, params={param: value})
        assert response.status_code == 200, f"{param}={value} → {response.text}"

        got = getattr(harness.action_requests.only_call.filters, field_name)
        if expected is not None:
            assert got == expected, f"{param} landed as {got!r}"
        else:
            assert got is not None, f"{param} did not reach `{field_name}`"


def test_an_unfiltered_list_hands_down_an_empty_filter_object(harness: AdminHarness) -> None:
    """No query string means "everything" — the panel's first load.

    The control for the walk above: without it, a route that hard-coded a filter would still pass
    every parameter case while silently scoping the default answer.
    """
    harness.action_requests.calls.clear()
    harness.client.get(ACTION_REQUESTS_PATH)

    filters = harness.action_requests.only_call.filters
    assert filters.status is None
    assert filters.tool_name is None
    assert filters.requested_by_email is None
    assert filters.created_from is None
    assert filters.created_to is None


def test_the_page_bound_is_enforced_at_the_query(harness: AdminHarness) -> None:
    """422 above the ceiling and 422 below one, from the `Query` validator (V85's family)."""
    assert (
        harness.client.get(ACTION_REQUESTS_PATH, params={"limit": MAX_PAGE_SIZE}).status_code == 200
    )
    assert (
        harness.client.get(ACTION_REQUESTS_PATH, params={"limit": MAX_PAGE_SIZE + 1}).status_code
        == 422
    )
    assert harness.client.get(ACTION_REQUESTS_PATH, params={"limit": 0}).status_code == 422


def test_an_unknown_status_is_refused_rather_than_ignored(harness: AdminHarness) -> None:
    """422 for a status outside the enum.

    A filter silently dropped would answer with the whole trail while the client believed it was
    scoped — a wrong answer wearing a right one's clothes, which is the failure mode the audit
    reader's LIKE escaping exists to prevent one table over.
    """
    assert harness.client.get(ACTION_REQUESTS_PATH, params={"status": "MAYBE"}).status_code == 422


def test_the_status_enum_is_the_four_the_panel_offers(harness: AdminHarness) -> None:
    """The Python half of the status dropdown's mirror, asserted by name and by wire value.

    `audit-filters.tsx` hand-lists four options and `approvals-admin-page.test.tsx` asserts that
    list whole. Nothing in that package can read `ActionRequestStatus`, so on its own the panel
    proves only that it renders what it declares. This is the other side of the pair, the shape
    `FILTER_QUERIES` already uses against `ACTION_REQUEST_QUERY_KEYS`: each language asserts its
    own set, and the two sets are written to match.

    By name and not by count. A fifth member — or a rename — leaves the dropdown silently
    offering four, so those rows become unfilterable while every other lane stays green: the
    table still renders the unknown status, because `resolveActionRequestStatus` falls through
    honestly, and no request errors. A length assertion goes red for the right thing spelled
    wrongly and green for the wrong thing spelled right.

    Each value is also driven at the route, so an option the panel offers cannot be one the API
    refuses — a dropdown entry that 422s is a dead control, and the name check alone would not
    see it.
    """
    assert {member.name for member in ActionRequestStatus} == {
        "PENDING",
        "APPROVED",
        "DENIED",
        "EXPIRED",
    }
    assert {member.value for member in ActionRequestStatus} == {
        "PENDING",
        "APPROVED",
        "DENIED",
        "EXPIRED",
    }

    for member in ActionRequestStatus:
        response = harness.client.get(ACTION_REQUESTS_PATH, params={"status": member.value})
        assert response.status_code == 200, f"{member.value} → {response.status_code}"


def test_the_cursor_walks_pages_and_stops(harness: AdminHarness) -> None:
    """`nextCursor` continues after the last item seen, and is `null` on the last page."""
    items = [build_list_item(has_receipt=False) for _ in range(5)]
    reader = FakeActionRequestAdminReader(items)
    with admin_harness(action_requests=reader) as built:
        built.sign_in()

        seen: list[str] = []
        cursor: str | None = None
        for _ in range(5):
            params = {"limit": 2}
            if cursor:
                params["cursor"] = cursor
            body = built.client.get(ACTION_REQUESTS_PATH, params=params).json()
            seen.extend(item["actionRequestId"] for item in body["items"])
            cursor = body["nextCursor"]
            if cursor is None:
                break

        assert cursor is None
        assert seen == [str(item.action_request_id) for item in items]
        assert len(set(seen)) == 5


def test_a_malformed_cursor_is_a_four_hundred(harness: AdminHarness) -> None:
    """400 `invalid_audit_cursor` — the same codec, and the same refusal, as the audit list."""
    response = harness.client.get(ACTION_REQUESTS_PATH, params={"cursor": "not-a-cursor"})
    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_audit_cursor"


# --- The detail payload: `reason` is why this router exists ---


def test_the_detail_carries_the_operators_reason(harness: AdminHarness) -> None:
    """V15, C8: the operator's own words, readable by an administrator for the first time.

    The embed card carries no `reason` field at any status by construction and `tool_runs` has no
    column for it, so before this route the field the whole approval design turns on was writable
    and unreadable.
    """
    request_id = only_item(harness).action_request_id
    body = harness.client.get(f"{ACTION_REQUESTS_PATH}/{request_id}").json()

    assert body["reason"] == harness.action_requests.reason
    assert body["reason"]


def test_a_pending_request_reports_a_null_reason_not_an_empty_one(harness: AdminHarness) -> None:
    """`null` when nobody typed one, never `''`.

    An empty string would read as "the operator wrote nothing", which the gate refuses with a 409
    and the DB CHECK refuses against any other writer. The two spellings must not be interchangeable
    on the wire (V117's family: an empty value is a claim, absence is not).
    """
    item = build_list_item(
        status=ActionRequestStatus.PENDING, decided_after_seconds=None, has_receipt=False
    )
    reader = FakeActionRequestAdminReader([item], reason=None)
    with admin_harness(action_requests=reader) as built:
        built.sign_in()
        body = built.client.get(f"{ACTION_REQUESTS_PATH}/{item.action_request_id}").json()

    assert body["reason"] is None
    assert body["decidedAt"] is None
    assert body["status"] == "PENDING"


def test_an_unknown_or_malformed_request_id_is_one_refusal(harness: AdminHarness) -> None:
    """404 `action_request_not_found` for both, on the detail and on the receipt.

    A 422 for the malformed one would describe what the path validator accepts rather than what
    exists (T63(e), admin side).
    """
    for suffix in ("", "/receipt"):
        for identifier in (str(uuid4()), "not-a-uuid"):
            response = harness.client.get(f"{ACTION_REQUESTS_PATH}/{identifier}{suffix}")
            assert response.status_code == 404, f"{identifier}{suffix} → {response.status_code}"
            assert response.json()["error_code"] == "action_request_not_found"


# --- The receipt payload ---


def test_the_receipt_carries_both_halves_and_the_delta(harness: AdminHarness) -> None:
    """V46, V34: `before` and `after` uncollapsed, with the runner's delta beside them."""
    item = only_item(harness)
    body = harness.client.get(f"{ACTION_REQUESTS_PATH}/{item.action_request_id}/receipt").json()

    assert set(body) == {
        "actionRequestId",
        "toolRunId",
        "createdAt",
        "ok",
        "before",
        "after",
        "errorCode",
        "delta",
    }
    assert body["ok"] is True
    assert body["before"]
    assert body["after"]
    assert body["errorCode"] is None
    assert body["delta"] is None


def test_a_request_without_a_receipt_answers_its_own_code(harness: AdminHarness) -> None:
    """404 `action_receipt_not_found` — a different fact from "no such request".

    Every denied, expired and still-pending request lands here, and the panel reaches this address
    from a `hasReceipt` bit that may have gone stale. Telling an administrator the request does not
    exist would be wrong about something they can see on the list.
    """
    item = build_list_item(status=ActionRequestStatus.DENIED, has_receipt=False)
    reader = FakeActionRequestAdminReader([item])
    with admin_harness(action_requests=reader) as built:
        built.sign_in()
        response = built.client.get(f"{ACTION_REQUESTS_PATH}/{item.action_request_id}/receipt")

    assert response.status_code == 404
    assert response.json()["error_code"] == "action_receipt_not_found"


def test_the_receipt_route_checks_presence_without_loading_the_request(
    harness: AdminHarness,
) -> None:
    """The existence check is the id-only read, never the detail read.

    Separating "no such request" from "that decision started no run" needs one extra `SELECT`,
    and the cheapest one that answers it selects the id. The detail read answers it too, and
    carries `approval_context` back over two outer joins to do so — the largest JSONB on this
    path, and the one the list route drops for exactly that reason. Asserted on *which read the
    route made* rather than on a timing: the double records the two separately, so a route that
    went back to `request_detail` here fails on the empty-detail line.
    """
    item = only_item(harness)
    harness.action_requests.detail_calls.clear()
    harness.action_requests.exists_calls.clear()

    response = harness.client.get(f"{ACTION_REQUESTS_PATH}/{item.action_request_id}/receipt")

    assert response.status_code == 200
    assert harness.action_requests.exists_calls == [item.action_request_id]
    assert harness.action_requests.detail_calls == []


def test_the_two_receipt_refusals_are_not_the_same_code(harness: AdminHarness) -> None:
    """The control for the pair above: one address, two causes, two codes.

    Without this, both tests would pass against a route that answered `action_request_not_found`
    for everything — which is the collapse the separate error class exists to prevent.
    """
    absent = harness.client.get(f"{ACTION_REQUESTS_PATH}/{uuid4()}/receipt").json()["error_code"]

    item = build_list_item(status=ActionRequestStatus.EXPIRED, has_receipt=False)
    with admin_harness(action_requests=FakeActionRequestAdminReader([item])) as built:
        built.sign_in()
        no_receipt = built.client.get(
            f"{ACTION_REQUESTS_PATH}/{item.action_request_id}/receipt"
        ).json()["error_code"]

    assert absent == "action_request_not_found"
    assert no_receipt == "action_receipt_not_found"
    assert absent != no_receipt


def test_a_failed_change_keeps_its_error_code_and_its_measured_delta(harness: AdminHarness) -> None:
    """A runner failure publishes a delta; an executor refusal publishes none.

    `ok: false` alone cannot tell those apart — the presence of `delta` is the only discriminator
    — so an admin surface that dropped the field would merge two different stories into one.
    """
    item = build_list_item()
    reader = FakeActionRequestAdminReader(
        [item],
        receipts={
            item.action_request_id: build_receipt(
                action_request_id=item.action_request_id,
                ok=False,
                error_code="ssh_sudo_required",
                delta={"identity": {"server": "web-01"}, "verification": "unavailable"},
            )
        },
    )
    with admin_harness(action_requests=reader) as built:
        built.sign_in()
        body = built.client.get(f"{ACTION_REQUESTS_PATH}/{item.action_request_id}/receipt").json()

    assert body["ok"] is False
    assert body["errorCode"] == "ssh_sudo_required"
    assert body["delta"] == {"identity": {"server": "web-01"}, "verification": "unavailable"}


# --- The relocation checklist: nothing the card stops rendering becomes unreadable ---


def test_every_field_the_card_stops_rendering_is_reachable_here(harness: AdminHarness) -> None:
    """One assertion per field name: every field the card is to stop rendering is readable here.

    The operator's approval card is being narrowed to "may this run, and what will it do?", and
    the fields it stops rendering stay in the database, in the API payload and in the audit trail
    — nothing is deleted, it is relocated.

    **This is the present-by-name half only, and it is the only half that exists yet.** Nothing
    in this file reads the card, so deleting a row from the embed card leaves this green; what
    reddens is the *admin* surface ceasing to serve a field. The absent-by-name half — the card no
    longer rendering what moved — belongs to the commit that performs the removal, and V120 is
    what requires the receiving surface to ship first. Relocation rather than deletion is proven
    by the pair, so read this as the receiving end being ready, not as the move being checked.

    Asserted **by name**, never by counting keys, for the reason V38 already records: a count
    assertion goes red for the right thing spelled wrongly and green for the wrong thing spelled
    right. `librechat_user_id`, `server_id` and `api_username` live inside the gate context under
    the gate's own spelling, so they are read through the same nesting the writer used.
    """
    item = only_item(harness)
    detail = harness.client.get(f"{ACTION_REQUESTS_PATH}/{item.action_request_id}").json()
    receipt = harness.client.get(f"{ACTION_REQUESTS_PATH}/{item.action_request_id}/receipt").json()

    context = detail["approvalContext"]

    # The LibreChat account behind the call — the card's "LibreChat account" row.
    assert (
        context["requester"]["librechat_user_id"]
        == (GATE_CONTEXT["requester"]["librechat_user_id"])
    )
    # The grouping label — the card's "Conversation" row.
    assert detail["conversationRef"] == item.conversation_ref
    # The execution the approval started — the card's "Execution → Run" UUID.
    assert detail["toolRunId"] == str(item.tool_run_id)
    # Which machine acted, and as whom — the card's before-state block.
    assert context["evidence"]["server_id"] == GATE_CONTEXT["evidence"]["server_id"]
    assert context["evidence"]["api_username"] == GATE_CONTEXT["evidence"]["api_username"]
    # The nested account blob the card flattens away.
    assert context["evidence"]["account"] == GATE_CONTEXT["evidence"]["account"]
    # Both raw halves, which the card replaces with the runner's delta.
    assert receipt["before"] == GATE_CONTEXT["evidence"]
    assert receipt["after"] == {"suspended": True, "user": "acmecorp"}


def test_the_gate_context_is_served_as_stored_and_not_reprojected(harness: AdminHarness) -> None:
    """The same object T33 persisted, whole.

    Not re-redacted and not flattened into named fields: it was redacted at gate time, and a
    second policy means the day the two disagree is the day one of them is wrong. Asserted as
    equality against the stored object rather than key-by-key, because a route that dropped an
    unrecognised key would pass every named check above while quietly narrowing what an auditor
    can see.
    """
    item = only_item(harness)
    detail = harness.client.get(f"{ACTION_REQUESTS_PATH}/{item.action_request_id}").json()

    assert detail["approvalContext"] == GATE_CONTEXT


# --- V73: the shared envelope ---


def test_every_refusal_carries_the_request_id(harness: AdminHarness) -> None:
    """V73: `request_id` in the body and `x-request-id` on the response, from the shared handler."""
    response = harness.client.get(f"{ACTION_REQUESTS_PATH}/{uuid4()}")

    assert response.status_code == 404
    body = response.json()
    assert body["request_id"]
    assert response.headers[REQUEST_ID_HEADER] == body["request_id"]
