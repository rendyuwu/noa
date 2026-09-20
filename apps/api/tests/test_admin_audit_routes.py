"""The two admin audit routes, over the real service (the admin API's contract).

`support/admin.py`'s harness mounts this router beside the rest of `/admin` with `require_admin`,
`require_session_user`, the real `JWTService`, the real `ToolRunAuditService` and the shared error
handler — only the SQL is doubled. So a 403 here is the shipped 403, the cursor in a `nextCursor`
is the shipped token, and the body is the shipped body.

What this file owns, and what it deliberately does not:

- **owns** the HTTP surface: the admin gate on both routes, the query string → filter
  mapping, the run row's field list *on the serialized payload*, the page bound, the cursor walk,
  and the one refusal a bad id or a bad cursor produces.
- **does not own** whether a filter narrows anything. The predicates live in the SQL, so a Python
  double applying them would be a second, more forgiving judge — `test_tool_run_audit_read.py`
  reads the compiled statement and `test_admin_audit_live.py` runs it against Postgres.
- **does not own** the tool-run trail's "queryable" clause. That one needs a row written by the
  real writer, which is the live file's whole job.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest

from core.audit.errors import ToolRunAuditError
from core.audit.tool_run_reads import MAX_PAGE_SIZE
from core.db.lifecycle import ToolRisk, ToolRunStatus
from core.errors import NoaError
from noa_api.api.request_context import REQUEST_ID_HEADER
from noa_api.api.routes.admin_audit import router as admin_audit_router
from support.admin import (
    TOOL_RUNS_PATH,
    AdminHarness,
    admin_harness,
    registered_routes,
)
from support.errors import error_subclasses
from support.tool_run_audit import RUN_CREATED_AT, FakeToolRunAuditReader, build_list_item

# Every query parameter the list route accepts, with the `ToolRunAuditFilters` field it must reach.
# One table rather than seven assertions: the panel builds this exact key set
# (`apps/admin-web/src/lib/admin/audit/audit-model.ts`), so the accepted names live in one place
# here and one place there instead of drifting apart across a dozen tests.
FILTER_QUERIES: tuple[tuple[str, str, str, Any], ...] = (
    ("toolName", "whm_list_accounts", "tool_name", "whm_list_accounts"),
    ("status", "FAILED", "status", ToolRunStatus.FAILED),
    ("risk", "CHANGE", "risk", ToolRisk.CHANGE),
    ("conversationRef", "conv-77", "conversation_ref", "conv-77"),
    ("requestedByEmail", "ops@", "requested_by_email", "ops@"),
    ("from", "2026-08-01T00:00:00Z", "created_from", None),
    ("to", "2026-08-31T23:59:59Z", "created_to", None),
)

# The addresses this router registers, with the path parameter renamed so a template can be
# formatted. Compared against `router.routes` below rather than trusted: a table checked only
# against its own `len()` is pinned to itself and can never notice an address that was added and
# never listed. The comparison is `support.admin.registered_routes`, shared with the
# action-request route test.
ID_PARAM: str = "tool_run_id"

ROUTE_TABLE: tuple[tuple[str, str], ...] = (
    ("GET", TOOL_RUNS_PATH),
    ("GET", f"{TOOL_RUNS_PATH}/{{id}}"),
)


def mounted() -> set[tuple[str, str]]:
    """Every `(method, path)` this router mounts, in `ROUTE_TABLE`'s spelling."""
    return registered_routes(admin_audit_router, id_param=ID_PARAM)


@pytest.fixture
def harness() -> Iterator[AdminHarness]:
    """A signed-in admin and one recorded run."""
    with admin_harness(tool_runs=FakeToolRunAuditReader([build_list_item()])) as built:
        built.sign_in()
        yield built


# --- both routes are admin-only ---


