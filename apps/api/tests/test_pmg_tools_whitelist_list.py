"""`pmg_whitelist_list` — the whitelist that answers with a surface, not with rows.

The summary-plus-URL half of the PMG whitelist pair. `pmg_whitelist_search` answers a membership
question in the transcript; this one answers "what is on this node?" by parking its entries in
`tool_result_tables` and handing back a summary plus the address of the page that renders them.

**The second producer of that surface**, after `whm_list_accounts`, and the reason this
file exists as well as that one: the table surface is a shared capability rather than
a per-tool special case, and a claim like that is only checkable once two tools from two
different systems route through it with nothing added for either.

Same seams as its sibling next door: real resolver, real `resolve_pmg_ssh_config`, real
`SecretCipher`, real `pmgsh` command composition, real `sanitize_tool_errors`, real
`park_result_table`. Only the socket and the table *writer* are doubled — whether Postgres
accepts the insert is `test_result_tables_live.py`'s claim, not this file's.

Five properties carry the weight.

**No entry reaches the model** — summary plus URL, and the URL carries an id only.
Asserted against the serialized `ToolResult`,
with a sentinel driven through the real writer — a row that only ever existed in a fixture
proves nothing about what a tool emits.

**The bound is stated and the order is reproducible** — a capped READ ships total count and
truncation flag. `cap_rows` keeps a prefix and
never re-sorts, and `pmgsh ls` prints in whatever order PMG stores, so the sort is this tool's
debt: without it a capped page is an arbitrary subset that differs between two identical calls.

**One `pmgsh` read, argv-safe.** The exact command is asserted, and so is the fact that
it is the only one — a listing must not sync, create or delete.

**A duplicate line is listed twice.** `mynetworks` really holds both, the remove tool has to take
each, and a listing that collapsed them would describe a file PMG does not have.

**A dead address is worse than a refusal.** A table that could not be written refuses the READ;
a URL to a table that was never stored is discovered later, by an operator, in a transcript
that persists.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastmcp.tools import ToolResult
from mcp.types import EmbeddedResource, TextContent
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.tool_catalog import TOOL_CATALOG
from core.integrations.pmg.mynetworks import parse_mynetworks_entries
from core.integrations.pmg.pmgsh_cli import MYNETWORKS_PATH, PMGSH_BINARY
from core.results.errors import ResultTableUnavailableError
from noa_api.mcp_server import build_mcp_server
from noa_api.mcp_tools.pmg_read import (
    PMG_WHITELIST_TABLE_COLUMNS,
    TOOL_PMG_WHITELIST_LIST,
    pmg_whitelist_list,
)
from noa_api.mcp_tools.results import (
    ERROR_TIMEOUT,
    ERROR_TOOL_EXECUTION_FAILED,
    MESSAGE_TIMEOUT,
    MESSAGE_TOOL_EXECUTION_FAILED,
)
from noa_api.mcp_tools.table_surface import TABLE_SURFACE_PATH, UI_RESOURCE_URI_PREFIX
from support.mcp_identity import StubSession, authenticated_caller, http_request_context
from support.pmg import (
    SERVER_NAME,
    SSH_PASSWORD_PLAINTEXT,
    SSH_PRIVATE_KEY_PLAINTEXT,
    mynetworks_output,
    whitelist_context,
    whitelist_server,
)
from support.remote_exec import command_result, install_fake_ssh_exec
from support.result_tables import FakeToolResultTableWriter
from support.secrets import build_cipher
from support.servers import (
    EMBED_BASE_URL,
    RESULT_TABLE_MAX_ROWS,
    SECRETS,
    ToolFixture,
    build_tool_context,
)

READ_COMMAND = f"TERM=dumb {PMGSH_BINARY} ls {MYNETWORKS_PATH}"

# A CIDR that exists nowhere else in this suite, so finding it in a serialized tool result can
# only mean the rows travelled with it.
SENTINEL_CIDR = "203.0.113.77/32"


async def listing(
    fixture: ToolFixture,
    *,
    server_ref: str = SERVER_NAME,
    user_id: UUID | None = None,
) -> tuple[Any, UUID]:
    """Call the tool inside a real request context; return its answer and the caller's id.

    The identity is read by `park_table_result` from the authenticated request rather than passed in
    — requester-match — so the tool has to run inside the contextvar the auth middleware sets.
    """
    user, resolved = authenticated_caller(user_id)
    with http_request_context({}, user=user):
        answer = await pmg_whitelist_list(server_ref=server_ref, context=fixture.context)
    return answer, resolved


def text_block(answer: ToolResult) -> str:
    block = answer.content[0]
    assert isinstance(block, TextContent)
    return block.text


def parked_cidrs(fixture: ToolFixture) -> list[str]:
    return [str(row["cidr"]) for row in fixture.result_tables.only.rows]


# --- The happy path: a surface, not a listing ---


async def test_it_answers_with_a_summary_block_and_the_table_iframe(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Two blocks, text first — the address ships as plain text beside the frame, so it survives
    a frame that will not load."""
    fixture, _ = whitelist_context(
        monkeypatch,
        answer=command_result(stdout=mynetworks_output("10.10.10.0/24", "1.2.3.4/32")),
    )

    answer, _ = await listing(fixture)

    assert isinstance(answer, ToolResult)
    assert len(answer.content) == 2
    assert isinstance(answer.content[0], TextContent)
    resource = answer.content[1]
    assert isinstance(resource, EmbeddedResource)
    token = fixture.result_tables.only.token
    assert str(resource.resource.uri) == f"{UI_RESOURCE_URI_PREFIX}{token}"


