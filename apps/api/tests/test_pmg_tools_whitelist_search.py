"""`pmg_whitelist_search` — the discovery step in front of every PMG whitelist change.

Real resolver, real `resolve_pmg_ssh_config`, real `SecretCipher`, real `pmgsh` command
composition, real `sanitize_tool_errors`. Only the socket is doubled (`support.pmg`), because
everything the tool is answerable for happens on this side of it: which server was meant, which
credentials were sent, what command went out, and what the output means.

Five properties carry the weight.

**Membership is exact**. `1.2.3.4` and `1.2.3.4/32` are one entry; `1.2.3.0/24` in the
whitelist is *not* a match for `1.2.3.4`. Both directions, because getting either wrong is a
confident wrong answer rather than a visible failure.

**One `pmgsh` read, argv-safe**. The exact command is asserted, and so is the fact that
it is the only one — a search must not sync, create or delete.

**The session closes before the SSH hop.** The account search's rule, and the reason
`core.integrations.pmg.pmgsh_cli` takes a config rather than a row as of this task.

**A refusal names its cause**. An ambiguous `server_ref` comes back with
`choices`; a blank or unparseable target is refused before any I/O; an unpinned row answers
`ssh_host_key_not_validated` and never opens a connection; a `pmgsh` failure keeps the
code that names its remedy.

**Nothing a result carries is credential material**, asserted against both the
ciphertext in the column and the plaintext behind it — a leak of either into a LibreChat
transcript is the same leak.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import core.integrations.pmg.pmgsh_cli as pmgsh_cli_mod
from core.auth.tool_catalog import TOOL_CATALOG
from core.integrations.pmg.pmgsh_cli import MYNETWORKS_PATH, PMGSH_BINARY
from noa_api.mcp_server import build_mcp_server
from noa_api.mcp_tools.pmg_read import (
    ERROR_INVALID_TARGET,
    ERROR_TARGET_REQUIRED,
    TOOL_PMG_WHITELIST_SEARCH,
    pmg_whitelist_search,
)
from noa_api.mcp_tools.results import (
    ERROR_TIMEOUT,
    ERROR_TOOL_EXECUTION_FAILED,
    MESSAGE_TIMEOUT,
    MESSAGE_TOOL_EXECUTION_FAILED,
)
from support.mcp_identity import StubSession
from support.pmg import (
    SERVER_NAME,
    SSH_PASSWORD_PLAINTEXT,
    SSH_PRIVATE_KEY_PLAINTEXT,
    mynetworks_output,
    whitelist_context,
    whitelist_server,
)
from support.remote_exec import SUDO_DENIED_STDERR, command_result, install_fake_ssh_exec
from support.secrets import build_cipher
from support.servers import SECRETS, ToolFixture, build_tool_context, pmg_server

READ_COMMAND = f"TERM=dumb {PMGSH_BINARY} ls {MYNETWORKS_PATH}"


async def search(
    fixture: ToolFixture,
    *,
    target: str = "1.2.3.4",
    server_ref: str = SERVER_NAME,
) -> dict[str, Any]:
    return await pmg_whitelist_search(server_ref=server_ref, target=target, context=fixture.context)


# --- The happy path ---


async def test_it_answers_membership_with_the_entry_that_matched(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The shape a model reads: the verdict, what it was tested against, and the evidence."""
    fixture, _ = whitelist_context(
        monkeypatch,
        answer=command_result(stdout=mynetworks_output("10.10.10.0/24", "1.2.3.4/32")),
    )

    result = await search(fixture, target="1.2.3.4")

    assert result == {
        "ok": True,
        "server_id": str(fixture.pmg_servers.servers[0].id),
        "target": "1.2.3.4",
        "normalized_target": "1.2.3.4/32",
        "exists": True,
        "matches": [{"cidr": "1.2.3.4/32", "normalized": "1.2.3.4/32"}],
        "total_entries": 2,
    }