def test_both_audit_routes_are_admin_only(harness: AdminHarness) -> None:
    """An operator holding no `admin` role reaches none of the routes this router mounts.

    `ROUTE_TABLE` is compared against `router.routes` first, so the walk is over what is
    *registered* rather than over what someone listed. That is what makes "a third audit route
    added later without `AdminUserDep` fails here rather than shipping open" true: the table used
    to be asserted against its own length, which pins it to itself and can never see a new
    address. `test_admin_action_request_routes.py` closes the same gap the same way.
    """
    assert mounted() == set(ROUTE_TABLE)

    harness.sign_in("nonadmin@example.com", roles=())

    for method, template in ROUTE_TABLE:
        path = template.format(id=uuid4())
        response = harness.client.request(method, path)
        assert response.status_code == 403, f"{method} {path} → {response.status_code}"
        assert response.json()["error_code"] == "admin_access_required"


def test_a_demoted_admin_loses_the_audit_trail_on_the_next_request(harness: AdminHarness) -> None:
    """The role is re-read per request, never taken from the cookie.

    `require_admin` is a per-handler parameter here; a router that acquired a `dependencies=[…]`
    gate instead would pass the walk above and could stop re-reading the row.
    """
    admin = harness.sign_in()
    assert harness.client.get(TOOL_RUNS_PATH).status_code == 200

    harness.auth_repository.user_roles[admin.id].discard("admin")

    response = harness.client.get(TOOL_RUNS_PATH)
    assert response.status_code == 403
    assert response.json()["error_code"] == "admin_access_required"


# --- the field list, on the wire ---


def test_the_list_item_carries_every_recorded_field(harness: AdminHarness) -> None:
    """Requester, tool name, status, conversation ref, result summary and timing.

    Asserted on the serialized body, key set *and* values: a payload test that only spot-checked
    two fields would stay green while a third silently went missing, and the run row is a list.

    `args` is deliberately absent from a list item — the detail carries it, so fifty rows do not
    ship fifty JSONB payloads to draw five columns.
    """
    body = harness.client.get(TOOL_RUNS_PATH).json()
    assert body["nextCursor"] is None
    item = body["items"][0]

    assert set(item) == {
        "toolRunId",
        "toolName",
        "risk",
        "status",
        "conversationRef",
        "requestedByEmail",
        "resultSummary",
        "createdAt",
        "completedAt",
        "durationMs",
    }
    assert item["toolName"] == "whm_list_accounts"
    assert item["risk"] == "READ"
    assert item["status"] == "COMPLETED"
    assert item["conversationRef"] == "conv-1"
    assert item["requestedByEmail"] == "operator@example.com"
    assert item["resultSummary"] == '{"ok": true}'
    assert item["createdAt"] == RUN_CREATED_AT.isoformat()
    assert item["durationMs"] == 1500


def test_risk_and_status_are_separate_fields(harness: AdminHarness) -> None:
    """A *failed READ* is representable, and the audit list is where one is seen.

    The two columns are why. Folded into one lifecycle field, `FAILED` and `READ` would compete
    for the same cell and this row could not exist.
    """
    harness.tool_runs.items = [
        build_list_item(risk=ToolRisk.READ, status=ToolRunStatus.FAILED, result_summary="timeout")
    ]

    item = harness.client.get(TOOL_RUNS_PATH).json()["items"][0]

    assert (item["risk"], item["status"]) == ("READ", "FAILED")
    assert item["resultSummary"] == "timeout"


def test_a_started_run_has_no_completion_or_duration(harness: AdminHarness) -> None:
    """Timing half two is `null` while a run is in flight, and so is the derived duration.

    A zero would read as "it finished instantly", which is the opposite of what a `STARTED` row
    means (the stranded-run reaper exists for the ones that never finish).
    """
    harness.tool_runs.items = [build_list_item(status=ToolRunStatus.STARTED, completed_ms=None)]

    item = harness.client.get(TOOL_RUNS_PATH).json()["items"][0]

    assert item["completedAt"] is None
    assert item["durationMs"] is None


def test_a_deleted_requester_leaves_the_email_null(harness: AdminHarness) -> None:
    """`SET NULL`: the run outlives its operator, and the list says so rather than hiding it.

    An inner join on `users` would drop exactly these rows — the reason `select_tool_run_page` uses
    an outer one.
    """
    harness.tool_runs.items = [build_list_item(requested_by_email=None)]

    assert harness.client.get(TOOL_RUNS_PATH).json()["items"][0]["requestedByEmail"] is None