async def test_the_address_in_the_text_is_the_parked_table_s(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The URL an operator can copy names the row that was just written — the address ships as text,
    never a link alone.
    """
    fixture, _ = whitelist_context(
        monkeypatch, answer=command_result(stdout=mynetworks_output("1.2.3.4/32"))
    )

    answer, _ = await listing(fixture)

    token = fixture.result_tables.only.token
    assert f"{EMBED_BASE_URL}{TABLE_SURFACE_PATH}/{token}" in text_block(answer)


async def test_the_summary_names_the_server_that_was_actually_read(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The operator typed an SSH host; the sentence has to name the node that answered.

    A tool that echoed the `server_ref` back would tell an operator with several PMG boxes
    nothing they did not already type.
    """
    cipher = build_cipher()
    fixture, _ = whitelist_context(
        monkeypatch,
        cipher=cipher,
        servers=[whitelist_server(SERVER_NAME, cipher=cipher, ssh_host="mail-gw.example.net")],
    )

    answer, _ = await listing(fixture, server_ref="mail-gw.example.net")

    assert SERVER_NAME in text_block(answer)


async def test_an_empty_whitelist_is_a_parked_table_with_no_rows(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Nothing has gone wrong: PMG answered and `mynetworks` holds nothing NOA can read.

    A refusal here would be a tool reporting an outage for a true answer, and the page says
    "this read matched no rows" rather than rendering headings over nothing.
    """
    fixture, _ = whitelist_context(monkeypatch, answer=command_result(stdout=mynetworks_output()))

    answer, _ = await listing(fixture)

    assert isinstance(answer, ToolResult)
    stored = fixture.result_tables.only
    assert stored.rows == []
    assert stored.total_rows == 0
    assert stored.truncated is False


async def test_both_spellings_of_an_entry_are_parked(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """PMG's own token and the normalised form, because the two differ — single hosts normalize
    to `/32` and membership matches exact, never containment.

    Only the raw form tells an operator what is in the file; only the normalised one says what
    NOA compared against. A page with one of them hides the other.
    """
    fixture, _ = whitelist_context(
        monkeypatch, answer=command_result(stdout=mynetworks_output("1.2.3.4"))
    )

    await listing(fixture)

    assert fixture.result_tables.only.rows == [{"cidr": "1.2.3.4", "normalized": "1.2.3.4/32"}]


async def test_a_repeated_entry_is_listed_twice_rather_than_collapsed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`noa-old` deduplicated while parsing. Two spellings of one address in `mynetworks` are
    two lines in the file, the remove tool has to take each, and a listing that collapsed them
    would report a whitelist its own source does not have."""
    fixture, _ = whitelist_context(
        monkeypatch, answer=command_result(stdout=mynetworks_output("1.2.3.4", "1.2.3.4/32"))
    )

    await listing(fixture)

    assert parked_cidrs(fixture) == ["1.2.3.4", "1.2.3.4/32"]
    assert fixture.result_tables.only.total_rows == 2


# --- What the model is handed: summary plus URL, id only ---


async def test_no_whitelist_entry_reaches_the_tool_result(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The whole point of summary-plus-URL, asserted on the serialized result.

    Driven through the real writer, so the sentinel is a row that genuinely reached the table
    and not one that only ever existed in this file.
    """
    fixture, _ = whitelist_context(
        monkeypatch, answer=command_result(stdout=mynetworks_output(SENTINEL_CIDR))
    )

    answer, _ = await listing(fixture)

    assert parked_cidrs(fixture) == [SENTINEL_CIDR]
    assert SENTINEL_CIDR not in answer.model_dump_json()


async def test_the_result_carries_no_credential_material(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Neither the ciphertext in the column nor the plaintext behind it reaches the result."""
    fixture, _ = whitelist_context(
        monkeypatch, answer=command_result(stdout=mynetworks_output("1.2.3.4/32"))
    )

    answer, _ = await listing(fixture)
    serialized = answer.model_dump_json()

    for secret in (*SECRETS, SSH_PASSWORD_PLAINTEXT, SSH_PRIVATE_KEY_PLAINTEXT):
        assert secret not in serialized


# --- The bound belongs to the surface, and the order belongs to the producer ---


async def test_the_entries_are_parked_in_a_reproducible_network_order(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The cap's ordering clause. `cap_rows` is a prefix, so the order it is handed is the answer.

    `pmgsh ls` prints in whatever order PMG stores, which is documented nowhere — an unsorted
    capped page would be an arbitrary subset that differs between two identical calls.

    Numeric, not lexicographic: `10.9.0.0/24` belongs before `10.10.0.0/24`, and sorting the
    strings puts it after. The IPv6 entry is here because network objects of different families
    do not compare, so a key that sorted them directly would raise on this input.
    """
    fixture, _ = whitelist_context(
        monkeypatch,
        answer=command_result(
            stdout=mynetworks_output(
                "10.10.0.0/24",
                "2001:db8::/32",
                "10.9.0.0/24",
                "10.0.0.5",
                "10.0.0.0/8",
            )
        ),
    )

    await listing(fixture)

    assert parked_cidrs(fixture) == [
        "10.0.0.0/8",
        "10.0.0.5",
        "10.9.0.0/24",
        "10.10.0.0/24",
        "2001:db8::/32",
    ]


async def test_a_capped_listing_stores_the_pre_cut_total_and_says_so(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The cap is the surface's, and it reports itself in the text and in the envelope.

    The envelope is also what the audit path reads a status and a summary off — risk and
    status are separate columns, and every READ writes a run row:
    a content-only result would file a successful listing as a FAILED run with nothing in its
    summary.

    The fixture's cap is deliberately not `Settings`' 5000, so a tool that read the production
    default could not pass this — compare what the code decides, not what the constant says.
    """
    entries = [
        f"10.{index // 256}.{index % 256}.0/24" for index in range(RESULT_TABLE_MAX_ROWS + 3)
    ]
    fixture, _ = whitelist_context(
        monkeypatch, answer=command_result(stdout=mynetworks_output(*entries))
    )

    answer, _ = await listing(fixture)

    stored = fixture.result_tables.only
    assert len(stored.rows) == RESULT_TABLE_MAX_ROWS
    assert stored.total_rows == RESULT_TABLE_MAX_ROWS + 3
    assert stored.truncated is True
    assert answer.structured_content == {
        "ok": True,
        "total_rows": RESULT_TABLE_MAX_ROWS + 3,
        "stored_rows": RESULT_TABLE_MAX_ROWS,
        "truncated": True,
    }
    assert "truncated" in text_block(answer)


async def test_an_uncapped_listing_does_not_claim_truncation(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The negative control: without it, the case above passes against a constant."""
    fixture, _ = whitelist_context(
        monkeypatch, answer=command_result(stdout=mynetworks_output("1.2.3.4/32"))
    )

    answer, _ = await listing(fixture)

    assert fixture.result_tables.only.truncated is False
    assert answer.structured_content == {
        "ok": True,
        "total_rows": 1,
        "stored_rows": 1,
        "truncated": False,
    }
    assert "truncated" not in text_block(answer)


async def test_there_is_no_limit_argument_to_hide_entries_behind() -> None:
    """A listing offloads *whole*; the surface's cap is the one an operator asked for.

    A `limit` here would be a second bound, applied before the table's own and invisible on the page
    — the fabrication the stored bound exists to stop, one surface further back. The reason rule
    rides along: a READ has no reason field either, and a schema is where one would appear.
    """
    server = build_mcp_server(tool_context=build_tool_context().context)
    tools = {tool.name: tool for tool in await server.list_tools(run_middleware=False)}

    schema = tools[TOOL_PMG_WHITELIST_LIST].parameters

    assert set(schema["properties"]) == {"server_ref"}
    assert schema["required"] == ["server_ref"]


# --- One argv-safe read, and only one ---


async def test_the_listing_sends_one_argv_safe_mynetworks_read(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`pmgsh` over SSH, argv-only, against `/config/mynetworks` and nothing else.

    Asserted as the *whole* command list, so a listing that also synced, created or deleted
    fails here — a READ tool that mutates is the one failure the READ/CHANGE split exists to
    make impossible.
    """
    fixture, fake = whitelist_context(monkeypatch)

    await listing(fixture)

    assert fake.commands == [READ_COMMAND]


async def test_a_non_root_ssh_user_escalates_end_to_end(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`sudo -n` through the tool: the row's `ssh_username` decides, and the command that goes out
    is built from the config that opened the connection."""
    fixture, fake = whitelist_context(monkeypatch, ssh_username="noa-ops")

    await listing(fixture)

    assert fake.commands == [f"TERM=dumb sudo -n {PMGSH_BINARY} ls {MYNETWORKS_PATH}"]


# --- Whose table is it ---


async def test_the_table_is_parked_for_the_caller_the_token_authenticated(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The requester is the authenticated identity, never anything off the arguments.

    That column is what the surface matches on later, so a tool that could name a requester
    would be a tool that could park a whitelist under somebody else's name.
    """
    fixture, _ = whitelist_context(monkeypatch)

    _, caller = await listing(fixture)

    assert fixture.result_tables.only.requested_by_user_id == caller


async def test_the_parked_table_is_named_after_the_tool_that_produced_it(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The page renders this, and the audit row is keyed on the same string."""
    fixture, _ = whitelist_context(monkeypatch)

    await listing(fixture)

    assert fixture.result_tables.only.tool_name == TOOL_PMG_WHITELIST_LIST


async def test_the_database_session_closes_before_the_ssh_hop(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The account search's rule, and the park's own session is opened after the hop rather than
    around it.

    A pooled Postgres connection held across a hop to someone else's host is how a slow PMG
    node becomes a database outage — and parking inside the first session would put the write
    back inside exactly that window.
    """
    events: list[str] = []
    fixture, _ = whitelist_context(monkeypatch)

    def recording_handler(_command: str):  # type: ignore[no-untyped-def]
        events.append("ssh")
        return command_result(stdout=mynetworks_output("1.2.3.4/32"))

    install_fake_ssh_exec(monkeypatch, recording_handler)

    @asynccontextmanager
    async def recording_session_factory():  # type: ignore[no-untyped-def]
        events.append("session-open")
        try:
            yield cast("AsyncSession", StubSession())
        finally:
            events.append("session-close")

    context = replace(fixture.context, session_factory=recording_session_factory)
    user, _ = authenticated_caller(uuid4())
    with http_request_context({}, user=user):
        answer = await pmg_whitelist_list(server_ref=SERVER_NAME, context=context)

    assert isinstance(answer, ToolResult)
    assert events == ["session-open", "session-close", "ssh", "session-open", "session-close"]


# --- The columns, and the entry they are read from ---


def test_every_column_names_a_field_a_parsed_entry_carries() -> None:
    """Two lists of the same fields, and they must not drift apart."""
    [entry] = parse_mynetworks_entries(mynetworks_output("1.2.3.4"))

    assert {column.key for column in PMG_WHITELIST_TABLE_COLUMNS} == set(entry.as_payload())


def test_the_raw_spelling_is_the_first_column() -> None:
    """The order is part of what was rendered, so it is stored rather than re-derived — and
    PMG's own token leads, because that is the line an operator finds on the box."""
    assert [column.key for column in PMG_WHITELIST_TABLE_COLUMNS] == ["cidr", "normalized"]


# --- Refusals ---


async def test_an_ambiguous_server_ref_returns_choices(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A tie is candidates, never a pick — a guess lists another node's whitelist."""
    cipher = build_cipher()
    shared = "gw.example.com"
    fixture, fake = whitelist_context(
        monkeypatch,
        cipher=cipher,
        servers=[
            whitelist_server("alpha", cipher=cipher, ssh_host=shared),
            whitelist_server("bravo", cipher=cipher, ssh_host=shared),
        ],
    )

    answer, _ = await listing(fixture, server_ref=shared)

    assert answer["ok"] is False
    assert answer["error_code"] == "host_ambiguous"
    assert [choice["name"] for choice in answer["choices"]] == ["alpha", "bravo"]
    # No node was picked, so nothing was contacted and nothing was parked.
    assert fake.runs == []
    assert fixture.result_tables.stored == []


async def test_an_unknown_server_ref_is_host_not_found(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fixture, _ = whitelist_context(monkeypatch)

    answer, _ = await listing(fixture, server_ref="nope")

    assert answer["ok"] is False
    assert answer["error_code"] == "host_not_found"
    assert fixture.result_tables.stored == []


async def test_a_blank_server_ref_is_refused(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Whitespace-only rejected on the one required string this tool takes."""
    fixture, fake = whitelist_context(monkeypatch)

    answer, _ = await listing(fixture, server_ref="   ")

    assert answer["ok"] is False
    assert answer["error_code"] == "host_required"
    assert fake.runs == []
    assert fixture.result_tables.stored == []


async def test_an_unpinned_server_is_refused_before_any_ssh(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """No pin, no connection — and the refusal names the *server* rather than reporting
    a failed `pmgsh` command, which would send the operator to fix the wrong thing."""
    cipher = build_cipher()
    fixture, fake = whitelist_context(
        monkeypatch,
        cipher=cipher,
        servers=[whitelist_server(SERVER_NAME, cipher=cipher, ssh_host_key_fingerprint=None)],
    )

    answer, _ = await listing(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] == "ssh_host_key_not_validated"
    assert fake.runs == []
    assert fixture.result_tables.stored == []


async def test_a_failed_pmgsh_read_keeps_pmg_s_own_code(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`pmgsh_command_failed` carries PMG's own output, and no table is parked for an answer
    nobody got."""
    fixture, _ = whitelist_context(
        monkeypatch, answer=command_result(exit_code=2, stderr="pmgsh: 501 no such option")
    )

    answer, _ = await listing(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] == "pmgsh_command_failed"
    assert fixture.result_tables.stored == []


async def test_a_failed_park_refuses_the_read_rather_than_a_dead_address(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Fail-closed — the gate's rule, one surface over — through the real error boundary.

    The refusal is visible at the moment it happens; a result carrying the address of a table
    that was never written is discovered later, by an operator, in a persisted transcript.
    """
    fixture, _ = whitelist_context(
        monkeypatch,
        answer=command_result(stdout=mynetworks_output("1.2.3.4/32")),
        result_tables=FakeToolResultTableWriter(fail_with=RuntimeError("insert failed")),
    )

    answer, _ = await listing(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] == ResultTableUnavailableError.error_code
    assert answer["message"] == ResultTableUnavailableError.message
    assert "insert failed" not in str(answer)


class ExplodingPMGServerRepository:
    """A `ServerRefRepository` that fails the way a real one can."""

    def __init__(self, error: BaseException) -> None:
        self._error = error

    async def list_servers(self) -> Any:
        raise self._error

    async def get_by_id(self, server_id: UUID) -> Any:
        raise self._error


@pytest.mark.parametrize(
    ("error", "expected_code", "expected_message"),
    [
        pytest.param(
            RuntimeError("connection to postgres lost at 10.0.0.5:5432"),
            ERROR_TOOL_EXECUTION_FAILED,
            MESSAGE_TOOL_EXECUTION_FAILED,
            id="runtime-error",
        ),
        pytest.param(
            TimeoutError("read timed out after 30s"),
            ERROR_TIMEOUT,
            MESSAGE_TIMEOUT,
            id="timeout",
        ),
    ],
)
async def test_an_exception_reaches_the_caller_as_a_named_failure(
    error: BaseException, expected_code: str, expected_message: str
) -> None:
    """Raw exceptions sanitize to a code — two mappings — and the original text never travels.

    The success path answers with content blocks, so this is also where "a failure is the same
    envelope whatever the success was" is held — `sanitize_tool_errors` widens a return type
    rather than fixing one.
    """
    context = replace(
        build_tool_context().context,
        pmg_server_repository_factory=lambda _session: ExplodingPMGServerRepository(error),
    )
    user, _ = authenticated_caller(uuid4())

    with http_request_context({}, user=user):
        answer = await pmg_whitelist_list(server_ref=SERVER_NAME, context=context)

    assert answer == {"ok": False, "error_code": expected_code, "message": expected_message}
    assert "10.0.0.5" not in str(answer)


async def test_cancellation_is_not_swallowed() -> None:
    """A cancelled request has no caller left to answer; swallowing it hangs a shutdown."""
    context = replace(
        build_tool_context().context,
        pmg_server_repository_factory=lambda _session: ExplodingPMGServerRepository(
            asyncio.CancelledError()
        ),
    )

    with pytest.raises(asyncio.CancelledError):
        await pmg_whitelist_list(server_ref=SERVER_NAME, context=context)


# --- The tool ships with its gate ---


def test_the_tool_name_matches_the_catalog() -> None:
    """The registered name is the one RBAC grants are written against — admin bypass covers known
    tools only.
    """
    assert TOOL_PMG_WHITELIST_LIST in TOOL_CATALOG
