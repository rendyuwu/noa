"""The table surface's GET.

No Postgres: `support.result_tables.table_harness` swaps the reader for an in-memory double
and leaves the router, the error handler, `JWTService`, the real `AuthService` behind
`require_session_user` and the real `ResultTableService` as production code. The SQL gets its
own coverage in `test_result_tables_live.py`, which is where the `WHERE` is actually provable,
and `test_result_table_read.py` asserts the guards are *in* the statement.

Two things this file is careful about, both borrowed from the approval card's route tests:

- **Refusal bodies are compared, not just statuses.** V27 makes another operator's table
  answer the same as an absent one, and "same status" is a much weaker claim than "same body"
  — an `error_code` that differed would be an existence oracle with a 404 painted on it (B1's
  shape).
- **The requester asked about is the cookie's.** Recorded per lookup, so "the identity came
  from the session and not from the path" is asserted rather than assumed.

And one that is this surface's own: the response carries **no decision affordance**. There is
no CSRF token, no reason, no approve or deny — a parked listing has nothing to authorise, and
a token on this body would be a key with no door (§I.embed, V39's family).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import status
from httpx import Response

from support.result_tables import (
    COLUMNS,
    CREATED_AT,
    READ_TOOL,
    ROWS,
    TableHarness,
    table_harness,
)

OTHER_EMAIL = "second-operator@example.com"

TOKEN = "table-token-1"


@pytest.fixture
def harness():
    with table_harness() as built:
        built.sign_in()
        yield built


def body(response: Response) -> dict:
    return response.json()


# --------------------------------------------------------------------------------------
# What the surface says
# --------------------------------------------------------------------------------------


def test_the_table_carries_its_columns_rows_and_provenance(harness: TableHarness) -> None:
    """V64: the whole listing, on NOA's origin, for the operator whose READ produced it."""
    harness.add_table(token=TOKEN)

    response = harness.get_table(TOKEN)

    assert response.status_code == status.HTTP_200_OK
    payload = body(response)
    assert payload["token"] == TOKEN
    assert payload["tool_name"] == READ_TOOL
    assert payload["columns"] == [column.as_payload() for column in COLUMNS]
    assert payload["rows"] == ROWS
    assert payload["created_at"] == CREATED_AT.isoformat()


def test_a_capped_table_reports_the_total_and_the_flag(harness: TableHarness) -> None:
    """V85: the bound travels to the surface, so the page can render it.

    `stored_rows` and `total_rows` are both sent, and they differ here — a body that carried
    only the rows would leave the page reporting a capped table as a complete one.
    """
    harness.add_table(token=TOKEN, total_rows=900, truncated=True)

    payload = body(harness.get_table(TOKEN))

    assert payload["total_rows"] == 900
    assert payload["stored_rows"] == len(ROWS)
    assert payload["truncated"] is True


def test_an_uncapped_table_says_so(harness: TableHarness) -> None:
    """The negative control: the flag separates, rather than always reading `True`."""
    harness.add_table(token=TOKEN)

    payload = body(harness.get_table(TOKEN))

    assert payload["truncated"] is False
    assert payload["total_rows"] == payload["stored_rows"] == len(ROWS)


def test_the_body_carries_no_decision_affordance(harness: TableHarness) -> None:
    """§I.embed: read-only, no decision controls — and the body is where that starts.

    Asserted on the key set rather than on values: a `csrf` field that happened to be `None`
    today is a field a later edit fills in, and a table has nothing to authorise.
    """
    harness.add_table(token=TOKEN)

    payload = body(harness.get_table(TOKEN))

    assert set(payload) == {
        "token",
        "tool_name",
        "columns",
        "rows",
        "total_rows",
        "stored_rows",
        "truncated",
        "created_at",
        "expires_at",
    }


# --------------------------------------------------------------------------------------
# Who may read it
# --------------------------------------------------------------------------------------


def test_the_requester_asked_about_is_the_cookies(harness: TableHarness) -> None:
    """V27: the identity comes from the session, never from the request.

    A token in a URL says which row; it does not say who is asking. The lookup records both,
    so a route that passed anything else through would be red here rather than merely wrong.
    """
    harness.add_table(token=TOKEN)

    harness.get_table(TOKEN)

    assert harness.repository.lookups == [(TOKEN, harness.operator.id)]


