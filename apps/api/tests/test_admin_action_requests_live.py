"""Reading the authorisation trail against a real Postgres (§I.admin-api — V8, V13, V15, V46).

`test_admin_action_request_routes.py` drives the surface over a double and
`test_action_request_admin_read.py` reads the compiled statement. **The claim this file exists for
is that the fields become readable at all** — and that is a claim about production code agreeing
through the database, so every row here is written by the **real writers** and read by the **real
reader**. A double answering it would be the test agreeing with itself, which is what V69 keeps
warning about.

The writers are three, and they are the ones that write these rows in production:

- `core.approvals.repository.SQLActionRequestRepository.create_pending` — the gate (T33), which
  writes PENDING and never writes a reason;
- `core.approvals.decisions.SQLActionDecisionRepository.write_decision` — the one writer of a
  terminal status and of `reason` (V22, V28, T37);
- `core.audit.receipts.SQLActionReceiptRepository.create_if_missing` — the receipt (T36, T38).

Four more questions are the database's rather than the code's:

- **`reason` survives the round trip.** It is written by a decision and had no reader anywhere
  until this surface; a test that asserted it off a fixture would be asserting its own string.
- **A deleted operator's decision survives, with a NULL requester.** `SET NULL` is a state only a
  real `DELETE` produces, and the outer join is what keeps the row visible.
- **A request with no receipt is still listed.** That is every deny and every expiry, and an inner
  join to `action_receipts` would hide all of them — the outer join is only checkable against a
  database that has one row and not the other.
- **The page tiles the trail when every timestamp is identical**, which is the case a
  `created_at`-only cursor gets wrong, and gets wrong silently (V92(c)).

Skipped, never failed, when Postgres is unreachable — like every other DB-backed test here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from core.approvals.admin_reads import (
    ActionRequestAdminFilters,
    ActionRequestAdminService,
    SQLActionRequestAdminReader,
)
from core.approvals.decisions import SQLActionDecisionRepository
from core.approvals.repository import SQLActionRequestRepository
from core.audit.cursor import decode_cursor
from core.audit.receipts import SQLActionReceiptRepository
from core.audit.tool_runs import SQLToolRunRepository
from core.db.lifecycle import ActionRequestStatus, ToolRisk, ToolRunStatus
from core.db.models import User
from support.database import migrated_database, session_factory

SCRATCH_DB = "noa_admin_action_requests_test"

OPERATOR_EMAIL = "operator@example.com"
OTHER_EMAIL = "someone-else@example.com"

CHANGE_TOOL = "whm_suspend_account"
OTHER_TOOL = "proxmox_vm_nic"

# The two ends of an incident window. Different instants, and both ends asserted alone: a floor
# and a ceiling swapped answer with the complement of the window and never with an error.
WINDOW_FROM = datetime(2026, 9, 10, 0, 0, tzinfo=UTC)
WINDOW_TO = datetime(2026, 9, 20, 0, 0, tzinfo=UTC)

# The words an operator types on the card. Long and specific on purpose: `reason` is `Text` because
# no machine writes it, and a test that used "ok" would pass against a column that truncated.
REASON = (
    "Customer confirmed the abuse report on ticket OPS-4412 and asked for the account to be "
    "suspended until the compromised mailbox password is rotated."
)

# What the gate persists. `evidence` carries the two identity fields the operator's card stops
# rendering, because this surface is where they have to remain readable.
GATE_CONTEXT: dict[str, Any] = {
    "requester": {"email": OPERATOR_EMAIL, "librechat_user_id": "lc-user-77"},
    "arguments": {"user": "acmecorp", "server_ref": "web-01"},
    "evidence": {
        "server_id": "3f9d0a2e-0000-4000-8000-000000000001",
        "api_username": "noa-automation",
        "account": {"user": "acmecorp", "domain": "acme.example", "suspended": False},
    },
}

RECEIPT_DATA: dict[str, Any] = {
    "ok": True,
    "before": GATE_CONTEXT["evidence"],
    "after": {"user": "acmecorp", "suspended": True},
    "error_code": None,
    "delta": {
        "identity": {"user": "acmecorp", "domain": "acme.example"},
        "verification": "verified",
        "changed_fields": [{"field": "suspended", "old": False, "new": True}],
    },
}


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    """A scratch database at `head`. Skips when Postgres is unreachable."""
    with migrated_database(SCRATCH_DB) as url:
        yield url


@pytest.fixture
async def session(database_url: str) -> AsyncIterator[AsyncSession]:
    """One session over a freshly emptied database.

    The truncate and the engine lifecycle live in `support.database.session_factory` (V66) —
    a second copy is a second place for `MUTATED_TABLES` to go stale, and a file that quietly
    stopped emptying a table would fail somewhere else entirely. This surface needs one
    connection rather than two, so it opens a single session off that factory rather than
    holding the factory itself: nothing here races anything.
    """
    async with session_factory(database_url) as sessions, sessions() as opened:
        yield opened


@pytest.fixture
def reader(session: AsyncSession) -> ActionRequestAdminService:
    """The service the route holds, over the real SQL reader."""
    return ActionRequestAdminService(repository=SQLActionRequestAdminReader(session))


async def insert_user(session: AsyncSession, email: str) -> UUID:
    user = User(email=email, ldap_dn=f"CN={email}", display_name="Operator", is_active=True)
    session.add(user)
    await session.commit()
    return user.id


async def open_request(
    session: AsyncSession,
    *,
    requester: UUID,
    tool_name: str = CHANGE_TOOL,
    conversation_ref: str | None = "conv-1",
    approval_context: dict[str, Any] | None = None,
    created_at: datetime | None = None,
) -> UUID:
    """A PENDING row, written by the gate's own repository (T33).

    `created_at` is a server default, so a test that needs a particular instant sets it afterwards
    — the writer has no parameter for it and giving one would be a column this surface could
    disagree with the gate about.
    """
    repository = SQLActionRequestRepository(session)
    request_id = await repository.create_pending(
        tool_name=tool_name,
        requested_by_user_id=requester,
        conversation_ref=conversation_ref,
        approval_context=approval_context or GATE_CONTEXT,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    await repository.commit()

    if created_at is not None:
        await session.execute(
            sa.text("UPDATE action_requests SET created_at = :at WHERE id = :id"),
            {"at": created_at, "id": request_id},
        )
        await session.commit()

    return request_id


async def decide(
    session: AsyncSession,
    *,
    action_request_id: UUID,
    status: ActionRequestStatus,
    reason: str | None,
    tool_run_id: UUID | None = None,
) -> None:
    """A terminal status and its reason, through the one writer allowed to record them."""
    repository = SQLActionDecisionRepository(session)
    await repository.write_decision(
        action_request_id=action_request_id,
        status=status,
        reason=reason,
        decided_at=datetime.now(UTC),
        tool_run_id=tool_run_id,
    )
    await repository.commit()


async def start_run(session: AsyncSession, *, requester: UUID) -> UUID:
    """A real `tool_runs` row, so the FK on the decision points at something."""
    repository = SQLToolRunRepository(session)
    tool_run_id = await repository.start_run(
        tool_name=CHANGE_TOOL,
        requested_by_user_id=requester,
        risk=ToolRisk.CHANGE,
        conversation_ref="conv-1",
        args={"user": "acmecorp"},
    )
    await repository.commit()
    await repository.finish_run(
        tool_run_id=tool_run_id,
        status=ToolRunStatus.COMPLETED,
        result_summary='{"ok": true}',
    )
    await repository.commit()
    return tool_run_id


async def write_receipt(
    session: AsyncSession,
    *,
    action_request_id: UUID,
    tool_run_id: UUID | None,
    receipt_data: dict[str, Any] | None = None,
) -> None:
    """The receipt, through T38's own writer."""
    repository = SQLActionReceiptRepository(session)
    await repository.create_if_missing(
        action_request_id=action_request_id,
        tool_run_id=tool_run_id,
        receipt_data=receipt_data or RECEIPT_DATA,
    )
    await session.commit()


