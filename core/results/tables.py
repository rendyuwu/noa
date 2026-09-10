"""Parking one large READ result, and reading it back for its requester.

A READ that answers with a listing does three things here: it hands its rows over, gets a
token, and puts the URL that token names into its tool result (`noa_api.mcp_tools`). Nothing
about the rows reaches the model — that is the whole of V64, and the reason the summary the
model *is* given has to carry the counts this module computes.

**Two classes, split by what they can do**, the discipline `core.approvals` established: the
writer inserts and commits, the reader has no `commit` and no statement that is not a
`SELECT`. The MCP tool path holds the first; the HTTP surface an operator opens holds the
second, and neither is reachable from the other's side.

**The read is one statement, and all three guards are in it**. Token, requester
and deadline sit in the same `WHERE`, so a table that is not the caller's — or is past its
lifetime — is never fetched into the process. A guard applied after the read is a projection
away from being no guard at all: the row would be loaded, in front of the logger and the next
edit, and every existing test would stay green (V93, measured at T42(b)).

`requested_by_user_id` is `SET NULL` (T56's migration), so a deleted operator's table matches
nobody under SQL's NULL semantics — the fail-closed direction, the same one V27 names for
approval requests.

**The cap is applied where the row is written, and it reports itself**. `cap_rows`
returns the rows that survive, the count before the cut and whether there was one; all three
are stored, so no reader has to infer a total from the rows it can see. A surface that
rendered `len(rows)` as the total would be exactly the fabrication V85 exists to stop, and
NOA rather than the model would be its author.

**Order belongs to the producer.** The cut keeps the order it was handed, and the tool that
hands it over is the one that knows which order is reproducible for its source — V85's
amended ordering clause, which T24 already had to bend for `csf -g`. What this module
guarantees is that the cut is a prefix and never a re-sort.

**Rows are redacted on the way in**. The same one-way `redact_sensitive_data` the
audit path uses, at any depth (B7's lesson: a guard that reads only the top level is not a
guard). A parked table outlives the call, sits behind a URL in a persisted transcript,
and is read by a browser — three reasons it must not be the one writer exempt from the rule.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.clock import as_utc, now_utc
from core.db.models import ToolResultTable
from core.secrets.redaction import redact_sensitive_data

# 32 bytes, URL-safe: 43 characters, and unguessable by any margin that matters. The token is
# not what authorises the read — the cookie and the requester-match are — so this
# is defence beside the guard rather than instead of it. A shorter token would be a URL an
# operator could mistype into somebody else's table only if the match were dropped, and that
# is exactly the sort of "only if" a second layer exists to survive.
TABLE_TOKEN_BYTES: int = 32


def mint_table_token() -> str:
    """A fresh token for one parked table."""
    return secrets.token_urlsafe(TABLE_TOKEN_BYTES)


@dataclass(frozen=True)
class TableColumn:
    """One column of a parked table: the row key, and what to print above it.

    Two fields rather than a bare key list because the surface renders a heading and the tool
    knows what its own fields are called. A page that title-cased the key would be inventing
    the operator-facing name of somebody else's API field.
    """

    key: str
    label: str

    def as_payload(self) -> dict[str, str]:
        """JSON-native fields, for the column list on the row and in the HTTP body."""
        return {"key": self.key, "label": self.label}


@dataclass(frozen=True)
class CappedRows:
    """The rows a table will hold, and what the cap hid.

    `total_rows` is the count *before* the cut. It equals `len(rows)` exactly when
    `truncated` is false, which is what makes the flag checkable against the numbers instead
    of being a claim of its own.
    """

    rows: list[dict[str, Any]]
    total_rows: int
    truncated: bool


@dataclass(frozen=True)
class ParkedTable:
    """What the tool needs after parking: the address's token, and the bound to state.

    Deliberately not the rows. The whole point of V64 is that the tool's answer does not
    carry them, and a value object that could would be one a future edit could serialise into
    a tool result by accident.
    """

    token: str
    total_rows: int
    stored_rows: int
    truncated: bool
    expires_at: datetime


@dataclass(frozen=True)
class ResultTableView:
    """One parked table as the operator who produced it may read it.

    No requester identity and no id: the caller is the requester by construction — the read
    matched on them — so echoing it back would be telling somebody their own name. The token
    is carried because the page renders under it and a retry needs it.

    `total_rows` and `truncated` ride beside the rows all the way to the browser: the bound
    has to be rendered, not merely stored, or a capped table reads on screen like a complete
    one (V85, and V92's "a limit gets a voice" one surface over).
    """

    token: str
    tool_name: str
    columns: list[TableColumn]
    rows: list[dict[str, Any]]
    total_rows: int
    truncated: bool
    created_at: datetime
    expires_at: datetime

    @property
    def stored_rows(self) -> int:
        """How many rows this table actually holds — never presented as the total."""
        return len(self.rows)

    def as_payload(self) -> dict[str, Any]:
        """JSON-native fields for the HTTP body.

        `total_rows` and `truncated` are always present, including when nothing was dropped:
        a renderer that had to infer "not truncated" from a missing key is a renderer that
        reports a complete table when the field was simply forgotten.
        """
        return {
            "token": self.token,
            "tool_name": self.tool_name,
            "columns": [column.as_payload() for column in self.columns],
            "rows": [dict(row) for row in self.rows],
            "total_rows": self.total_rows,
            "stored_rows": self.stored_rows,
            "truncated": self.truncated,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }


def cap_rows(rows: Sequence[Mapping[str, Any]], *, max_rows: int) -> CappedRows:
    """The first `max_rows` rows, the count before the cut, and whether one happened.

    A prefix, never a re-sort: the caller ordered these, and only the caller knows which
    order is reproducible for its source (V85's amended ordering clause). Re-sorting here
    would scramble a grouping the evidence is read by, which is the deviation T24 had to make
    for `csf -g`.

    Redaction runs over the rows that survive. One-way, recursive, and the same function
    the audit path uses — a per-writer exemption from one redaction rule is how one of them
    eventually writes a credential.
    """
    total_rows = len(rows)
    kept = rows[:max_rows]
    redacted = [redact_sensitive_data(dict(row)) for row in kept]
    return CappedRows(
        rows=[row if isinstance(row, dict) else {} for row in redacted],
        total_rows=total_rows,
        truncated=total_rows > len(kept),
    )


class ToolResultTableWriter(Protocol):
    """The one write this path may make: park a table."""

    async def store(
        self,
        *,
        token: str,
        tool_name: str,
        requested_by_user_id: UUID,
        columns: Sequence[TableColumn],
        rows: Sequence[Mapping[str, Any]],
        total_rows: int,
        truncated: bool,
        expires_at: datetime,
    ) -> None: ...

    async def commit(self) -> None: ...


class SQLToolResultTableWriter:
    """`ToolResultTableWriter` over one `AsyncSession`.

    Writes `tool_result_tables` and nothing else — no status anywhere in this design belongs
    to it, and there is no read method here for the same reason `core.approvals.repository`
    has none: the surface that reads a table back sits behind a cookie, on the other side of
    the boundary an MCP token cannot cross.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def store(
        self,
        *,
        token: str,
        tool_name: str,
        requested_by_user_id: UUID,
        columns: Sequence[TableColumn],
        rows: Sequence[Mapping[str, Any]],
        total_rows: int,
        truncated: bool,
        expires_at: datetime,
    ) -> None:
        """Insert one parked table. The caller commits.

        Plain `dict`s and lists into JSONB: a `Mapping` subclass or a `datetime` handed in
        here would round-trip into something a comparison against the original would not
        match — `build_approval_context`'s rule, one table over.
        """
        self._session.add(
            ToolResultTable(
                token=token,
                tool_name=tool_name,
                requested_by_user_id=requested_by_user_id,
                column_labels=[column.as_payload() for column in columns],
                rows=[dict(row) for row in rows],
                total_rows=total_rows,
                truncated=truncated,
                expires_at=expires_at,
            )
        )

    async def commit(self) -> None:
        """Make the parked table durable before its URL is handed to anyone."""
        await self._session.commit()


async def park_result_table(
    writer: ToolResultTableWriter,
    *,
    tool_name: str,
    requested_by_user_id: UUID,
    columns: Sequence[TableColumn],
    rows: Sequence[Mapping[str, Any]],
    max_rows: int,
    ttl_seconds: int,
    now: datetime | None = None,
    token: str | None = None,
) -> ParkedTable:
    """Cap, redact, store and commit one large READ result.

    One function so the four steps cannot be done in three places in three orders. The token
    is minted here rather than taken from the caller — `token` exists for tests that need a
    known one — because a caller-supplied token is a caller-supplied identifier for somebody
    else's row.

    Returns the counts as well as the token: the tool's text has to state them, and
    re-reading the row it just wrote to find out would be a second answer to one question.
    """
    capped = cap_rows(rows, max_rows=max_rows)
    minted = token or mint_table_token()
    expires_at = now_utc(now) + timedelta(seconds=ttl_seconds)

    await writer.store(
        token=minted,
        tool_name=tool_name,
        requested_by_user_id=requested_by_user_id,
        columns=columns,
        rows=capped.rows,
        total_rows=capped.total_rows,
        truncated=capped.truncated,
        expires_at=expires_at,
    )
    await writer.commit()

    return ParkedTable(
        token=minted,
        total_rows=capped.total_rows,
        stored_rows=len(capped.rows),
        truncated=capped.truncated,
        expires_at=expires_at,
    )


def select_table_for_requester(*, token: str, requester_user_id: UUID, now: datetime) -> Any:
    """The statement the read path issues, and the whole of its access control.

    Three predicates, one `WHERE`: the token names the row, the requester decides whether it
    may be seen, and the deadline decides whether it still exists. Built as a function rather
    than inline so a test can compile it and assert the guards are *in the statement* — a
    check the caller makes afterwards leaves the row loaded, and every payload test stays
    green while it does.
    """
    return select(ToolResultTable).where(
        ToolResultTable.token == token,
        # The access control. A NULL requester — the FK is `SET NULL` — matches nobody.
        ToolResultTable.requested_by_user_id == requester_user_id,
        # The lifetime, judged in the same statement so an expired table is never fetched and
        # answers exactly as an absent one does.
        ToolResultTable.expires_at > now,
    )


class ToolResultTableReader(Protocol):
    """The one read this path may make."""

    async def get_for_requester(
        self,
        *,
        token: str,
        requester_user_id: UUID,
        now: datetime,
    ) -> ResultTableView | None: ...


class SQLToolResultTableReader:
    """`ToolResultTableReader` over one `AsyncSession`.

    No `commit`, and nothing to commit: every statement here is a `SELECT`. A read past the
    deadline writes nothing either — unlike an approval, where a stale PENDING has to become
    terminal because V23 answers "may this run?" from that column. Nothing reads a
    parked table's status, so there is none to correct: the row simply stops matching.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_requester(
        self,
        *,
        token: str,
        requester_user_id: UUID,
        now: datetime,
    ) -> ResultTableView | None:
        """The caller's table, or `None`.

        `None` covers "no such token" *and* "not yours" *and* "its requester was deleted"
        *and* "past its deadline", which is the point — the caller cannot tell them apart,
        and the surface has one refusal for all four.
        """
        result = await self._session.execute(
            select_table_for_requester(
                token=token,
                requester_user_id=requester_user_id,
                now=now,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None

        return ResultTableView(
            token=row.token,
            tool_name=row.tool_name,
            columns=_columns_from_payload(row.column_labels),
            rows=_rows_from_payload(row.rows),
            total_rows=row.total_rows,
            truncated=row.truncated,
            created_at=as_utc(row.created_at),
            expires_at=as_utc(row.expires_at),
        )


class ResultTableService:
    """Read one parked table for the operator who produced it.

    Thin on purpose. Its whole job is to read one clock so the deadline the statement judges
    against is a single moment rather than one the repository picks for itself — the rule
    `core.clock` exists for — and to keep the surface's dependency a *reader* rather than the
    writer next door.
    """

    def __init__(self, *, repository: ToolResultTableReader) -> None:
        self._repository = repository

    async def table_for(
        self,
        *,
        token: str,
        requester_user_id: UUID,
        now: datetime | None = None,
    ) -> ResultTableView | None:
        """The caller's table if it exists, is theirs and is still live; else `None`."""
        return await self._repository.get_for_requester(
            token=token,
            requester_user_id=requester_user_id,
            now=now_utc(now),
        )


def _columns_from_payload(value: Any) -> list[TableColumn]:
    """The stored column list, defensively (V38's family).

    `column_labels` is unversioned JSONB, so an entry that is not an object, or is missing
    its key, is skipped rather than raised on: a `KeyError` reaching the surface is a blank
    frame, and a blank frame says less than a table with one column missing. `label` falls
    back to the key, which is at worst ugly and at best exactly right.
    """
    if not isinstance(value, list):
        return []

    columns: list[TableColumn] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        if not isinstance(key, str) or not key:
            continue
        label = entry.get("label")
        columns.append(TableColumn(key=key, label=label if isinstance(label, str) else key))
    return columns


def _rows_from_payload(value: Any) -> list[dict[str, Any]]:
    """The stored rows, defensively — the rule `_columns_from_payload` states, one field over."""
    if not isinstance(value, list):
        return []
    return [dict(row) for row in value if isinstance(row, dict)]


__all__ = [
    "TABLE_TOKEN_BYTES",
    "CappedRows",
    "ParkedTable",
    "ResultTableService",
    "ResultTableView",
    "SQLToolResultTableReader",
    "SQLToolResultTableWriter",
    "TableColumn",
    "ToolResultTableReader",
    "ToolResultTableWriter",
    "cap_rows",
    "mint_table_token",
    "park_result_table",
    "select_table_for_requester",
]
