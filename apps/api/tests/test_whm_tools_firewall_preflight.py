"""`whm_preflight_firewall_entries` — step 1 of the firewall flow.

The one exposed preflight (DECISIONS section 6.5): the operator reads the verdict and decides
whether to release, so it is an operator decision point rather than a gate, and the "preflight goes
internal" rule does not reach it.

Real resolver, real `SecretCipher`, real CSF and Imunify parsing, real command composition, real
`sanitize_tool_errors`. Only the SSH socket is doubled — replaced in all three modules the tool
crosses (`availability`, `csf_cli`, `imunify_cli`) by one recorder, so a test can assert the
whole command sequence rather than one hop of it.

Five properties carry the weight.

**Both backends, in parallel, and only the usable ones.** Zero usable backends is an error and never
an empty success; a denied `sudo -n` gets its own code, because "no firewall tools on this server"
sends an operator hunting an install that is already there (`noa-old` GH #82).

**A verdict read from a subset says so.** `noa-old` fell through to `not_found` when a
backend failed, so a broken CSF plus a clean Imunify reported "this address is not blocked" on a
box whose *blocking* backend was silent. Here the backend that did not answer is named, and a
call where nothing answered is `unknown`. The zero-backend rule bounds only the zero case; this
is the partial one, and this tool is where the name-the-gap rule was written.

**The preflight accepts every target kind.** The CHANGE tools reject anything but IPv4
because they write rules; reporting what CSF says about an IPv6 address is useful regardless.

**Truncated evidence states its bound.** `csf -g` on a busy box is hundreds of lines and
the parser keeps twenty.

**Nothing a result carries is credential material**, asserted against both the
ciphertext in the column and the plaintext behind it — a leak of either into a persisted
transcript is the same leak.

**And, since the release-and-allow tool, nothing a result carries is the operator's approval
reason.** This tool
reads csf's own lines into a transcript, and that tool writes the reason into the comment of every
allow entry it creates — so this file is where the return path is closed, the way
`test_whm_tools_search_accounts.py` is for WHM's `suspendreason`. The cut and its negative
control are asserted together, because a rule that cut every line would pass the first assertion
and destroy the evidence the tool exists for.
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

from core.auth.tool_catalog import TOOL_CATALOG
from core.integrations.whm.csf_cli import CSF_BINARY
from core.integrations.whm.firewall_gate import ERROR_NO_FIREWALL_BACKEND
from core.integrations.whm.imunify_cli import IMUNIFY_BINARY
from core.remote_exec.types import CommandResult
from noa_api.mcp_server import build_mcp_server
from noa_api.mcp_tools.results import (
    ERROR_TIMEOUT,
    ERROR_TOOL_EXECUTION_FAILED,
    MESSAGE_TIMEOUT,
    MESSAGE_TOOL_EXECUTION_FAILED,
)
from noa_api.mcp_tools.whm_firewall import (
    ERROR_INVALID_RESPONSE,
    ERROR_INVALID_TARGET,
    ERROR_TARGET_REQUIRED,
    NOA_COMMENT_MARKER,
    TOOL_WHM_PREFLIGHT_FIREWALL_ENTRIES,
    VERDICT_ALLOWLISTED,
    VERDICT_BLOCKED,
    VERDICT_NOT_FOUND,
    VERDICT_UNKNOWN,
    noa_firewall_comment,
    whm_preflight_firewall_entries,
)
from support.mcp_identity import StubSession
from support.remote_exec import (
    SUDO_DENIED_STDERR,
    command_result,
    install_fake_ssh_exec,
    patch_ssh_exec,
)
from support.secrets import build_cipher
from support.servers import SECRETS, ToolFixture, build_tool_context, whm_server
from support.whm_firewall import (
    CSF_ALLOW_LINE,
    CSF_CLEAN_OUTPUT,
    CSF_DENY_LINE,
    CSF_UNREADABLE_OUTPUT,
    IMUNIFY_CLEAN,
    IMUNIFY_DROP,
    IMUNIFY_WHITE,
    SERVER_NAME,
    SSH_PASSWORD_PLAINTEXT,
    SSH_PRIVATE_KEY_PLAINTEXT,
    TARGET,
    FakeFirewall,
    both_backends,
    csf_answer,
    firewall_context,
    imunify_answer,
    is_probe,
    is_query,
    preflight_server,
)


async def preflight(
    fixture: ToolFixture, *, server_ref: str = SERVER_NAME, target: str = TARGET
) -> dict[str, Any]:
    return await whm_preflight_firewall_entries(
        server_ref=server_ref, target=target, context=fixture.context
    )


# --- Both backends, only the usable ones, in parallel ---


async def test_both_backends_are_queried_and_their_evidence_is_merged(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The baseline. Every refusal below is only meaningful if this path works."""
    fixture, fake = firewall_context(
        monkeypatch,
        firewall=both_backends(csf=csf_answer(CSF_DENY_LINE), imunify=imunify_answer(IMUNIFY_DROP)),
    )

    result = await preflight(fixture)

    assert result["ok"] is True
    assert result["available_backends"] == {"csf": True, "imunify": True}
    assert result["csf"] == {"ok": True, "verdict": "blocked"}
    assert result["imunify"] == {"ok": True, "verdict": "blacklisted"}
    assert result["combined_verdict"] == VERDICT_BLOCKED
    assert result["unanswered_backends"] == []
    assert result["matches"] == [
        CSF_DENY_LINE,
        f"Imunify blacklist: {TARGET} (smtpauth brute force)",
    ]
    # Two probes and two queries, no more.
    assert len(fake.commands) == 4
    assert [command for command in fake.commands if is_query(command)] == [
        f"TERM=dumb {CSF_BINARY} -g {TARGET}",
        f"{IMUNIFY_BINARY} ip-list local list --by-ip {TARGET} --json",
    ]