async def approved_change(session: AsyncSession) -> tuple[UUID, UUID, UUID]:
    """One whole story: operator, approved request, run and receipt. Ids in that order."""
    requester = await insert_user(session, OPERATOR_EMAIL)
    request_id = await open_request(session, requester=requester)
    tool_run_id = await start_run(session, requester=requester)
    await decide(
        session,
        action_request_id=request_id,
        status=ActionRequestStatus.APPROVED,
        reason=REASON,
        tool_run_id=tool_run_id,
    )
    await write_receipt(session, action_request_id=request_id, tool_run_id=tool_run_id)
    return requester, request_id, tool_run_id


# --- The claim: the operator's reason becomes readable ---


@pytest.mark.anyio
async def test_the_reason_a_decision_wrote_comes_back_whole(
    session: AsyncSession, reader: ActionRequestAdminService
) -> None:
    """V15, C8: written by the decision path, read by this surface, byte for byte.

    The one field the whole approval design turns on had a writer, a DB CHECK and no reader. This
    is the round trip that makes "an administrator can see why a change was authorised" a
    statement rather than a sentence.
    """
    _, request_id, _ = await approved_change(session)

    detail = await reader.request_detail(action_request_id=request_id)
    assert detail is not None
    assert detail.reason == REASON
    assert detail.item.status is ActionRequestStatus.APPROVED
    assert detail.item.decided_at is not None


