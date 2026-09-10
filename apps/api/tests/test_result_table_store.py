"""Parking a large READ's rows: the cap, the bound it reports, and the redaction.

`core.results.tables` is where "the rows do not enter the transcript" becomes a row in
Postgres, and where "a READ that caps ships its own bound" becomes two stored numbers.
Both are asserted here against the writer double; the SQL has its own lane
(`test_result_tables_live.py`) and the tool result has another (`test_mcp_table_surface.py`).

Three claims this file exists for:

- **The total is the count before the cut.** A capped table that reported `len(rows)` would
  tell an operator a dense server has exactly as many accounts as the page happens to show —
  a fabrication authored by NOA rather than by the model.
- **The cut is a prefix.** The producer ordered the rows and only the producer knows which
  order is reproducible for its source, so re-sorting here would scramble a grouping the
  evidence is read by (the cap's ordering clause, the firewall preflight's `csf -g` deviation).
- **Rows are redacted on the way in, at any depth.** A parked table outlives the call and
  sits behind a URL in a persisted transcript, so a per-writer exemption from the redaction
  rule is exactly how one of them eventually stores a credential.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from core.results.tables import (
    TableColumn,
    cap_rows,
    mint_table_token,
    park_result_table,
)
from core.secrets.redaction import REDACTED
from support.result_tables import COLUMNS, FakeToolResultTableWriter

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

TTL_SECONDS = 1800


def rows(count: int) -> list[dict[str, object]]:
    """`count` distinguishable rows, in a deliberate order."""
    return [
        {"user": f"account-{index:03d}", "domain": f"{index}.example"} for index in range(count)
    ]


# --------------------------------------------------------------------------------------
# The cap and the bound it reports
# --------------------------------------------------------------------------------------


def test_a_capped_table_reports_the_total_and_the_flag() -> None:
    """The count before the cut travels with the rows that survived it."""
    capped = cap_rows(rows(120), max_rows=25)

    assert len(capped.rows) == 25
    assert capped.total_rows == 120
    assert capped.truncated is True


def test_an_uncapped_table_reports_truncated_false_and_the_same_number_twice() -> None:
    """The negative control.

    Without it, "a capped table says so" passes just as well against a flag pinned to `True`
    — and a surface that always claims truncation teaches an operator to ignore the word.
    """
    capped = cap_rows(rows(9), max_rows=25)

    assert len(capped.rows) == 9
    assert capped.total_rows == 9
    assert capped.truncated is False


def test_a_table_exactly_at_the_cap_is_not_truncated() -> None:
    """The boundary, pinned: nothing was dropped, so nothing may say it was.

    `>` rather than `>=` in the writer, and this is the case that separates them — the same
    off-by-one the account search proved red for `whm_search_accounts`.
    """
    capped = cap_rows(rows(25), max_rows=25)

    assert capped.total_rows == 25
    assert capped.truncated is False


def test_the_cut_keeps_the_order_it_was_given() -> None:
    """A prefix, never a re-sort.

    The rows arrive reverse-sorted on purpose. A cut that sorted first would return
    `account-000` and pass an assertion about counts alone, which is why this asserts the
    identity of the surviving rows rather than how many there are.
    """
    descending = list(reversed(rows(10)))

    capped = cap_rows(descending, max_rows=3)

    assert [row["user"] for row in capped.rows] == ["account-009", "account-008", "account-007"]


# --------------------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------------------


def test_a_credential_in_a_row_is_redacted_before_it_is_stored() -> None:
    """The parked table is not the one writer exempt from the redaction rule."""
    capped = cap_rows([{"user": "acmeco", "ssh_password": "hunter2"}], max_rows=25)

    assert capped.rows[0]["ssh_password"] == REDACTED
    assert capped.rows[0]["user"] == "acmeco"


def test_a_nested_credential_is_redacted_too() -> None:
    """A guard that reads only the top level is not a guard.

    `redact_sensitive_data` recurses through mappings and sequences, and a parked row is a
    remote system's own shape — nested by nature. Asserted rather than assumed, because the
    depth-1 version of exactly this rule shipped once already.
    """
    capped = cap_rows(
        [{"user": "acmeco", "server": {"ssh_private_key": "-----BEGIN-----"}}],
        max_rows=25,
    )

    stored = capped.rows[0]["server"]
    assert isinstance(stored, dict)
    assert stored["ssh_private_key"] == REDACTED


# --------------------------------------------------------------------------------------
# Parking: what reaches the writer, and in what order
# --------------------------------------------------------------------------------------


async def test_parking_stores_the_capped_rows_and_the_counts() -> None:
    """One call, one row: the rows the cap left, and both numbers beside them."""
    writer = FakeToolResultTableWriter()
    requester = uuid4()

    parked = await park_result_table(
        writer,
        tool_name="whm_list_accounts",
        requested_by_user_id=requester,
        columns=COLUMNS,
        rows=rows(40),
        max_rows=25,
        ttl_seconds=TTL_SECONDS,
        now=NOW,
    )

    stored = writer.only
    assert stored.tool_name == "whm_list_accounts"
    assert stored.requested_by_user_id == requester
    assert len(stored.rows) == 25
    assert stored.total_rows == 40
    assert stored.truncated is True
    assert parked.total_rows == 40
    assert parked.stored_rows == 25
    assert parked.truncated is True


async def test_the_deadline_is_the_ttl_from_the_moment_it_was_parked() -> None:
    """The lifetime is configured, and it is stamped once.

    Asserted against the moment passed in rather than against "roughly now", so a wiring that
    read a different setting — or computed the deadline from a second clock read — is red
    rather than approximately green.
    """
    writer = FakeToolResultTableWriter()

    parked = await park_result_table(
        writer,
        tool_name="pmg_whitelist_list",
        requested_by_user_id=uuid4(),
        columns=COLUMNS,
        rows=rows(2),
        max_rows=25,
        ttl_seconds=TTL_SECONDS,
        now=NOW,
    )

    assert parked.expires_at == datetime(2026, 8, 9, 12, 30, tzinfo=UTC)
    assert writer.only.expires_at == parked.expires_at


async def test_the_row_is_committed_before_the_token_is_handed_back() -> None:
    """The address is handed to a model; the row behind it has to be durable first.

    Counted separately from the store, because "wrote it" and "made it visible to the next
    connection" are two claims and only the second is what a URL in a transcript depends on.
    """
    writer = FakeToolResultTableWriter()

    await park_result_table(
        writer,
        tool_name="whm_list_accounts",
        requested_by_user_id=uuid4(),
        columns=COLUMNS,
        rows=rows(2),
        max_rows=25,
        ttl_seconds=TTL_SECONDS,
        now=NOW,
    )

    assert writer.commits == 1


async def test_each_parked_table_gets_its_own_token() -> None:
    """Two READs, two addresses. A shared token would put one operator's rows behind
    another's URL — and the token is minted here rather than taken from a caller precisely so
    a tool cannot supply one."""
    writer = FakeToolResultTableWriter()

    for _ in range(2):
        await park_result_table(
            writer,
            tool_name="whm_list_accounts",
            requested_by_user_id=uuid4(),
            columns=[TableColumn(key="user", label="Account")],
            rows=rows(1),
            max_rows=25,
            ttl_seconds=TTL_SECONDS,
            now=NOW,
        )

    first, second = writer.stored
    assert first.token != second.token


def test_a_minted_token_is_long_enough_to_be_unguessable() -> None:
    """32 random bytes, URL-safe. Defence beside the requester-match, never instead of it."""
    token = mint_table_token()

    assert len(token) >= 43
    assert token.strip("-_") != ""
