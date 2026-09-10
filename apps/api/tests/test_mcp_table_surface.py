"""What a large READ answers with, and what it never answers with.

`noa_api.mcp_tools.table_surface` is the READ-side sibling of the CHANGE gate response, and
this file makes the same four claims about it, one surface over:

- **two content blocks, text first** — the frame is where the table is read and the address in
  the text is what remains when the frame does not load. Asserted on the count and the order,
  because "a resource exists" stays true when the text block is the half that got dropped;
- **the resource is shaped the way the render gate measured** — `ui://` scheme, `text/uri-list`
  mime, URL as the body — the three fields the render gate measured live. Any one of the three wrong
  and the table either does not render or renders on an opaque origin;
- **the URL carries the token and nothing else**. Taken apart rather than prefix-matched,
  so a query parameter added later goes red instead of hiding on the end of a `startswith`;
- **the bound is stated in the text**. A model reports what it was handed, so a capped
  table that did not say so becomes "there are twenty-five accounts" on a box with hundreds.

And two this surface owes that the CHANGE gate does not.

**No rows anywhere in the result.** The whole point of summary-plus-URL is that the body never
enters the transcript, and a "sample row" would be exactly the ops data it exists to keep out of
LibreChat's MongoDB.

**A counts envelope beside the blocks**. The change gate answers with content
alone, for two reasons, and only one of them survives the trip to a READ: a CHANGE call skips
the audit middleware, so no reader is owed an envelope, while every READ is recorded and
`status_for_payload` reads a missing envelope as FAILED. The reason that does survive — an
envelope must not be a third place the URL lives — is asserted here too.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest
from fastmcp.tools import ToolResult
from mcp.types import EmbeddedResource, TextContent

from core.results.errors import ResultTableUnavailableError
from core.results.tables import ParkedTable, TableColumn
from noa_api.mcp_tools.table_surface import (
    TABLE_SURFACE_PATH,
    UI_RESOURCE_URI_PREFIX,
    build_table_result,
    park_table_result,
    table_surface_url,
)
from noa_api.mcp_tools.ui_resource import UI_RESOURCE_MIME_TYPE
from support.mcp_identity import authenticated_caller, http_request_context
from support.result_tables import COLUMNS, FakeToolResultTableWriter
from support.servers import (
    EMBED_BASE_URL,
    RESULT_TABLE_MAX_ROWS,
    RESULT_TABLE_TTL_SECONDS,
    ToolFixture,
    build_tool_context,
)

READ_TOOL = "whm_list_accounts"

# See `support.result_tables.DEFAULT_TOKEN`: a constant, because a `token` parameter with a
# literal default reads as a hardcoded credential and is not one.
DEFAULT_TOKEN = "table-token-1"

SUMMARY = "12 accounts on `alpha`."

EXPIRES_AT = datetime(2026, 8, 9, 12, 30, tzinfo=UTC)

# A credential-shaped value and an ordinary one, driven through the real writer so a test can
# hunt for both in what the model was handed.
SENTINEL_ROW: dict[str, Any] = {"user": "sentinel-account", "ssh_password": "sentinel-secret"}


def parked(
    *,
    token: str = DEFAULT_TOKEN,
    total_rows: int = 12,
    stored_rows: int = 12,
    truncated: bool = False,
) -> ParkedTable:
    """What `park_table_result` hands back, without touching a writer."""
    return ParkedTable(
        token=token,
        total_rows=total_rows,
        stored_rows=stored_rows,
        truncated=truncated,
        expires_at=EXPIRES_AT,
    )


def result(tools: ToolFixture, **overrides: Any) -> ToolResult:
    return build_table_result(
        parked(**overrides),
        tool_name=READ_TOOL,
        summary=SUMMARY,
        context=tools.context,
    )


def text_block(answer: ToolResult) -> str:
    block = answer.content[0]
    assert isinstance(block, TextContent)
    return block.text


def resource_block(answer: ToolResult) -> EmbeddedResource:
    block = answer.content[1]
    assert isinstance(block, EmbeddedResource)
    return block


async def park(
    tools: ToolFixture,
    *,
    rows: list[dict[str, Any]],
    user_id: UUID | None = None,
) -> tuple[ParkedTable, UUID]:
    """Park a table inside a real request context; return the result and the caller."""
    user, resolved = authenticated_caller(user_id)
    with http_request_context({}, user=user):
        result = await park_table_result(
            tool_name=READ_TOOL,
            columns=COLUMNS,
            rows=rows,
            context=tools.context,
        )
    return result, resolved


# --------------------------------------------------------------------------------------
# The shape of the answer (one-function-shapes-it, one surface over)
# --------------------------------------------------------------------------------------


def test_the_result_carries_a_summary_block_and_a_ui_resource() -> None:
    """Two blocks, text first.

    The count is the assertion, not "a resource is present": a result that lost its text block
    still contains a resource, and it is the text that carries the address an operator can copy
    when the frame does not render.
    """
    answer = result(build_tool_context())

    assert len(answer.content) == 2
    assert isinstance(answer.content[0], TextContent)
    assert isinstance(answer.content[1], EmbeddedResource)


def test_the_resource_uses_the_ui_scheme_and_the_uri_list_mime() -> None:
    """The three fields that make LibreChat render a frame at all.

    `ui://` is what its parser classifies on — the classifier needs the scheme; `text/uri-list`
    is what mcp-ui maps to an iframe
    `src` rather than `srcDoc`; the body is the URL because that is what a uri-list is. Same
    mechanism as the approval card, which is what summary-plus-URL means by "not a second one".
    """
    tools = build_tool_context()
    answer = result(tools)

    resource = resource_block(answer).resource
    assert str(resource.uri) == f"{UI_RESOURCE_URI_PREFIX}{DEFAULT_TOKEN}"
    assert resource.mimeType == UI_RESOURCE_MIME_TYPE
    assert getattr(resource, "text", None) == table_surface_url(
        DEFAULT_TOKEN, embed_base_url=EMBED_BASE_URL
    )


def test_the_table_url_carries_the_token_and_nothing_else() -> None:
    """The address is a name, not a key, and it names one thing — an id-only URL.

    Taken apart rather than prefix-matched. A `startswith` assertion stays green when a query
    parameter — a tool name, a row count, a server — is appended later, and everything in this
    string persists in LibreChat's MongoDB forever.
    """
    url = table_surface_url(DEFAULT_TOKEN, embed_base_url=EMBED_BASE_URL)

    parts = urlsplit(url)
    assert parts.path == f"{TABLE_SURFACE_PATH}/{DEFAULT_TOKEN}"
    assert parts.query == ""
    assert parts.fragment == ""
    assert f"{parts.scheme}://{parts.netloc}" == EMBED_BASE_URL


def test_the_url_join_survives_a_base_with_a_trailing_slash() -> None:
    """One doubled slash is the kind of thing that only shows up in front of an operator."""
    url = table_surface_url(DEFAULT_TOKEN, embed_base_url=f"{EMBED_BASE_URL}/")

    assert url == f"{EMBED_BASE_URL}{TABLE_SURFACE_PATH}/{DEFAULT_TOKEN}"
    assert "//tables" not in url


def test_the_surface_reads_the_base_off_the_context_it_was_given() -> None:
    """Two contexts, two addresses: nothing in the result builder holds its own copy."""
    one = text_block(result(build_tool_context(embed_base_url="https://one.test")))
    two = text_block(result(build_tool_context(embed_base_url="https://two.test")))

    assert "https://one.test/tables/" in one
    assert "https://two.test/tables/" in two


# --------------------------------------------------------------------------------------
# What the text says and what it does not
# --------------------------------------------------------------------------------------


def test_the_text_block_carries_the_plain_address_and_no_markup() -> None:
    """An address, not a link — the escape hatch ships the plain address.

    A `target="_blank"` clicked inside the frame opens nothing at all under one of the two
    render sites' sandboxes, silently — so the door that always works is text a human can
    copy, and markup NOA wrote would be that door spelled the withholdable way.
    """
    body = text_block(result(build_tool_context()))

    assert table_surface_url(DEFAULT_TOKEN, embed_base_url=EMBED_BASE_URL) in body
    assert "href" not in body
    assert "<a " not in body


def test_a_capped_table_says_so_in_the_text() -> None:
    """The model reports what it was handed, so the cap's bound has to be in the sentence."""
    body = text_block(result(build_tool_context(), total_rows=900, stored_rows=25, truncated=True))

    assert "900" in body
    assert "25" in body
    assert "truncated" in body