@pytest.mark.anyio
async def test_a_pending_request_reads_back_with_no_reason(
    session: AsyncSession, reader: ActionRequestAdminService
) -> None:
    """The gate writes no reason, so `None` is the honest answer — never `''`.

    The control for the test above: without it, a reader that returned a constant string would
    pass the round trip and be wrong about every undecided row. `ck_action_requests_decided_reason`
    is what makes the pair meaningful — a blank reason on a decided request is unrepresentable.
    """
    requester = await insert_user(session, OPERATOR_EMAIL)
    request_id = await open_request(session, requester=requester)

    detail = await reader.request_detail(action_request_id=request_id)
    assert detail is not None
    assert detail.reason is None
    assert detail.item.status is ActionRequestStatus.PENDING
    assert detail.item.decided_at is None
    assert detail.item.tool_run_id is None


# --- The relocation checklist, end to end ---


@pytest.mark.anyio
async def test_every_field_the_card_stops_rendering_survives_the_round_trip(
    session: AsyncSession, reader: ActionRequestAdminService
) -> None:
    """One assertion per field name, over rows the real writers wrote.

    The route test asserts the same list against a fixture, which proves the serializer. This one
    proves the *storage*: the gate wrote this context, the receipt writer wrote these halves, and
    the admin reader hands both back unchanged. Asserted by name and never by counting keys, for
    the reason V38 records — a count goes red for the right thing spelled wrongly.
    """
    _, request_id, tool_run_id = await approved_change(session)

    detail = await reader.request_detail(action_request_id=request_id)
    receipt = await reader.receipt_detail(action_request_id=request_id)
    assert detail is not None
    assert receipt is not None

    evidence = detail.approval_context["evidence"]

    assert detail.approval_context["requester"]["librechat_user_id"] == "lc-user-77"
    assert detail.item.conversation_ref == "conv-1"
    assert detail.item.tool_run_id == tool_run_id
    assert evidence["server_id"] == GATE_CONTEXT["evidence"]["server_id"]
    assert evidence["api_username"] == "noa-automation"
    assert evidence["account"] == GATE_CONTEXT["evidence"]["account"]
    assert receipt.before == RECEIPT_DATA["before"]
    assert receipt.after == RECEIPT_DATA["after"]


@pytest.mark.anyio
async def test_the_gate_context_is_returned_as_stored(
    session: AsyncSession, reader: ActionRequestAdminService
) -> None:
    """Whole-object equality: nothing added, nothing dropped, nothing re-redacted.

    The named checks above would all pass against a reader that quietly discarded a key it did not
    recognise, which is the shape a re-projection would take.
    """
    _, request_id, _ = await approved_change(session)

    detail = await reader.request_detail(action_request_id=request_id)
    assert detail is not None
    assert detail.approval_context == GATE_CONTEXT


@pytest.mark.anyio
async def test_the_receipt_comes_back_with_its_delta(
    session: AsyncSession, reader: ActionRequestAdminService
) -> None:
    """V46: both halves and the runner's delta, off the row T38's writer wrote."""
    _, request_id, tool_run_id = await approved_change(session)

    receipt = await reader.receipt_detail(action_request_id=request_id)
    assert receipt is not None
    assert receipt.ok is True
    assert receipt.error_code is None
    assert receipt.delta == RECEIPT_DATA["delta"]
    assert receipt.tool_run_id == tool_run_id
    assert receipt.action_request_id == request_id


@pytest.mark.anyio
async def test_a_stored_receipt_without_a_delta_reads_back_as_none(
    session: AsyncSession, reader: ActionRequestAdminService
) -> None:
    """The absence is a fact, and it is the only discriminator between two `ok: false` stories.

    An executor refusal stores no `delta` key at all; a runner failure stores one carrying earned
    falses. A reader that defaulted the missing key to `{}` would merge them, and the merge would
    be invisible — both would render as "a delta was computed and it was empty".
    """
    requester = await insert_user(session, OPERATOR_EMAIL)
    request_id = await open_request(session, requester=requester)
    await decide(
        session,
        action_request_id=request_id,
        status=ActionRequestStatus.APPROVED,
        reason=REASON,
    )
    await write_receipt(
        session,
        action_request_id=request_id,
        tool_run_id=None,
        receipt_data={
            "ok": False,
            "before": {},
            "after": {},
            "error_code": "change_execution_failed",
        },
    )

    receipt = await reader.receipt_detail(action_request_id=request_id)
    assert receipt is not None
    assert receipt.delta is None
    assert receipt.ok is False
    assert receipt.error_code == "change_execution_failed"