def test_the_detail_carries_args_summary_and_timing(harness: AdminHarness) -> None:
    """The admin API's contract: the detail is the list item plus the redacted arguments.

    The keys are asserted as a superset relation rather than by hand, so the two payloads cannot
    drift: a field renamed on the list item shows up here.
    """
    run = harness.tool_runs.items[0]
    harness.tool_runs.args = {"server_ref": "web16", "password": "[REDACTED]"}

    detail = harness.client.get(f"{TOOL_RUNS_PATH}/{run.tool_run_id}").json()
    listed = harness.client.get(TOOL_RUNS_PATH).json()["items"][0]

    assert set(detail) == set(listed) | {"args", "requestedByUserId"}
    assert {key: detail[key] for key in listed} == listed
    assert detail["args"] == {"server_ref": "web16", "password": "[REDACTED]"}
    assert detail["resultSummary"] == '{"ok": true}'
    assert detail["durationMs"] == 1500


def test_a_call_with_no_arguments_answers_an_empty_object(harness: AdminHarness) -> None:
    """`{}`, never `null`: "took no arguments" and "arguments not recorded" differ."""
    run = harness.tool_runs.items[0]
    harness.tool_runs.args = {}

    assert harness.client.get(f"{TOOL_RUNS_PATH}/{run.tool_run_id}").json()["args"] == {}


# --- The query string reaches the filters ---


def test_every_query_parameter_reaches_the_filter_object(harness: AdminHarness) -> None:
    """Each accepted parameter lands on its `ToolRunAuditFilters` field, and none is dropped.

    One assertion per row of `FILTER_QUERIES` and a count check on the table: a parameter added to
    the route without a field — or a field the route stops filling — fails here. The two date
    parameters are asserted as "set", because a parsed `datetime` is FastAPI's to produce and
    re-deriving the expected instant here would be testing `fromisoformat`.
    """
    assert len(FILTER_QUERIES) == 7

    for param, value, field_name, expected in FILTER_QUERIES:
        harness.tool_runs.calls.clear()
        response = harness.client.get(TOOL_RUNS_PATH, params={param: value})

        assert response.status_code == 200, f"{param}={value} → {response.text}"
        filters = harness.tool_runs.only_call.filters
        actual = getattr(filters, field_name)
        if expected is None:
            assert actual is not None, f"{param} did not reach `{field_name}`"
        else:
            assert actual == expected, f"{param} reached `{field_name}` as {actual!r}"


def test_an_unfiltered_list_passes_no_filters(harness: AdminHarness) -> None:
    """The negative control for the walk above: with no query string, every field is `None`.

    Without this, a route that hard-coded a filter would satisfy every assertion in that test.
    """
    harness.client.get(TOOL_RUNS_PATH)

    filters = harness.tool_runs.only_call.filters
    assert [getattr(filters, name) for _, _, name, _ in FILTER_QUERIES] == [None] * 7


def test_an_unknown_status_or_risk_is_refused(harness: AdminHarness) -> None:
    """422 from the enum, not a silently ignored filter.

    A route typing these as `str` would accept `status=RUNNING` and answer the *whole* trail, which
    is a wrong answer that reads like a filtered one.
    """
    for param, value in (("status", "RUNNING"), ("risk", "WRITE")):
        response = harness.client.get(TOOL_RUNS_PATH, params={param: value})
        assert response.status_code == 422, f"{param}={value} → {response.status_code}"


# --- Paging: the bound, and the walk ---


def test_the_page_bound_is_enforced_and_reported(harness: AdminHarness) -> None:
    """`limit` is bounded at both ends, and the ceiling is the module's, not a literal here."""
    assert harness.client.get(TOOL_RUNS_PATH, params={"limit": 0}).status_code == 422
    assert (
        harness.client.get(TOOL_RUNS_PATH, params={"limit": MAX_PAGE_SIZE + 1}).status_code == 422
    )
    assert harness.client.get(TOOL_RUNS_PATH, params={"limit": MAX_PAGE_SIZE}).status_code == 200

    harness.tool_runs.calls.clear()
    harness.client.get(TOOL_RUNS_PATH, params={"limit": 7})
    assert harness.tool_runs.only_call.limit == 7