def test_an_unknown_and_a_foreign_token_answer_one_body(harness: TableHarness) -> None:
    """V27: existence does not leak, and the bound is the *body*, not the status.

    A code that differed by cause would be a 403 spelled differently, and a status-only
    assertion could not see it. Only `request_id` may differ.
    """
    stranger = harness.add_operator(OTHER_EMAIL)
    harness.add_table_for(stranger.id, token="stranger-token")

    unknown = harness.get_table("no-such-token")
    foreign = harness.get_table("stranger-token")

    assert unknown.status_code == foreign.status_code == status.HTTP_404_NOT_FOUND
    assert body(unknown)["error_code"] == body(foreign)["error_code"] == "result_table_not_found"
    assert body(unknown)["message"] == body(foreign)["message"]
    assert body(unknown)["request_id"] != body(foreign)["request_id"]


def test_a_table_whose_requester_was_deleted_matches_nobody(harness: TableHarness) -> None:
    """The FK is `SET NULL`, so a deleted operator leaves a row behind. It answers 404.

    Fail-closed is the direction that matters: the alternative reading — NULL matches anyone —
    would make every abandoned table world-readable by the next signed-in operator.
    """
    harness.add_table_for(None, token=TOKEN)

    response = harness.get_table(TOKEN)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert body(response)["error_code"] == "result_table_not_found"


def test_a_table_past_its_deadline_answers_the_same_refusal(harness: TableHarness) -> None:
    """The lifetime is part of the same refusal, not a separate story.

    An expired table and one that never existed are indistinguishable on purpose: "it was
    there an hour ago" is not information this surface owes a caller who may not have been the
    one who parked it.
    """
    harness.add_table(token=TOKEN, expires_in_seconds=-1)

    response = harness.get_table(TOKEN)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert body(response)["error_code"] == "result_table_not_found"


def test_a_signed_out_caller_gets_401_and_no_lookup(harness: TableHarness) -> None:
    """V6, V22: the cookie is the first half of the access control.

    No lookup happens at all — the refusal is upstream of the read, so an unauthenticated
    caller cannot use response timing or a lookup counter as an oracle either.
    """
    harness.add_table(token=TOKEN)
    harness.sign_out()

    response = harness.get_table(TOKEN)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert harness.repository.lookups == []


def test_a_disabled_operator_loses_the_table_on_the_next_request(
    harness: TableHarness,
) -> None:
    """V6: the row is re-read per request, because a session JWT is not revocable.

    The same property `require_session_user` gives every other cookie-authenticated surface —
    asserted here because this one is new, not because it is different.
    """
    harness.add_table(token=TOKEN)
    harness.operator.is_active = False

    response = harness.get_table(TOKEN)

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert harness.repository.lookups == []


# --------------------------------------------------------------------------------------
# Failures
# --------------------------------------------------------------------------------------


def test_an_unexpected_read_failure_answers_the_shared_envelope(harness: TableHarness) -> None:
    """V73: every error body carries `error_code`, `message` and `request_id`.

    Not a stack trace and not Starlette's default shape — the surface an operator sees when
    the database is unhappy is the same one every other route produces.
    """
    harness.add_table(token=TOKEN)
    harness.repository.fail = RuntimeError("connection reset")

    with harness.client_that_reports_server_errors() as client:
        response = client.get(f"/tables/{TOKEN}")

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert set(body(response)) == {"error_code", "message", "request_id"}


def test_a_token_that_is_not_a_uuid_is_still_just_a_token(harness: TableHarness) -> None:
    """T63(e)'s argument, one surface over: one refusal for the whole family.

    A shape check here would answer 422 for a malformed token and 404 for an unknown one,
    which tells a caller which strings are worth guessing.
    """
    response = harness.get_table("not-a-uuid-at-all")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert body(response)["error_code"] == "result_table_not_found"
    assert harness.repository.lookups == [("not-a-uuid-at-all", harness.operator.id)]


def test_a_token_nobody_parked_reaches_the_reader_rather_than_a_router_guess() -> None:
    """The refusal is the reader's answer, not the router's 404 for an unmatched path."""
    with table_harness() as built:
        built.sign_in()
        assert built.get_table(str(uuid4())).status_code == status.HTTP_404_NOT_FOUND
        assert len(built.repository.lookups) == 1