# --- What only a database can settle ---


@pytest.mark.anyio
async def test_a_decision_that_outlived_its_operator_stays_visible(
    session: AsyncSession, reader: ActionRequestAdminService
) -> None:
    """`SET NULL` (T34) is a state only a real `DELETE` produces.

    An inner join to `users` would hide exactly the decisions an audit trail is kept for — the
    ones whose operator has left. The email reads as `None`, which says "the account is gone", not
    "nobody asked".
    """
    requester, request_id, _ = await approved_change(session)

    await session.execute(sa.text("DELETE FROM users WHERE id = :id"), {"id": requester})
    await session.commit()

    page = await reader.list_requests()
    assert [item.action_request_id for item in page.items] == [request_id]
    assert page.items[0].requested_by_email is None
    assert page.items[0].has_receipt is True


@pytest.mark.anyio
async def test_a_request_with_no_receipt_is_still_listed(
    session: AsyncSession, reader: ActionRequestAdminService
) -> None:
    """Every deny and every expiry has no receipt, and an inner join would hide all of them.

    The `hasReceipt` bit is what the panel uses to decide whether the receipt route has anything
    to serve, so it is asserted in both directions against two rows in one page — one with a
    receipt and one without. A single-row fixture would pass against a bit hard-coded either way.
    """
    requester = await insert_user(session, OPERATOR_EMAIL)
    denied = await open_request(
        session, requester=requester, created_at=datetime(2026, 9, 9, 9, 0, tzinfo=UTC)
    )
    await decide(
        session,
        action_request_id=denied,
        status=ActionRequestStatus.DENIED,
        reason="Requested by the wrong team; the account owner has not been contacted.",
    )

    approved = await open_request(
        session, requester=requester, created_at=datetime(2026, 9, 9, 8, 0, tzinfo=UTC)
    )
    tool_run_id = await start_run(session, requester=requester)
    await decide(
        session,
        action_request_id=approved,
        status=ActionRequestStatus.APPROVED,
        reason=REASON,
        tool_run_id=tool_run_id,
    )
    await write_receipt(session, action_request_id=approved, tool_run_id=tool_run_id)

    page = await reader.list_requests()
    by_id = {item.action_request_id: item for item in page.items}

    assert set(by_id) == {denied, approved}
    assert by_id[denied].has_receipt is False
    assert by_id[approved].has_receipt is True
    assert await reader.receipt_detail(action_request_id=denied) is None
    assert await reader.receipt_detail(action_request_id=approved) is not None


@pytest.mark.anyio
async def test_the_page_tiles_a_trail_whose_timestamps_are_identical(
    session: AsyncSession, reader: ActionRequestAdminService
) -> None:
    """V92(c): `created_at` alone is not unique, and a cursor over it fails silently.

    `<` drops the rest of a tied group and `<=` serves it forever, so the walk is asserted on both
    the count and the *set*: those are different claims, and only one of them catches a repeat.
    """
    requester = await insert_user(session, OPERATOR_EMAIL)
    same_instant = datetime(2026, 9, 9, 7, 0, tzinfo=UTC)
    opened = [
        await open_request(session, requester=requester, created_at=same_instant) for _ in range(5)
    ]

    seen: list[UUID] = []
    cursor = None
    for _ in range(6):
        page = await reader.list_requests(limit=2, cursor=cursor)
        seen.extend(item.action_request_id for item in page.items)
        if page.next_cursor is None:
            break
        cursor = decode_cursor(page.next_cursor)

    assert len(seen) == 5
    assert set(seen) == set(opened)


@pytest.mark.anyio
async def test_each_filter_narrows_against_postgres(
    session: AsyncSession, reader: ActionRequestAdminService
) -> None:
    """The predicates are the statement's; this is where they are settled.

    Includes the `ILIKE` escape, which only Postgres can judge: an unescaped `%` widens the filter
    to the whole trail while the response still reads as filtered.
    """
    operator = await insert_user(session, OPERATOR_EMAIL)
    other = await insert_user(session, OTHER_EMAIL)

    mine = await open_request(session, requester=operator)
    theirs = await open_request(session, requester=other, tool_name=OTHER_TOOL)
    await decide(
        session,
        action_request_id=theirs,
        status=ActionRequestStatus.DENIED,
        reason="Not this week.",
    )

    by_tool = await reader.list_requests(filters=ActionRequestAdminFilters(tool_name=CHANGE_TOOL))
    assert [item.action_request_id for item in by_tool.items] == [mine]

    by_status = await reader.list_requests(
        filters=ActionRequestAdminFilters(status=ActionRequestStatus.DENIED)
    )
    assert [item.action_request_id for item in by_status.items] == [theirs]

    by_email = await reader.list_requests(
        filters=ActionRequestAdminFilters(requested_by_email="someone-else@")
    )
    assert [item.action_request_id for item in by_email.items] == [theirs]

    # The escape: `%` is a literal here, so it matches no address at all. Unescaped it would
    # match every one of them, and the caller would never know.
    wildcarded = await reader.list_requests(
        filters=ActionRequestAdminFilters(requested_by_email="%")
    )
    assert wildcarded.items == []

    everything = await reader.list_requests()
    assert {item.action_request_id for item in everything.items} == {mine, theirs}