def test_an_uncapped_table_states_the_total_without_claiming_truncation() -> None:
    """The negative control. Without it, "a capped table says so" would pass against a
    sentence that always says it — and a warning that is always on is one nobody reads."""
    body = text_block(result(build_tool_context(), total_rows=12, stored_rows=12))

    assert "12" in body
    assert "truncated" not in body


def test_the_text_names_the_deadline() -> None:
    """A parked table expires; a model that did not know would tell an operator to open a
    dead address tomorrow."""
    assert EXPIRES_AT.isoformat() in text_block(result(build_tool_context()))


def test_the_result_carries_no_reason_shaped_word() -> None:
    """The model-facing safety text rule, widened to this surface: the rule is about model-facing
    text, and this is model-facing
    text. A model told the field exists is a model that can be argued into filling it."""
    body = text_block(result(build_tool_context())).lower()

    assert "reason" not in body
    assert "justif" not in body


# --------------------------------------------------------------------------------------
# The envelope beside the blocks, and what it must not carry
# --------------------------------------------------------------------------------------


def test_the_result_carries_the_counts_as_a_success_envelope() -> None:
    """`ToolRunAuditMiddleware` reads the run's status off this and nothing else.

    The CHANGE gate answers with content alone because a CHANGE call skips the audit
    middleware and no reader is owed an envelope. A READ has one, and
    `status_for_payload` reads a missing envelope as FAILED — so a content-only table result
    would put every successful large listing into the audit trail as a failure.
    """
    answer = result(build_tool_context(), total_rows=900, stored_rows=25, truncated=True)

    assert answer.structured_content == {
        "ok": True,
        "total_rows": 900,
        "stored_rows": 25,
        "truncated": True,
    }


