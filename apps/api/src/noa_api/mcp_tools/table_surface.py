"""A large READ's answer: a summary, a URL, and the rows parked out of sight (T56 — V64).

V16 splits the tool surface by risk; V64 splits the READ half by *size*. A bounded question —
`whm_search_accounts`, `pmg_whitelist_search` — answers in the transcript. A listing does
not: `whm_list_accounts` on a dense server is thousands of rows, and putting them in front of
the model costs tokens for a body no human reads there anyway. So the rows go to
`tool_result_tables` (`core.results.tables`) and the tool answers with a summary plus the
address of the page that renders them.

**Two calls, like the CHANGE gate next door.** `park_table_result` writes the row and hands
back the token and the counts; `build_table_result` turns those into the tool result. Split
for the reason T32/T33 are: the first touches the database and the second is pure, so the
shape of what a model sees is testable without one.

**Same mechanism as the approval card, not a second one** (V64, V37). The iframe fields come
from `noa_api.mcp_tools.ui_resource` — `ui://` scheme, `text/uri-list`, URL as the body — so
"how NOA renders inside LibreChat's frame" has one spelling. Only the `ui://` name and the
route differ.

**The text block states the bound** (V85). Whatever the cap dropped is said in the sentence
the model reads, because the model reports what it was handed: "there are twenty accounts" on
a box with two hundred is a fabrication the *tool* authored. The counts come back from the
write rather than from a second read of the row that was just written.

**And it is an address, not a link** (V25, V94). No markup: a `target="_blank"` inside
LibreChat's frame opens nothing at all under one of the two render sites' sandboxes, silently
(R32), so the door that always works is text a human can copy. The wording is one string for
the whole surface, the way the CHANGE gate's is.

**Nothing about the rows reaches here.** No sample, no first record, no column values — the
tool result persists in LibreChat's MongoDB (V26), and a "preview row" would be exactly the
ops data V64 exists to keep out of it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

import structlog
from fastmcp.tools import ToolResult
from mcp.types import TextContent

from core.results.errors import ResultTableUnavailableError
from core.results.tables import ParkedTable, TableColumn, park_result_table
from noa_api.mcp_request_auth import current_mcp_identity
from noa_api.mcp_tools.context import McpToolContext, build_result_table_writer
from noa_api.mcp_tools.ui_resource import build_ui_resource, embed_url

# Where the table surface is served from, under `NOA_EMBED_BASE_URL`. The route itself is
# `apps/web-embed/src/app/tables/[token]/page.tsx` — two languages, so this constant and that
# directory cannot be checked against each other by a compiler (the note `APPROVAL_CARD_PATH`
# carries, one surface over).
TABLE_SURFACE_PATH: Final = "/tables"

# The MCP resource identifier for a parked table. `ui://` is what LibreChat's parser
# classifies on (R31c); a resource without it arrives as an ordinary attachment and never
# renders. Distinct from the approval card's prefix so the two surfaces are distinguishable
# in a transcript, and so LibreChat's own resource id is not shared between them.
UI_RESOURCE_URI_PREFIX: Final = "ui://noa/table/"

# One structured event per parked table. Identifiers and counts only — never a row, never a
# column value (V8, V26).
LOG_RESULT_TABLE_PARKED: Final = "mcp_result_table_parked"

# The write failed, so the READ has no surface to point at and is refused rather than
# answered with a dead address.
LOG_RESULT_TABLE_WRITE_FAILED: Final = "mcp_result_table_write_failed"

logger = structlog.get_logger(__name__)


def table_surface_url(token: str, *, embed_base_url: str) -> str:
    """The address of one parked table (T56, V26).

    **The token and nothing else.** No tool name, no server, no row count: this string ends up
    in a tool result that persists in LibreChat's MongoDB, so everything in it is readable by
    a LibreChat administrator forever. Authorisation to read what is behind it is the
    `noa_session` cookie plus the requester-match, evaluated when the page fetches — the URL
    is a name, not a key.
    """
    return embed_url(
        embed_base_url=embed_base_url,
        path=TABLE_SURFACE_PATH,
        identifier=token,
    )


async def park_table_result(
    *,
    tool_name: str,
    columns: Sequence[TableColumn],
    rows: Sequence[Mapping[str, object]],
    context: McpToolContext,
) -> ParkedTable:
    """Write one large READ's rows where the tool result can point at them (V8, V64, V85).

    The requester is read here, not passed in: `current_mcp_identity()` is who the call
    authenticated as, and it is the same column the surface matches on later (V27). A tool
    that could pass a requester would be a tool that could park a table under somebody else's
    name — the argument the CHANGE gate makes for reading its own caller.

    Its own session, opened and closed here. The tool has none open by this point — a pooled
    connection held across a WHM or PMG round trip is how a slow remote becomes a database
    outage (T21's rule) — and the rows only exist once that round trip is done.

    Fail-closed, like the CHANGE gate's write: a `ResultTableUnavailableError` rather than a
    result whose address leads nowhere. `Exception` rather than a driver class for the same
    reason — every way this fails ends in "there is no table", and a constraint name in front
    of the model is what V8 closes.
    """
    identity = current_mcp_identity()
    try:
        async with context.session_factory() as session:
            parked = await park_result_table(
                build_result_table_writer(context, session),
                tool_name=tool_name,
                requested_by_user_id=identity.user_id,
                columns=columns,
                rows=rows,
                max_rows=context.result_table_max_rows,
                ttl_seconds=context.result_table_ttl_seconds,
            )
    except Exception as exc:
        logger.error(
            LOG_RESULT_TABLE_WRITE_FAILED,
            tool=tool_name,
            cause=type(exc).__name__,
            detail=str(exc),
        )
        raise ResultTableUnavailableError(
            f"`tool_result_tables` INSERT for `{tool_name}` failed: {type(exc).__name__}"
        ) from exc

    logger.info(
        LOG_RESULT_TABLE_PARKED,
        tool=tool_name,
        requested_by_user_id=str(identity.user_id),
        total_rows=parked.total_rows,
        stored_rows=parked.stored_rows,
        truncated=parked.truncated,
        expires_at=parked.expires_at.isoformat(),
    )
    return parked


def build_table_result(
    parked: ParkedTable,
    *,
    tool_name: str,
    summary: str,
    context: McpToolContext,
) -> ToolResult:
    """Shape a large READ's result: the summary text, then the table's iframe (T56, V64).

    Two content blocks, text **first**, exactly as the CHANGE gate ships its pair: the frame
    is where the operator reads the table, and the address in the text is what remains when
    the frame does not load (V25). Asserted on the count and the order, because "a resource
    exists" stays true when the text block is the half that got dropped.

    `summary` is the tool's own one-line answer — what it looked at and what it found — and
    this function adds what is true of every parked table: how many matched, whether the page
    is capped, where it is, and when it expires.

    No `structured_content`, for T32's measured reason: R29 measured a content-only result,
    and an envelope beside this would be a third place the URL lives.
    """
    url = table_surface_url(parked.token, embed_base_url=context.embed_base_url)
    return ToolResult(
        content=[
            TextContent(type="text", text=_table_result_text(parked, summary=summary, url=url)),
            build_ui_resource(uri=f"{UI_RESOURCE_URI_PREFIX}{parked.token}", url=url),
        ]
    )


def _table_result_text(parked: ParkedTable, *, summary: str, url: str) -> str:
    """The text block, one wording for the whole surface (V25, V85, V94).

    Four things, and the order is what a reader needs in the order they need it: what the
    READ found, where the rows are, that the address is copyable, and what the page does not
    contain. The truncation sentence is unconditional in shape — it says the stored count and
    the total either way — because a sentence that appears only when something was dropped is
    one a model learns to treat as noise.
    """
    return (
        f"{summary}\n\n"
        f"{_rows_sentence(parked)}\n\n"
        f"Open the full table on NOA: {url}\n"
        "If the table does not open here, paste that address into a browser tab.\n\n"
        f"The table expires at {parked.expires_at.isoformat()}. The rows are not in this "
        "conversation; read them on that page."
    )


def _rows_sentence(parked: ParkedTable) -> str:
    """How many matched, and how many the page holds (V85).

    The total is the count before the cap, so a capped table never reads like a complete one.
    Both numbers are stated even when they agree, so "1,240 of 1,240" and "5,000 of 12,000"
    are the same sentence with different numbers rather than two shapes a reader has to tell
    apart.
    """
    if parked.truncated:
        return (
            f"{parked.total_rows} rows matched; the table shows the first "
            f"{parked.stored_rows}. It is truncated — narrow the search to see the rest."
        )
    return f"{parked.total_rows} rows matched, and all of them are on the table."


__all__ = [
    "LOG_RESULT_TABLE_PARKED",
    "LOG_RESULT_TABLE_WRITE_FAILED",
    "TABLE_SURFACE_PATH",
    "UI_RESOURCE_URI_PREFIX",
    "build_table_result",
    "park_table_result",
    "table_surface_url",
]
