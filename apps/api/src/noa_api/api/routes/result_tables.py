"""The large-READ table surface's read: one parked table, for its requester (T56, I.embed).

**Read-only, and there is nothing here to decide.** The approval routes next door own the
one door on an authorization; this one owns a listing that a READ already produced.
No POST, no CSRF token, no reason — a table has nothing to authorise, and the embed page
that renders it carries no decision controls either (§I.embed).

**The access control is the cookie plus the requester-match**. `SessionUserDep` is the
first half and V6's row re-read with it: a disabled operator loses the table on their next
request rather than at cookie expiry. The second half sits inside the statement
(`core.results.tables.select_table_for_requester`), so a table that is not this caller's is
never fetched — and neither is one past its lifetime, which is judged by the same `WHERE`.

**One refusal for four causes.** Unknown token, another operator's, one whose requester was
deleted, one expired: all 404 `result_table_not_found`, one body, only `request_id` differing
(V73). The token is unguessable (32 random bytes) but that is defence beside the guard, never
instead of it — the URL travels in a tool result that persists in LibreChat's MongoDB,
so it is a name, not a key.

**The bound rides in the body**. `total_rows` and `truncated` are stored on the row and
carried here, so the page can say how many matches there were rather than how many it was
given. A body that shipped rows alone would leave the surface reporting a capped table as a
complete one, which is the fabrication V85 exists to stop.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from core.results.errors import ResultTableNotFoundError
from noa_api.api.deps import ResultTableServiceDep, SessionUserDep

router = APIRouter(prefix="/tables", tags=["tables"])


class ResultTableResponse(BaseModel):
    """200 body for one parked table.

    Shaped by `ResultTableView.as_payload()` rather than re-listed field by field, the way
    `ApprovalCardResponse` is: two spellings of one payload is one that can disagree, and the
    view is where the decision about what an operator may see was made.

    `columns` and `rows` are loose types on purpose — the fields are a WHM account's or a PMG
    whitelist entry's own shape, and a model that flattened them here would have to be widened
    by every READ that ever parks a table.

    `stored_rows` beside `total_rows` because they answer different questions: how many are on
    this page, and how many matched. Both are sent even when they are equal, so a renderer
    switching on `truncated` never has to infer a total from what it can count.
    """

    token: str
    tool_name: str
    columns: list[dict[str, Any]]
    rows: list[dict[str, Any]]
    total_rows: int
    stored_rows: int
    truncated: bool
    created_at: str
    expires_at: str


@router.get("/{token}", response_model=ResultTableResponse)
async def read_table(
    token: str,
    current_user: SessionUserDep,
    tables: ResultTableServiceDep,
) -> ResultTableResponse:
    """One parked table, for the operator whose READ produced it.

    `token` is a plain `str`, not a shaped identifier: V27 owns what an absent, malformed or
    foreign token answers and answers all of them alike, so a format check here would be a
    second, more talkative judge in front of it — and a 422 for a malformed one would say that
    well-formed tokens are the ones worth guessing (T63(e)'s argument, one surface over).
    """
    table = await tables.table_for(token=token, requester_user_id=current_user.user_id)
    if table is None:
        raise ResultTableNotFoundError(
            f"no `tool_result_tables` row for requester {current_user.user_id}"
        )

    return ResultTableResponse(**table.as_payload())


__all__ = ["ResultTableResponse", "read_table", "router"]