def test_the_envelope_states_both_counts_when_nothing_was_dropped() -> None:
    """The negative control: `truncated` is a stored fact, not a shape difference.

    Without this, "a capped table says so" would pass against an envelope that always claims
    truncation, and a reader could not tell a complete table from a capped one by its keys.
    """
    answer = result(build_tool_context(), total_rows=12, stored_rows=12)

    assert answer.structured_content == {
        "ok": True,
        "total_rows": 12,
        "stored_rows": 12,
        "truncated": False,
    }


def test_the_envelope_carries_no_address_and_no_token() -> None:
    """The half of the gate's reason that does hold here.

    An envelope beside the blocks must not become a third place the URL lives: everything in a
    tool result persists in LibreChat's MongoDB, and the address is already stated once in the
    text and once in the resource.
    """
    tools = build_tool_context()
    rendered = json.dumps(result(tools).structured_content)

    assert DEFAULT_TOKEN not in rendered
    assert TABLE_SURFACE_PATH not in rendered
    assert EMBED_BASE_URL not in rendered


async def test_no_row_reaches_the_tool_result() -> None:
    """The whole point is that the body stays out of the transcript.

    Driven through the real writer and the real result builder, then hunted in the serialized
    result — the second half matters, because a sentinel that only ever existed in a fixture
    proves nothing about what a tool emits.
    """
    tools = build_tool_context()
    table, _ = await park(tools, rows=[SENTINEL_ROW])

    serialized = result(tools, token=table.token, total_rows=1, stored_rows=1).model_dump_json()

    assert "sentinel-account" not in serialized
    assert "sentinel-secret" not in serialized


# --------------------------------------------------------------------------------------
# Parking: who it is stored for, and what happens when it cannot be
# --------------------------------------------------------------------------------------


async def test_the_table_is_parked_for_the_caller_the_token_authenticated() -> None:
    """The requester is read from the identity, never passed in.

    A tool that could pass one would be a tool that could park a table under somebody else's
    name — and the column this writes is the one the surface matches on later.
    """
    tools = build_tool_context()

    _, caller = await park(tools, rows=[{"user": "acmeco"}])

    assert tools.result_tables.only.requested_by_user_id == caller


async def test_parking_uses_the_cap_and_the_ttl_off_the_context() -> None:
    """Both numbers are configured, and neither is hardcoded in the tool path.

    The fixture's cap and TTL are deliberately not `Settings`' values, so a wiring that read
    the production defaults could not pass this — the same trick the gate used for the pending TTL.
    """
    tools = build_tool_context()
    rows = [{"user": f"account-{index}"} for index in range(RESULT_TABLE_MAX_ROWS + 5)]

    table, _ = await park(tools, rows=rows)

    stored = tools.result_tables.only
    assert len(stored.rows) == RESULT_TABLE_MAX_ROWS
    assert stored.total_rows == RESULT_TABLE_MAX_ROWS + 5
    assert table.truncated is True
    assert (stored.expires_at - datetime.now(UTC)).total_seconds() == pytest.approx(
        RESULT_TABLE_TTL_SECONDS, abs=30
    )


async def test_a_failed_park_refuses_the_read_rather_than_handing_out_a_dead_address() -> None:
    """Fail-closed, one table over from the gate's rule.

    A result whose URL leads nowhere is worse than a refusal: the refusal is visible when it
    happens, and the dead link is discovered by an operator, later, in a transcript.
    """
    tools = build_tool_context(
        result_tables=FakeToolResultTableWriter(fail_with=RuntimeError("insert failed"))
    )

    with pytest.raises(ResultTableUnavailableError):
        await park(tools, rows=[{"user": "acmeco"}])


async def test_the_columns_reach_the_writer_in_order() -> None:
    """A table's column order is part of what was rendered, so it is stored, not re-derived."""
    tools = build_tool_context()
    columns = [TableColumn(key="b", label="Second"), TableColumn(key="a", label="First")]

    user, _ = authenticated_caller(uuid4())
    with http_request_context({}, user=user):
        await park_table_result(
            tool_name=READ_TOOL,
            columns=columns,
            rows=[{"a": 1, "b": 2}],
            context=tools.context,
        )

    assert [column.key for column in tools.result_tables.only.columns] == ["b", "a"]