def test_a_cursor_walk_yields_every_run_once(harness: AdminHarness) -> None:
    """The pages tile the trail: five runs at `limit=2` come back as 2 + 2 + 1, no repeats.

    `nextCursor` is minted from the *last item of the page*, never from the extra row the reader
    dropped — taking it from the extra row would skip that run on the next page, which is the kind
    of gap an audit trail must not have. The final page's `null` is the bound (the row cap's
    family): a client can tell "that is all" from "there is more" without counting rows against its
    own limit.
    """
    harness.tool_runs.items = [build_list_item(tool_name=f"tool_{index}") for index in range(5)]

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(3):
        params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        body = harness.client.get(TOOL_RUNS_PATH, params=params).json()
        seen.extend(item["toolRunId"] for item in body["items"])
        cursor = body["nextCursor"]

    assert cursor is None
    assert seen == [str(item.tool_run_id) for item in harness.tool_runs.items]
    assert len(set(seen)) == 5


def test_the_last_page_carries_a_null_cursor(harness: AdminHarness) -> None:
    """One run, one page: `nextCursor` is absent, not an empty string.

    The negative control for the walk above — a route that always minted a token would page
    forever, and a client with a "next" button would follow it.
    """
    assert harness.client.get(TOOL_RUNS_PATH).json()["nextCursor"] is None


# --- one envelope, one refusal per cause ---


def test_unknown_and_malformed_ids_answer_one_body(harness: AdminHarness) -> None:
    """404 `tool_run_not_found` for both, byte-identical apart from `request_id`.

    A 422 for the malformed id would describe what the path validator accepts rather than what
    exists; `core.audit.errors` records why the split is refused. Asserted on the *body* and not
    only the status, which is a differing code leaking existence: a different answer spelled
    quietly.
    """
    unknown = harness.client.get(f"{TOOL_RUNS_PATH}/{uuid4()}")
    malformed = harness.client.get(f"{TOOL_RUNS_PATH}/not-a-uuid")

    assert unknown.status_code == malformed.status_code == 404
    assert {key: value for key, value in unknown.json().items() if key != "request_id"} == {
        key: value for key, value in malformed.json().items() if key != "request_id"
    }
    assert unknown.json()["error_code"] == "tool_run_not_found"
    assert unknown.json()["request_id"] == unknown.headers[REQUEST_ID_HEADER]


def test_a_malformed_id_never_reaches_the_reader(harness: AdminHarness) -> None:
    """The refusal happens in front of the lookup, so a junk path segment costs no query."""
    harness.client.get(f"{TOOL_RUNS_PATH}/not-a-uuid")

    assert harness.tool_runs.detail_calls == []


def test_an_undecodable_cursor_is_refused(harness: AdminHarness) -> None:
    """400 `invalid_audit_cursor` — one code for every malformed shape, and no page served.

    `noa-old` raised FastAPI's own `RequestValidationError` here, so a bad cursor answered 422 with
    a validation-error list; NOA answers the shared envelope.
    """
    for cursor in ("not-base64", "", "e30", "!!!!"):
        harness.tool_runs.calls.clear()
        response = harness.client.get(TOOL_RUNS_PATH, params={"cursor": cursor})

        if cursor == "":
            # An empty query value is "no cursor" to the route, so the page is served.
            assert response.status_code == 200
            continue

        assert response.status_code == 400, f"cursor={cursor!r} → {response.status_code}"
        assert response.json()["error_code"] == "invalid_audit_cursor"
        assert harness.tool_runs.calls == []


def test_every_audit_error_subclass_is_mapped(harness: AdminHarness) -> None:
    """No class in this family inherits `NoaError`'s 503 fallback.

    The shape every error family in this suite carries: reaching the fallback means a subclass
    arrived without anybody deciding its status, and 503 reads as "NOA is down" for what is a
    request problem.
    """
    del harness

    for klass in error_subclasses(ToolRunAuditError):
        assert "status_code" in klass.__dict__, f"{klass.__name__} has no explicit status"
        assert klass.status_code != NoaError.status_code, klass
        assert klass.status_code in {400, 404}, klass