@pytest.mark.anyio
async def test_a_percent_in_an_address_is_matched_as_a_literal(
    session: AsyncSession, reader: ActionRequestAdminService
) -> None:
    """The `ESCAPE` clause and the escaped value have to name the same character.

    `escape_like` inserts the escape character and the `ILIKE` declares what it means, and the
    two live in different call sites — `core.audit.tool_run_reads` owns both halves and
    `core.approvals.admin_reads` imports them, so one constant serves both (V66). Declaring one
    character while inserting another is silent: the inserted one becomes a literal and the `%`
    beside it goes live.

    The wildcard case above cannot see that. Searching `%` against addresses that contain no
    escape character returns nothing whether the escaping worked or was merely inert, so it
    passes either way. This one puts a literal `%` in an address and asks for it: an escaper and
    a clause that agree return exactly that row, under-escaping returns every row, and a
    mismatched pair returns none of them.
    """
    literal = await insert_user(session, "ops%pct@example.com")
    plain = await insert_user(session, OPERATOR_EMAIL)

    percent_row = await open_request(session, requester=literal)
    plain_row = await open_request(session, requester=plain)

    found = await reader.list_requests(filters=ActionRequestAdminFilters(requested_by_email="%"))

    assert [item.action_request_id for item in found.items] == [percent_row]
    assert {item.action_request_id for item in (await reader.list_requests()).items} == {
        percent_row,
        plain_row,
    }


@pytest.mark.anyio
async def test_the_date_window_keeps_what_is_inside_it_and_drops_what_is_not(
    session: AsyncSession, reader: ActionRequestAdminService
) -> None:
    """`created_from` is a floor and `created_to` is a ceiling, settled by Postgres.

    The filter walk above sets every filter except these two. A compiled statement can say the
    operators are in the `WHERE`; only rows can say which side of the boundary each one keeps.
    Each end is asserted alone as well as as a pair, because the pair is symmetric under a swap:
    inverted, a window still returns rows — the complement of the ones asked for, newest first,
    with a working `nextCursor` and nothing in the response saying so. An admin narrowing the
    authorisation trail to an incident window would be reading everything outside it.
    """
    requester = await insert_user(session, OPERATOR_EMAIL)
    before = await open_request(
        session, requester=requester, created_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    )
    inside = await open_request(
        session, requester=requester, created_at=datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    )
    after = await open_request(
        session, requester=requester, created_at=datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    )

    window = await reader.list_requests(
        filters=ActionRequestAdminFilters(created_from=WINDOW_FROM, created_to=WINDOW_TO)
    )
    assert [item.action_request_id for item in window.items] == [inside]

    floor_only = await reader.list_requests(
        filters=ActionRequestAdminFilters(created_from=WINDOW_FROM)
    )
    assert {item.action_request_id for item in floor_only.items} == {inside, after}

    ceiling_only = await reader.list_requests(
        filters=ActionRequestAdminFilters(created_to=WINDOW_TO)
    )
    assert {item.action_request_id for item in ceiling_only.items} == {inside, before}


@pytest.mark.anyio
async def test_the_existence_read_answers_for_a_real_row_and_an_absent_one(
    session: AsyncSession, reader: ActionRequestAdminService
) -> None:
    """The receipt route's presence check, against the table it asks.

    Both directions, because a reader hard-coded either way would pass one of them: an id the
    gate wrote is `True`, an id nothing wrote is `False`. The route turns the `False` into
    `action_request_not_found` and only then asks for the receipt, which is how "no such request"
    stays distinguishable from "that decision started no run".
    """
    requester = await insert_user(session, OPERATOR_EMAIL)
    request_id = await open_request(session, requester=requester)

    assert await reader.request_exists(action_request_id=request_id) is True
    assert await reader.request_exists(action_request_id=uuid4()) is False