async def test_a_backend_that_is_not_installed_is_never_queried(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """CSF-only is the common shape — plenty of cPanel boxes never install Imunify.

    Querying it anyway would produce a failure entry that says "not installed" in a code that
    means "the command broke", and `available_backends` already says it plainly.
    """
    fixture, fake = firewall_context(
        monkeypatch, firewall=FakeFirewall(csf=csf_answer(CSF_DENY_LINE), imunify=None)
    )

    result = await preflight(fixture)

    assert result["available_backends"] == {"csf": True, "imunify": False}
    assert result["combined_verdict"] == VERDICT_BLOCKED
    assert "imunify" not in result
    assert not any(is_query(command) and IMUNIFY_BINARY in command for command in fake.commands)


async def test_both_backend_queries_are_in_flight_at_once(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`asyncio.gather`, never two sequential awaits.

    Each query is a full SSH handshake to the same host while an operator waits on a chat turn.
    The wait below deadlocks unless the second query starts before the first returns, so a
    sequential rewrite fails here rather than merely being slower.
    """
    box = both_backends()
    both_started = asyncio.Event()
    in_flight = 0

    async def fake_ssh_exec(config, *, command: str, **_):  # type: ignore[no-untyped-def]
        nonlocal in_flight
        if is_query(command):
            in_flight += 1
            if in_flight == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=2)
        return box(command)

    fixture, _ = firewall_context(monkeypatch)
    patch_ssh_exec(monkeypatch, fake_ssh_exec)

    result = await preflight(fixture)

    assert both_started.is_set()
    assert result["ok"] is True


# --- Zero backends is an error, never an empty success ---
#
# The tool holds no copy of this check: it is `firewall_gate.run_on_usable_backends`'
# refusal, raised where the backend set becomes work and travelling back through
# `sanitize_tool_errors`. These two cases are therefore also what proves the guard is no
# longer per-tool — they pass with nothing about availability written in this tool at all, which
# is what the firewall CHANGE tools inherit. `test_whm_firewall_gate.py` asserts the gate itself.


async def test_zero_usable_backends_is_an_error_not_an_empty_success(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A success carrying `not_found` here would say the address is clean on a server
    whose firewall NOA could not read at all."""
    fixture, fake = firewall_context(monkeypatch, firewall=FakeFirewall())

    result = await preflight(fixture)

    assert result["ok"] is False
    assert result["error_code"] == ERROR_NO_FIREWALL_BACKEND
    # Nothing was queried, so nothing can be reported.
    assert all(is_probe(command) for command in fake.commands)


async def test_zero_backends_with_denied_sudo_names_sudo_rather_than_the_install(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """`noa-old` GH #82: two causes, two remedies, two codes.

    The binaries are there; the sudoers line is not. Reporting "no firewall tools" sends the
    operator to install software that is already installed.
    """
    fixture, _ = firewall_context(
        monkeypatch,
        firewall=FakeFirewall(csf=csf_answer(), imunify=imunify_answer(), sudo_denied=True),
        ssh_username="noa-ops",
    )

    result = await preflight(fixture)

    assert result["ok"] is False
    assert result["error_code"] == "ssh_sudo_required"


async def test_one_denied_backend_does_not_stop_the_other(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`sudo_required` travels on a successful result too: it is why Imunify is missing from
    `available_backends`, and the operator would otherwise read that as "not installed"."""

    class OneDenied(FakeFirewall):
        def __call__(self, command: str) -> CommandResult:
            if is_probe(command) and IMUNIFY_BINARY in command:
                return command_result(command=command, exit_code=1, stderr=SUDO_DENIED_STDERR)
            return super().__call__(command)

    fixture, _ = firewall_context(
        monkeypatch,
        firewall=OneDenied(csf=csf_answer(CSF_DENY_LINE), imunify=imunify_answer()),
        ssh_username="noa-ops",
    )

    result = await preflight(fixture)

    assert result["ok"] is True
    assert result["available_backends"] == {"csf": True, "imunify": False}
    assert result["sudo_required"] is True
    assert result["combined_verdict"] == VERDICT_BLOCKED


# --- the combined verdict, and what it refuses to claim ---


@pytest.mark.parametrize(
    ("csf", "imunify", "expected"),
    [
        pytest.param(CSF_DENY_LINE, IMUNIFY_CLEAN, VERDICT_BLOCKED, id="csf-blocks"),
        pytest.param(CSF_CLEAN_OUTPUT, IMUNIFY_DROP, VERDICT_BLOCKED, id="imunify-blocks"),
        pytest.param(CSF_ALLOW_LINE, IMUNIFY_DROP, VERDICT_BLOCKED, id="one-of-each-blocks"),
        pytest.param(CSF_ALLOW_LINE, IMUNIFY_CLEAN, VERDICT_ALLOWLISTED, id="csf-allows"),
        pytest.param(CSF_CLEAN_OUTPUT, IMUNIFY_WHITE, VERDICT_ALLOWLISTED, id="imunify-allows"),
        pytest.param(CSF_CLEAN_OUTPUT, IMUNIFY_CLEAN, VERDICT_NOT_FOUND, id="both-clean"),
    ],
)
async def test_a_block_on_either_backend_wins_over_an_allow(  # type: ignore[no-untyped-def]
    monkeypatch, csf: str, imunify: str, expected: str
) -> None:
    """Block beats allow, mirroring each backend's own precedence.

    An address can sit in `csf.deny` and `csf.allow` at once, and the operationally true
    statement is "still blocked" — the release-and-allow tool releases *and* allows in one action,
    so that intermediate state is real.
    """
    fixture, _ = firewall_context(
        monkeypatch,
        firewall=both_backends(csf=csf_answer(csf), imunify=imunify_answer(imunify)),
    )

    assert (await preflight(fixture))["combined_verdict"] == expected


async def test_a_backend_that_did_not_answer_is_named_and_never_reads_as_clean(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The departure from `noa-old`, and the reason it matters.

    There the combined verdict was computed from whichever backend succeeded and otherwise fell
    through to `not_found`: a broken CSF plus a clean Imunify reported "this address is not
    blocked" — a fabrication the tool authored. The verdict is still
    reported, because CSF-down is not a reason to withhold Imunify's answer, only to bound what
    it means, so the gap is stated beside it.
    """
    fixture, _ = firewall_context(
        monkeypatch,
        firewall=both_backends(
            csf=csf_answer("csf: command failed", exit_code=2), imunify=imunify_answer()
        ),
    )

    result = await preflight(fixture)

    assert result["ok"] is True
    assert result["combined_verdict"] == VERDICT_NOT_FOUND
    assert result["unanswered_backends"] == ["csf"]
    assert result["csf"]["ok"] is False


async def test_unreadable_csf_output_is_not_an_answer_either(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """CSF's own `unknown` verdict means "csf said something we do not recognise".

    A parse regression must never read as a clean address, so `unknown` counts as not having
    answered even though the command succeeded.
    """
    fixture, _ = firewall_context(
        monkeypatch, firewall=FakeFirewall(csf=csf_answer(CSF_UNREADABLE_OUTPUT))
    )

    result = await preflight(fixture)

    assert result["csf"] == {"ok": True, "verdict": VERDICT_UNKNOWN}
    assert result["unanswered_backends"] == ["csf"]
    assert result["combined_verdict"] == VERDICT_UNKNOWN


async def test_when_no_backend_answers_the_verdict_is_unknown(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Both installed, both broken. The tool ran; it has no answer, and says so."""
    fixture, _ = firewall_context(
        monkeypatch,
        firewall=both_backends(
            csf=csf_answer("csf: command failed", exit_code=2),
            imunify=imunify_answer("not json at all"),
        ),
    )

    result = await preflight(fixture)

    assert result["combined_verdict"] == VERDICT_UNKNOWN
    assert sorted(result["unanswered_backends"]) == ["csf", "imunify"]
    assert result["matches"] == []


# --- A failed backend keeps the code that names its remedy ---


@pytest.mark.parametrize(
    ("firewall", "backend", "expected_code"),
    [
        pytest.param(
            both_backends(csf=csf_answer("csf: not running", exit_code=2)),
            "csf",
            "csf_command_failed",
            id="csf-exit-non-zero",
        ),
        pytest.param(
            both_backends(csf=csf_answer("   ")),
            "csf",
            ERROR_INVALID_RESPONSE,
            id="csf-empty-output",
        ),
        pytest.param(
            both_backends(imunify=imunify_answer("not json at all")),
            "imunify",
            "imunify_json_parse_error",
            id="imunify-unparseable",
        ),
        pytest.param(
            both_backends(imunify=imunify_answer("   ")),
            "imunify",
            "imunify_empty_response",
            id="imunify-empty",
        ),
    ],
)
async def test_a_backend_failure_keeps_its_own_error_code(  # type: ignore[no-untyped-def]
    monkeypatch, firewall: FakeFirewall, backend: str, expected_code: str
) -> None:
    """Which system to go fix is in the code, so it is not collapsed into one."""
    fixture, _ = firewall_context(monkeypatch, firewall=firewall)

    result = await preflight(fixture)

    assert result[backend] == {
        "ok": False,
        "error_code": expected_code,
        "message": result[backend]["message"],
    }


# --- The preflight accepts every kind the CHANGE tools refuse ---


@pytest.mark.parametrize(
    ("target", "kind"),
    [
        pytest.param("1.2.3.4", "ip", id="ipv4"),
        pytest.param("1.2.3.0/24", "cidr", id="ipv4-network"),
        pytest.param("2001:db8::1", "ipv6", id="ipv6"),
        pytest.param("2001:db8::/32", "ipv6_cidr", id="ipv6-network"),
        pytest.param("app-01.example.com", "hostname", id="hostname"),
    ],
)
async def test_the_preflight_answers_for_every_target_kind(  # type: ignore[no-untyped-def]
    monkeypatch, target: str, kind: str
) -> None:
    """The IPv4-only rule belongs to the CHANGE tools, which write rules.

    "You asked about an IPv6 address and here is what CSF says" is a useful answer even where
    changing it is not permitted. `target_kind` travels so the model does not have to classify
    an address itself before deciding what it may call next.
    """
    fixture, fake = firewall_context(monkeypatch)

    result = await preflight(fixture, target=target)

    assert result["ok"] is True
    assert result["target"] == target
    assert result["target_kind"] == kind
    assert f"{CSF_BINARY} -g {target}" in " ".join(fake.commands)


async def test_an_unclassifiable_target_is_refused_before_any_ssh(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`csf -g bad_target` would grep the tables for a string that is not an address."""
    fixture, fake = firewall_context(monkeypatch)

    result = await preflight(fixture, target="not an address")

    assert result["ok"] is False
    assert result["error_code"] == ERROR_INVALID_TARGET
    assert fake.runs == []


async def test_the_target_is_trimmed_but_never_normalised(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A bare address stays `ip`. Turning `1.2.3.4` into `1.2.3.4/32` would erase the
    distinction the CHANGE tools check."""
    fixture, fake = firewall_context(monkeypatch)

    result = await preflight(fixture, target="  1.2.3.4  ")

    assert result["target"] == "1.2.3.4"
    assert result["target_kind"] == "ip"
    assert f"TERM=dumb {CSF_BINARY} -g 1.2.3.4" in fake.commands


# --- The argument guards, before any I/O ---


@pytest.mark.parametrize("target", ["", "   ", "\t\n"])
async def test_a_blank_target_is_refused_before_any_io(monkeypatch, target: str) -> None:  # type: ignore[no-untyped-def]
    """`csf -g ""` matches every line in the tables."""
    fixture, fake = firewall_context(monkeypatch)

    result = await preflight(fixture, target=target)

    assert result["ok"] is False
    assert result["error_code"] == ERROR_TARGET_REQUIRED
    # No server was resolved and no host was reached.
    assert fixture.servers.reads == 0
    assert fake.runs == []


async def test_a_blank_server_ref_is_refused(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fixture, fake = firewall_context(monkeypatch)

    result = await preflight(fixture, server_ref="   ")

    assert result["ok"] is False
    assert result["error_code"] == "host_required"
    assert fake.runs == []


# --- Which server did they mean? ---


async def test_an_ambiguous_server_ref_returns_choices(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A tie is candidates, never a pick — a guess would read the wrong firewall."""
    shared = "https://shared.example.net:2087"
    fixture, fake = firewall_context(
        monkeypatch,
        servers=[whm_server("one", base_url=shared), whm_server("two", base_url=shared)],
    )

    result = await preflight(fixture, server_ref="shared.example.net")

    assert result["ok"] is False
    assert result["error_code"] == "host_ambiguous"
    assert [choice["name"] for choice in result["choices"]] == ["one", "two"]
    assert fake.runs == []


async def test_the_resolved_server_is_the_one_connected_to(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The row that won the resolution is the row whose credentials are used.

    Asserted on the host the transport saw: a re-read by id, or a fallback to "the first
    server", would check the firewall of a machine the operator did not name.
    """
    cipher = build_cipher()
    fixture, fake = firewall_context(
        monkeypatch,
        cipher=cipher,
        servers=[preflight_server(name, cipher=cipher) for name in ("alpha", "beta")],
    )

    await preflight(fixture, server_ref="beta")

    assert {run.config.host for run in fake.runs} == {"beta.example.net"}


async def test_a_server_ref_by_id_resolves(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    server_id = uuid4()
    cipher = build_cipher()
    fixture, fake = firewall_context(
        monkeypatch,
        cipher=cipher,
        servers=[preflight_server(SERVER_NAME, cipher=cipher, server_id=server_id)],
    )

    result = await preflight(fixture, server_ref=str(server_id))

    assert result["server_id"] == str(server_id)
    assert fake.runs


# --- the row has to become a connection, and the refusal names the row ---


@pytest.mark.parametrize(
    ("row", "expected_code"),
    [
        pytest.param(
            {"ssh_host_key_fingerprint": None},
            "ssh_host_key_not_validated",
            id="unpinned",
        ),
        pytest.param(
            {"ssh_password": None, "ssh_private_key": None},
            "ssh_not_configured",
            id="no-credentials",
        ),
        pytest.param({"base_url": "not-a-url"}, "ssh_invalid_host", id="bad-base-url"),
    ],
)
async def test_a_row_that_cannot_become_a_connection_names_the_row(  # type: ignore[no-untyped-def]
    monkeypatch, row: dict[str, Any], expected_code: str
) -> None:
    """These used to reach `check_firewall_binaries` and come back as "no firewall backends",
    which sends an operator to install software when the fix is an admin field.

    `resolve_whm_ssh_config` raises an `SSHExecutionError`, which is a `NoaError`, so
    `sanitize_tool_errors` hands the model the code that names the remedy.
    """
    fixture, fake = firewall_context(monkeypatch, servers=[whm_server(SERVER_NAME, **row)])

    result = await preflight(fixture)

    assert result["ok"] is False
    assert result["error_code"] == expected_code
    assert fake.runs == []


# --- The credential path and the pin ---


async def test_the_ssh_hop_uses_the_decrypted_credentials_and_the_pin(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The columns hold ciphertext; the handshake needs plaintext and a fingerprint.

    This is the assertion that keeps `resolve_whm_ssh_config` in the path. A tool that passed
    the column values straight through would still "work" against a doubled transport and fail
    only in production — and the pin travelling is what `ssh_exec` enforces.
    """
    fixture, fake = firewall_context(monkeypatch)

    await preflight(fixture)

    assert fake.runs
    for run in fake.runs:
        assert run.config.password == SSH_PASSWORD_PLAINTEXT
        assert run.config.private_key == SSH_PRIVATE_KEY_PLAINTEXT
        assert run.config.host_key_fingerprint


async def test_the_row_is_resolved_once_for_the_whole_call(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Four hops, one connection value. Resolving per command would decrypt four times and,
    worse, would need the ORM row alive for the length of the call."""
    fixture, fake = firewall_context(monkeypatch)

    await preflight(fixture)

    assert len(fake.runs) == 4
    assert len({id(run.config) for run in fake.runs}) == 1


async def test_the_database_session_closes_before_the_first_ssh_hop(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """No session held across a hop, and the reason the SSH layer takes a config rather than a row.

    A pooled Postgres connection held across four SSH handshakes to someone else's host is how
    a slow WHM server becomes a database outage.
    """
    events: list[str] = []
    box = both_backends()

    def recording_handler(command: str) -> CommandResult:
        events.append("ssh")
        return box(command)

    fixture, _ = firewall_context(monkeypatch)
    install_fake_ssh_exec(monkeypatch, recording_handler)

    @asynccontextmanager
    async def recording_session_factory():  # type: ignore[no-untyped-def]
        events.append("session-open")
        try:
            yield cast("AsyncSession", StubSession())
        finally:
            events.append("session-close")

    context = replace(fixture.context, session_factory=recording_session_factory)
    result = await whm_preflight_firewall_entries(
        server_ref=SERVER_NAME, target=TARGET, context=context
    )

    assert result["ok"] is True
    assert events[:2] == ["session-open", "session-close"]
    assert set(events[2:]) == {"ssh"}


# --- Capped evidence carries its own bound ---


async def test_capped_csf_evidence_states_its_own_bound(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The stated-cap rule, and this is the second capper the invariant was written ahead of (the
    account search was the first). Twenty lines with no other signal read as "there are twenty
    entries".

    The kept order is csf's own rather than a sort — the cap's ordering clause: what it
    asks for is reproducibility across identical calls, which `csf -g` gives and `listaccts`
    (where the clause was written) does not.
    """
    lines = [CSF_DENY_LINE] + [f"lfd: ({TARGET}) blocked attempt {index}" for index in range(40)]
    fixture, _ = firewall_context(
        monkeypatch, firewall=FakeFirewall(csf=csf_answer("\n".join(lines)))
    )

    result = await preflight(fixture)

    assert len(result["matches"]) == 20
    assert result["total_matches"] == 41
    assert result["truncated"] is True


async def test_an_uncapped_result_is_not_truncated(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Off-by-one guard, and the total counts both backends rather than only the capped one."""
    fixture, _ = firewall_context(
        monkeypatch,
        firewall=both_backends(csf=csf_answer(CSF_DENY_LINE), imunify=imunify_answer(IMUNIFY_DROP)),
    )

    result = await preflight(fixture)

    assert result["total_matches"] == 2
    assert len(result["matches"]) == 2
    assert result["truncated"] is False


# --- A comment NOA wrote does not come back through this tool --- The release-and-allow tool writes
# the operator's approval reason into the comment of every allow entry it creates (a firewall entry
# has a comment field whose only honest content is why the address was allowed). This tool reads
# csf's and Imunify's own text straight into a transcript, so it is the return path the reason rule
# closes, exactly as `whm_search_accounts` is for WHM's `suspendreason`. The cut is possible because
# NOA authored the comment and stamped a marker into it. What it costs is stated rather than hidden:
# csf gives a comment no closing boundary, so anything after the marker on that line goes with it.

REASON_WRITTEN_OUT = "customer confirmed, ticket NOC-4471"


def noa_commented_line(action_request_id: UUID, *, reason: str = REASON_WRITTEN_OUT) -> str:
    """A `csf.allow` line as csf echoes one NOA created."""
    comment = noa_firewall_comment(action_request_id, reason=reason)
    return f"Found {TARGET} in /etc/csf/csf.allow ({comment})"


async def test_a_comment_noa_wrote_is_cut_back_to_its_marker(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The one field the reason rule keeps from the LLM does not return through this result.

    Asserted on the serialized result rather than on a key set, because the reason is not a field
    here — it is text inside an evidence line, and a key comparison would pass over it.

    The marker survives, and that is the point of having one: `noa:<id>` is the address of the
    approval row where an operator can read the reason behind their own cookie, which is
    what a human on the server needs and what a model must not be handed.
    """
    action_request_id = uuid4()
    fixture, _ = firewall_context(
        monkeypatch,
        firewall=FakeFirewall(csf=csf_answer(noa_commented_line(action_request_id))),
    )

    result = await preflight(fixture)

    assert REASON_WRITTEN_OUT not in json.dumps(result)
    assert result["matches"] == [
        f"Found {TARGET} in /etc/csf/csf.allow ({NOA_COMMENT_MARKER}{action_request_id}"
    ]
    # The verdict is read from the full parse, before anything is cut.
    assert result["combined_verdict"] == VERDICT_ALLOWLISTED


async def test_a_line_noa_did_not_write_is_kept_intact(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The cut's negative control, and the reason it is marker-based rather than positional.

    LFD's own block reason and Imunify's `smtpauth brute force` are the evidence this tool exists
    to show. A rule that cut the tail off every line would take them, and the operator would be
    told "this address is blocked" with nothing about why — which is the state DECISIONS
    section 6.5's
    "block reason + log evidence" wording exists to prevent.
    """
    lfd_line = f"lfd: ({TARGET}) smtpauth brute force detected, blocking"
    fixture, _ = firewall_context(
        monkeypatch,
        firewall=both_backends(
            csf=csf_answer(f"{CSF_DENY_LINE}\n{lfd_line}"), imunify=imunify_answer(IMUNIFY_DROP)
        ),
    )

    result = await preflight(fixture)

    assert lfd_line in result["matches"]
    assert CSF_DENY_LINE in result["matches"]
    assert "smtpauth brute force" in json.dumps(result)


async def test_the_cut_survives_a_reason_that_looks_like_a_delimiter(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Why the cut runs to the end of the line rather than to a closing bracket.

    An operator types prose, and prose contains brackets and quotes. A rule that stopped at the
    first `)` would leave the tail of this reason in the transcript — which is the whole leak,
    just shorter.
    """
    action_request_id = uuid4()
    awkward = "ticket (NOC-4471) — customer 'acme' confirmed"
    fixture, _ = firewall_context(
        monkeypatch,
        firewall=FakeFirewall(
            csf=csf_answer(noa_commented_line(action_request_id, reason=awkward))
        ),
    )

    result = await preflight(fixture)

    assert "NOC-4471" not in json.dumps(result)
    assert "acme" not in json.dumps(result)
    assert str(action_request_id) in json.dumps(result)


# --- Nothing here is credential material ---


async def test_the_result_carries_no_credential_material(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Neither the ciphertext in the column nor the plaintext behind it.

    Against the whole serialized payload rather than key by key: what must hold is that the
    values appear nowhere, however they are nested. The evidence lines come from the remote
    host's own output, which is exactly the kind of text that could carry one through.
    """
    fixture, _ = firewall_context(
        monkeypatch,
        firewall=both_backends(csf=csf_answer(CSF_DENY_LINE), imunify=imunify_answer(IMUNIFY_DROP)),
    )

    result = await preflight(fixture)
    serialized = json.dumps(result, default=str)

    for secret in (*SECRETS, SSH_PASSWORD_PLAINTEXT, SSH_PRIVATE_KEY_PLAINTEXT):
        assert secret not in serialized


async def test_the_result_carries_no_raw_command_output(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """DECISIONS section 6.5: the `csf.deny` log line, never a raw iptables dump.

    `noa-old` shipped `raw_output` and `raw_data` beside the parsed verdict, and the result
    persists in LibreChat's MongoDB. The table row is gone from `matches` too, not only the
    `raw_output` key: the `csf.deny` line already names the entry it restates.
    """
    table_dump = (
        "Table  Chain            num   pkts bytes target     prot opt in     out\n"
        f"filter DENYIN           69       0     0 DROP       all  --  ens192 *  {TARGET}\n"
        f"{CSF_DENY_LINE}"
    )
    fixture, _ = firewall_context(monkeypatch, firewall=both_backends(csf=csf_answer(table_dump)))

    result = await preflight(fixture)

    assert set(result) == {
        "ok",
        "server_id",
        "target",
        "target_kind",
        "available_backends",
        "sudo_required",
        "combined_verdict",
        "unanswered_backends",
        "matches",
        "total_matches",
        "truncated",
        "csf",
        "imunify",
    }
    assert "raw_output" not in json.dumps(result)
    assert result["matches"] == [CSF_DENY_LINE]
    assert result["total_matches"] == 1


# --- An exception is a named failure ---


class ExplodingWHMServerRepository:
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
async def test_an_exception_reaches_the_caller_as_a_named_failure(  # type: ignore[no-untyped-def]
    monkeypatch, error: BaseException, expected_code: str, expected_message: str
) -> None:
    """The two sanitized mappings, and the original text never travels."""
    fixture, _ = firewall_context(monkeypatch)
    context = replace(
        fixture.context,
        whm_server_repository_factory=lambda _session: ExplodingWHMServerRepository(error),
    )

    result = await whm_preflight_firewall_entries(
        server_ref=SERVER_NAME, target=TARGET, context=context
    )

    assert result == {"ok": False, "error_code": expected_code, "message": expected_message}
    assert "10.0.0.5" not in json.dumps(result)


async def test_cancellation_is_not_swallowed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A cancelled request has no caller left to answer; swallowing it hangs a shutdown."""
    fixture, _ = firewall_context(monkeypatch)
    context = replace(
        fixture.context,
        whm_server_repository_factory=lambda _session: ExplodingWHMServerRepository(
            asyncio.CancelledError()
        ),
    )

    with pytest.raises(asyncio.CancelledError):
        await whm_preflight_firewall_entries(server_ref=SERVER_NAME, target=TARGET, context=context)


# --- The tool ships with its gate ---


def test_the_tool_name_matches_the_catalog() -> None:
    """The registered name is the one RBAC grants are written against."""
    assert TOOL_WHM_PREFLIGHT_FIREWALL_ENTRIES in TOOL_CATALOG


async def test_the_schema_takes_a_server_and_a_target_and_no_reason() -> None:
    """A READ tool has no reason either, and a schema is where one would appear."""
    server = build_mcp_server(tool_context=build_tool_context().context)
    tools = {tool.name: tool for tool in await server.list_tools(run_middleware=False)}

    schema = tools[TOOL_WHM_PREFLIGHT_FIREWALL_ENTRIES].parameters

    assert set(schema["properties"]) == {"server_ref", "target"}
    assert sorted(schema["required"]) == ["server_ref", "target"]