async def test_an_absent_address_is_a_success_with_no_matches(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Nothing has gone wrong: PMG answered and the address is not there."""
    fixture, _ = whitelist_context(
        monkeypatch, answer=command_result(stdout=mynetworks_output("10.10.10.0/24"))
    )

    result = await search(fixture, target="1.2.3.4")

    assert result["ok"] is True
    assert result["exists"] is False
    assert result["matches"] == []
    assert result["total_entries"] == 1


async def test_a_bare_address_matches_its_host_route_entry(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Exact membership end to end, in the direction that matters most: the operator types an
    address and PMG stores a /32. Comparing raw strings would answer "not whitelisted"."""
    fixture, _ = whitelist_context(
        monkeypatch, answer=command_result(stdout=mynetworks_output("1.2.3.4/32"))
    )

    result = await search(fixture, target="1.2.3.4")

    assert result["exists"] is True
    assert result["normalized_target"] == "1.2.3.4/32"


async def test_a_host_route_matches_a_bare_entry(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The same rule from the other side, and the raw spelling PMG uses travels back so an
    operator can find the line on the box."""
    fixture, _ = whitelist_context(
        monkeypatch, answer=command_result(stdout=mynetworks_output("1.2.3.4"))
    )

    result = await search(fixture, target="1.2.3.4/32")

    assert result["exists"] is True
    assert result["matches"] == [{"cidr": "1.2.3.4", "normalized": "1.2.3.4/32"}]


async def test_a_containing_network_is_not_a_match(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Exact membership means `1.2.3.0/24` in `mynetworks` does not make `1.2.3.4` an
    entry — and telling the operator it does sends them to remove a CIDR they never named."""
    fixture, _ = whitelist_context(
        monkeypatch, answer=command_result(stdout=mynetworks_output("1.2.3.0/24"))
    )

    result = await search(fixture, target="1.2.3.4")

    assert result["exists"] is False
    assert result["matches"] == []


async def test_an_ipv6_address_is_answered_too(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fixture, _ = whitelist_context(
        monkeypatch, answer=command_result(stdout=mynetworks_output("2001:db8::1/128"))
    )

    result = await search(fixture, target="2001:db8::1")

    assert result["exists"] is True
    assert result["normalized_target"] == "2001:db8::1/128"


async def test_a_network_target_reports_what_was_actually_tested(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`ipaddress` masks host bits, so `1.2.3.4/24` is a question about `1.2.3.0/24`. Echoing
    the input instead would let the operator read the verdict as being about their address."""
    fixture, _ = whitelist_context(
        monkeypatch, answer=command_result(stdout=mynetworks_output("1.2.3.0/24"))
    )

    result = await search(fixture, target="1.2.3.4/24")

    assert result["target"] == "1.2.3.4/24"
    assert result["normalized_target"] == "1.2.3.0/24"
    assert result["exists"] is True


async def test_a_repeated_entry_is_reported_rather_than_collapsed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`noa-old` deduplicated while parsing. Two spellings of one address in `mynetworks` is a
    fact about the whitelist, and removing one has to know both lines are there."""
    fixture, _ = whitelist_context(
        monkeypatch, answer=command_result(stdout=mynetworks_output("1.2.3.4", "1.2.3.4/32"))
    )

    result = await search(fixture, target="1.2.3.4")

    assert [match["cidr"] for match in result["matches"]] == ["1.2.3.4", "1.2.3.4/32"]


async def test_an_empty_whitelist_says_it_read_nothing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`exists: false` against zero parsed entries is a different fact from `exists: false`
    against three hundred, and `pmgsh ls` output alone cannot tell an empty `mynetworks` from a
    format NOA no longer recognises. The count is what lets an operator see which one they got.
    """
    fixture, _ = whitelist_context(monkeypatch, answer=command_result(stdout=mynetworks_output()))

    result = await search(fixture, target="1.2.3.4")

    assert result["exists"] is False
    assert result["total_entries"] == 0


# --- One argv-safe read, and only one ---


async def test_the_search_sends_one_argv_safe_mynetworks_read(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`pmgsh` over SSH, argv-only, against `/config/mynetworks` and nothing else.

    Asserted as the *whole* command list, so a search that also synced, created or deleted
    fails here — a READ tool that mutates is the one failure the READ/CHANGE split exists to
    make impossible.
    """
    fixture, fake = whitelist_context(monkeypatch)

    await search(fixture)

    assert fake.commands == [READ_COMMAND]


async def test_a_hostile_target_never_reaches_the_command(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The target is an LLM tool argument. It is refused as unparseable long before argv
    quoting would have to save it, and the read that goes out names no target at all —
    `pmgsh ls` takes the path and nothing else.
    """
    fixture, fake = whitelist_context(monkeypatch)

    result = await search(fixture, target="1.2.3.4; rm -rf /")

    assert result["ok"] is False
    assert result["error_code"] == ERROR_INVALID_TARGET
    assert fake.commands == []


async def test_a_non_root_ssh_user_escalates_end_to_end(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The sudo-prefix rule as a biconditional, through the tool rather than through the builder:
    the row's `ssh_username` decides, and the command that goes out is built from the config that
    opened the connection."""
    fixture, fake = whitelist_context(monkeypatch, ssh_username="noa-ops")

    await search(fixture)

    assert fake.commands == [f"TERM=dumb sudo -n {PMGSH_BINARY} ls {MYNETWORKS_PATH}"]


async def test_a_root_row_sends_no_sudo(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The other half of the biconditional — a blank `ssh_username` resolves to `root`."""
    fixture, fake = whitelist_context(monkeypatch)

    await search(fixture)

    assert "sudo" not in fake.commands[0]


# --- The argument guards, before any I/O ---


@pytest.mark.parametrize("target", ["", "   ", "\t\n"])
async def test_a_blank_target_is_refused_before_any_io(monkeypatch, target: str) -> None:  # type: ignore[no-untyped-def]
    """The schema cannot express it — `min_length` counts whitespace — so this is the
    gate, and searching for "" is not a question."""
    fixture, fake = whitelist_context(monkeypatch)

    result = await search(fixture, target=target)

    assert result["ok"] is False
    assert result["error_code"] == ERROR_TARGET_REQUIRED
    # Refused before any I/O: no server was resolved and PMG was never reached.
    assert fixture.pmg_servers.reads == 0
    assert fake.runs == []


@pytest.mark.parametrize(
    "target",
    [
        pytest.param("not-a-cidr", id="word"),
        pytest.param("example.com", id="hostname"),
        pytest.param("1.2.3.256", id="octet-out-of-range"),
        pytest.param("1.2.3.4/33", id="prefix-out-of-range"),
    ],
)
async def test_a_target_that_is_not_an_ip_or_cidr_is_refused(monkeypatch, target: str) -> None:  # type: ignore[no-untyped-def]
    """The only alternative is comparing raw strings against normalised entries, which answers
    "not whitelisted" for everything — a confident wrong answer.

    A hostname is refused rather than resolved: `mynetworks` holds CIDRs, and turning a name
    into an address here would answer about whatever DNS said at that moment.
    """
    fixture, fake = whitelist_context(monkeypatch)

    result = await search(fixture, target=target)

    assert result["ok"] is False
    assert result["error_code"] == ERROR_INVALID_TARGET
    assert fixture.pmg_servers.reads == 0
    assert fake.runs == []


async def test_a_blank_server_ref_is_refused(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Whitespace refused on the other required string too."""
    fixture, fake = whitelist_context(monkeypatch)

    result = await search(fixture, server_ref="   ")

    assert result["ok"] is False
    assert result["error_code"] == "host_required"
    assert fake.runs == []


# --- Which server did they mean? ---


async def test_an_ambiguous_server_ref_returns_choices(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A tie is candidates, never a pick — a guess answers about a node the operator
    never named."""
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

    result = await search(fixture, server_ref=shared)

    assert result["ok"] is False
    assert result["error_code"] == "host_ambiguous"
    assert [choice["name"] for choice in result["choices"]] == ["alpha", "bravo"]
    # No server was picked, so nothing was contacted.
    assert fake.runs == []


async def test_an_unknown_server_ref_is_host_not_found(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fixture, _ = whitelist_context(monkeypatch)

    result = await search(fixture, server_ref="nope")

    assert result["ok"] is False
    assert result["error_code"] == "host_not_found"
    assert "choices" not in result


async def test_the_resolved_server_is_the_one_contacted(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The row that won the resolution is the row whose host and credentials are used.

    Asserted on the connection the transport saw: a re-read by id, or a fallback to "the first
    server", would send the operator's question to a node they did not name.
    """
    cipher = build_cipher()
    fixture, fake = whitelist_context(
        monkeypatch,
        cipher=cipher,
        servers=[
            whitelist_server("alpha", cipher=cipher),
            whitelist_server("bravo", cipher=cipher),
        ],
    )

    await search(fixture, server_ref="bravo")

    assert fake.runs[0].config.host == "bravo.example.net"


async def test_a_server_ref_by_id_resolves(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The unambiguous form, and what a `choices` list tells the caller to come back with."""
    cipher = build_cipher()
    server_id = uuid4()
    fixture, fake = whitelist_context(
        monkeypatch,
        cipher=cipher,
        servers=[whitelist_server("alpha", cipher=cipher, server_id=server_id)],
    )

    result = await search(fixture, server_ref=str(server_id))

    assert result["ok"] is True
    assert fake.commands == [READ_COMMAND]


# --- The connection the row produces ---


async def test_an_unpinned_server_is_refused_before_any_ssh(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """No pin, no connection — and the refusal names the *server*.

    This case lived in `test_pmg_pmgsh_cli.py` until the CLI layer stopped taking rows. It
    belongs here now, and it is worth more here: reaching the box with an unpinned row would
    send the decrypted credentials to whatever answered, and reporting it as a failed `pmgsh`
    command would send the operator to fix the wrong thing.
    """
    cipher = build_cipher()
    fixture, fake = whitelist_context(
        monkeypatch,
        cipher=cipher,
        servers=[whitelist_server(SERVER_NAME, cipher=cipher, ssh_host_key_fingerprint=None)],
    )

    result = await search(fixture)

    assert result["ok"] is False
    assert result["error_code"] == "ssh_host_key_not_validated"
    assert fake.runs == []


async def test_a_server_with_no_credentials_is_refused_before_any_ssh(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`ssh_not_configured` is an admin filling in the row, not a retry — a distinction a
    generic command failure would lose."""
    fixture, fake = whitelist_context(
        monkeypatch,
        servers=[pmg_server(SERVER_NAME, ssh_password=None, ssh_private_key=None)],
    )

    result = await search(fixture)

    assert result["ok"] is False
    assert result["error_code"] == "ssh_not_configured"
    assert fake.runs == []


async def test_the_ssh_connection_uses_the_decrypted_credentials(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The columns hold `enc:v1:fernet:…`; the transport has to receive plaintext.

    This is the assertion that keeps the one decrypt site in the path. A tool that passed the
    column value straight through would still "work" against a doubled transport and fail only
    against a real PMG node.
    """
    fixture, fake = whitelist_context(monkeypatch)

    await search(fixture)

    [run] = fake.runs
    assert run.config.password == SSH_PASSWORD_PLAINTEXT
    assert run.config.private_key == SSH_PRIVATE_KEY_PLAINTEXT
    assert run.config.host_key_fingerprint is not None


async def test_the_database_session_closes_before_the_ssh_hop(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The account search's rule, and the reason the PMG SSH layer takes a config rather than a row.

    A pooled Postgres connection held across a hop to someone else's host is how a slow PMG
    node becomes a database outage.
    """
    events: list[str] = []
    fixture, _ = whitelist_context(monkeypatch)

    def recording_handler(_command: str):  # type: ignore[no-untyped-def]
        events.append("ssh")
        return command_result(stdout=mynetworks_output("1.2.3.4/32"))

    install_fake_ssh_exec(monkeypatch, pmgsh_cli_mod, recording_handler)

    @asynccontextmanager
    async def recording_session_factory():  # type: ignore[no-untyped-def]
        events.append("session-open")
        try:
            yield cast("AsyncSession", StubSession())
        finally:
            events.append("session-close")

    context = replace(fixture.context, session_factory=recording_session_factory)
    result = await pmg_whitelist_search(server_ref=SERVER_NAME, target="1.2.3.4", context=context)

    assert result["ok"] is True
    assert events == ["session-open", "session-close", "ssh"]


# --- What leaves the process ---


async def test_the_result_carries_no_credential_material(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Neither the ciphertext in the column nor the plaintext behind it.

    Against the whole serialized payload rather than key by key: what must hold is that the
    *values* appear nowhere, however they are nested.
    """
    fixture, _ = whitelist_context(
        monkeypatch, answer=command_result(stdout=mynetworks_output("1.2.3.4/32"))
    )

    result = await search(fixture)
    serialized = json.dumps(result, default=str)

    for secret in (*SECRETS, SSH_PASSWORD_PLAINTEXT, SSH_PRIVATE_KEY_PLAINTEXT):
        assert secret not in serialized


async def test_the_result_carries_no_server_row_fields(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A search answers about the whitelist. The node is identified by id and nothing else —
    its host, its username and its fingerprint are inventory, not an answer."""
    fixture, _ = whitelist_context(monkeypatch)

    result = await search(fixture)

    assert set(result) == {
        "ok",
        "server_id",
        "target",
        "normalized_target",
        "exists",
        "matches",
        "total_entries",
    }


# --- Failures ---


async def test_a_denied_sudo_keeps_its_own_code(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`noa-old` GH #82, at the tool boundary: `ssh_sudo_required` means fix the sudoers entry,
    and collapsing it into a generic failure sends an operator hunting an install that is
    already there."""
    fixture, _ = whitelist_context(
        monkeypatch,
        ssh_username="noa-ops",
        answer=command_result(exit_code=1, stderr=SUDO_DENIED_STDERR),
    )

    result = await search(fixture)

    assert result["ok"] is False
    assert result["error_code"] == "ssh_sudo_required"


async def test_a_failed_pmgsh_read_keeps_pmg_s_own_code(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`pmgsh_command_failed` carries PMG's own output, which is the diagnosis and carries no
    credential (see `core.integrations.pmg.pmgsh_cli`)."""
    fixture, _ = whitelist_context(
        monkeypatch,
        answer=command_result(exit_code=2, stderr="pmgsh: 501 no such option"),
    )

    result = await search(fixture)

    assert result["ok"] is False
    assert result["error_code"] == "pmgsh_command_failed"
    assert "501" in result["message"]


class ExplodingPMGServerRepository:
    """A `PMGServerReadRepository` that fails the way a real one can."""

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
    """The sanitizer's two mappings, and the original text never travels."""
    context = replace(
        build_tool_context().context,
        pmg_server_repository_factory=lambda _session: ExplodingPMGServerRepository(error),
    )

    result = await pmg_whitelist_search(server_ref=SERVER_NAME, target="1.2.3.4", context=context)

    assert result == {"ok": False, "error_code": expected_code, "message": expected_message}
    assert "10.0.0.5" not in json.dumps(result)


async def test_cancellation_is_not_swallowed() -> None:
    """A cancelled request has no caller left to answer; swallowing it hangs a shutdown."""
    context = replace(
        build_tool_context().context,
        pmg_server_repository_factory=lambda _session: ExplodingPMGServerRepository(
            asyncio.CancelledError()
        ),
    )

    with pytest.raises(asyncio.CancelledError):
        await pmg_whitelist_search(server_ref=SERVER_NAME, target="1.2.3.4", context=context)


# --- The tool ships with its gate, and its schema ---


def test_the_tool_name_matches_the_catalog() -> None:
    """The registered name is the one RBAC grants are written against."""
    assert TOOL_PMG_WHITELIST_SEARCH in TOOL_CATALOG


async def test_the_schema_takes_a_server_and_a_target_and_nothing_else() -> None:
    """A READ tool has no reason either, and a schema is where one would appear.

    Read off the registered tool rather than restated, so a signature that grew a parameter
    fails here instead of quietly accepting one from a client.
    """
    server = build_mcp_server(tool_context=build_tool_context().context)
    tools = {tool.name: tool for tool in await server.list_tools(run_middleware=False)}

    schema = tools[TOOL_PMG_WHITELIST_SEARCH].parameters

    assert set(schema["properties"]) == {"server_ref", "target"}
    assert sorted(schema["required"]) == ["server_ref", "target"]
