"""Doubles for the PMG integration layer and the PMG tool path.

`FakePMGServer` is a `PMGServerSecretLike`-shaped dataclass, for the layer that turns a row into
an `SSHConnectionConfig`. Its credential defaults are plaintext, because those tests are about
`resolve_pmg_ssh_config`'s decisions and not about the cipher.

The tool path needs a *real* `core.db.models.PMGServer`, and that lives in `support/servers.py`
with its WHM twin — the resolver's UUID branch needs a real id, and the inventory doubles for
both systems are one subject. This module wraps it: `whitelist_server` is the row a tool
actually connects with, and `whitelist_context` is the PMG twin of `support/whm_firewall.py`'s
`firewall_context`. Here rather than in a module of its own because PMG's whole tool path
crosses exactly one module that binds `ssh_exec` — the three-module install WHM's firewall needs
has no PMG counterpart.

The SSH transport doubles are shared and sit in `support/remote_exec.py`; the cipher is in
`support/secrets.py`.

`FakePMGWhitelist` is the pmg-whitelist tool's addition and the one double here that is not a
constant answer. It holds `mynetworks` as **state**: a `create` appends and a `delete` removes, so
the `ls` the runner takes afterwards returns what the runner actually wrote. That is what makes the
postflight worth running — a fixture replaying a canned "it is there now" would pass with the whole
write-then-verify path deleted — and it is what lets a removal's *bytes* be asserted rather than its
return value.

No live host and no live PMG: everything under test is reference resolution, command
composition, `mynetworks` parsing and refusal shaping.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest

import core.integrations.pmg.pmgsh_cli as pmgsh_cli_mod
from core.approvals.execution import ChangeExecutionRequest
from core.db.models import PMGServer
from core.integrations.pmg.mynetworks import normalize_cidr
from core.integrations.pmg.pmgsh_cli import MYNETWORKS_PATH
from core.remote_exec.types import CommandResult
from core.secrets.crypto import SecretCipher
from noa_api.mcp_tools.pmg_whitelist import (
    ACTION_ADD,
    EVIDENCE_ACTION,
    EVIDENCE_ENDPOINT,
    EVIDENCE_MATCHES,
    EVIDENCE_NORMALIZED_TARGET,
    EVIDENCE_SERVER_ID,
    EVIDENCE_SERVER_NAME,
    EVIDENCE_TARGET,
    EVIDENCE_TOTAL_ENTRIES,
    TOOL_PMG_WHITELIST,
    pmg_whitelist,
)
from support.action_decisions import REASON
from support.mcp_identity import authenticated_caller, http_request_context
from support.remote_exec import (
    PINNED_FINGERPRINT,
    SSH_PASSWORD,
    FakeSSH,
    command_result,
    install_fake_ssh_exec,
)
from support.result_tables import FakeToolResultTableWriter
from support.secrets import build_cipher
from support.servers import ToolFixture, build_tool_context, pmg_server

PMG_HOST = "pmg.example.com"

SERVER_NAME = "pmg1"

# The plaintexts behind a `whitelist_server` row's encrypted columns. That helper encrypts them,
# so an assertion on the config the transport saw proves a decrypt ran rather than a passthrough.
SSH_PASSWORD_PLAINTEXT = "operator-ssh-password"
SSH_PRIVATE_KEY_PLAINTEXT = "-----BEGIN OPENSSH PRIVATE KEY-----"


@dataclass
class FakePMGServer:
    """A `pmg_servers` row, structurally satisfying `PMGServerSecretLike`.

    Defaults describe a *usable* server — pinned, credentialed, root — so each test overrides
    only the one field it is about. No `base_url` and no `verify_ssl`: PMG is SSH-only, and
    `core.db.models.PMGServer` carries neither.
    """

    ssh_host: str = PMG_HOST
    ssh_username: str | None = None
    ssh_port: int | None = None
    ssh_password: str | None = SSH_PASSWORD
    ssh_private_key: str | None = None
    ssh_private_key_passphrase: str | None = None
    ssh_host_key_fingerprint: str | None = PINNED_FINGERPRINT


def whitelist_server(name: str, *, cipher: SecretCipher, **columns: Any) -> PMGServer:
    """A `pmg_servers` row whose SSH credentials really decrypt under `cipher`.

    `support.servers.pmg_server`'s defaults are ciphertext-*shaped* and do not decrypt: they
    exist to be asserted absent from a result. A row a tool actually connects with has to carry
    the real thing, so the one decrypt site runs instead of being assumed.
    """
    return pmg_server(
        name,
        ssh_password=cipher.encrypt_text(SSH_PASSWORD_PLAINTEXT),
        ssh_private_key=cipher.encrypt_text(SSH_PRIVATE_KEY_PLAINTEXT),
        **columns,
    )


def mynetworks_output(*cidrs: str) -> str:
    """What `pmgsh ls /config/mynetworks` prints, status line and header included.

    The shape `noa-old`'s tests used, kept because the parser's whole job is finding entries in
    it: a fixture of bare CIDRs would never exercise the lines that must produce none.
    """
    lines = ["200 OK", "id cidr"]
    lines.extend(f"{index} {cidr}" for index, cidr in enumerate(cidrs, start=1))
    return "\n".join(lines)


def whitelist_context(
    monkeypatch: pytest.MonkeyPatch,
    *,
    answer: CommandResult | None = None,
    handler: Callable[[str], CommandResult] | None = None,
    servers: list[PMGServer] | None = None,
    ssh_username: str | None = None,
    cipher: SecretCipher | None = None,
    result_tables: FakeToolResultTableWriter | None = None,
) -> tuple[ToolFixture, FakeSSH]:
    """A tool context whose PMG node is reachable only through one recorded `ssh_exec`.

    `ssh_username=None` resolves to `root`, which is the plain command shape; the escalation
    case passes one. A test supplying its own `servers` passes the `cipher` it encrypted them
    with, or the fixture would hold a key those rows do not open under.

    `answer` is one canned reply to every command, which is all a READ needs — those tools send
    exactly one. `handler` is the pmg-whitelist tool's seam: a CHANGE sends `ls`, then a mutation,
    then `pmgconfig sync`, then `ls` again, and each has to answer differently, so a
    `FakePMGWhitelist` dispatches on the command instead. One builder for both, because the
    resolution, the cipher and the row are the same either way.

    `result_tables` is `pmg_whitelist_list`'s seam: the default double records what was
    parked, and a test passes its own to make the insert fail. The fixture always has one, so
    the listing tool works here without any test asking for it.
    """
    resolved_cipher = cipher or build_cipher()
    rows = (
        servers
        if servers is not None
        else [whitelist_server(SERVER_NAME, cipher=resolved_cipher, ssh_username=ssh_username)]
    )
    fixture = build_tool_context(
        pmg_servers=rows, cipher=resolved_cipher, result_tables=result_tables
    )
    if handler is None:
        reply = answer if answer is not None else command_result(stdout=mynetworks_output())

        def handler(_command: str) -> CommandResult:
            return reply

    fake = install_fake_ssh_exec(monkeypatch, pmgsh_cli_mod, handler)
    return fixture, fake


# --- The pmg-whitelist tool: a whitelist a change can actually move ---

# The address the CHANGE tests ask about, and what `normalize_cidr` makes of it. A bare host, so the
# two spellings differ — which is the case the exact-membership rule is about and the one a fixture
# storing only `/32` would hide.
TARGET = "203.0.113.10"
TARGET_NORMALIZED = "203.0.113.10/32"

# One unrelated line, so an assertion that "the entry is gone" is not also satisfied by a fixture
# that lost everything.
BYSTANDER = "10.10.10.0/24"

# `KEY=value` as `build_remote_command` emits it, ahead of any `sudo`.
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


@dataclass
class FakePMGWhitelist:
    """One PMG node's `mynetworks`, as state, with a failure knob per step.

    The defaults describe a node whose whitelist holds one unrelated network — the shape of an
    `add` that has work to do. Each knob turns exactly one command into a failure, so a test names
    the branch it is about instead of arranging a whole broken box.

    `entries` are PMG's own spellings in PMG's own order, because that is what `pmgsh ls` prints
    and what a removal has to name. Nothing here normalises: normalisation is the code under test.
    """

    entries: list[str] = field(default_factory=lambda: [BYSTANDER])

    # --- failure knobs, one per step ---
    list_error: CommandResult | None = None
    create_error: CommandResult | None = None
    delete_error: CommandResult | None = None
    sync_error: CommandResult | None = None
    # Every `ls` taken after the first mutation fails — the postflight-unavailable branch, with
    # the preflight left readable so the change still happens.
    fail_reads_after_write: bool = False
    # Answer `200 OK` and change nothing: the box that accepted a write and did not move. This is
    # the asking-the-CHANGE's-own-question case — the command says yes, the list says no.
    ignore_writes: bool = False

    commands: list[str] = field(default_factory=list)
    _writes: int = 0

    # --- the transport ---

    def handle(self, command: str) -> CommandResult:
        """Answer one composed command. Passed to `whitelist_context(handler=...)`."""
        self.commands.append(command)
        argv = pmg_argv(command)
        if not argv:
            return command_result(command=command, exit_code=1, stderr="no command")
        if argv[0] == "pmgconfig":
            return self.sync_error or command_result(command=command, stdout="200 OK")
        subcommand = argv[1] if len(argv) > 1 else ""
        if subcommand == "ls":
            return self._list(command)
        if subcommand == "create":
            return self._create(command, argv)
        if subcommand == "delete":
            return self._delete(command, argv)
        return command_result(command=command, exit_code=1, stderr="unrecognised command")

    # --- what a test reads back ---

    @property
    def mutations(self) -> list[str]:
        """Every command that was not a read. Empty is the "opened a question" assert."""
        return [command for command in self.commands if not _is_read(command)]

    @property
    def created(self) -> list[str]:
        """The `-cidr` value of each `create`, as it went on the wire."""
        return [pmg_argv(c)[4] for c in self.commands if command_step(c) == "create"]

    @property
    def deleted_paths(self) -> list[str]:
        """The path of each `delete`, as it went on the wire.

        The *path*, not a CIDR pulled out of it: which spelling the runner sent is exactly what
        this fixture exists to record, so it is read off the bytes rather than re-derived.
        """
        return [pmg_argv(c)[2] for c in self.commands if command_step(c) == "delete"]

    @property
    def synced(self) -> int:
        """How many times `pmgconfig sync` ran. A mutation that skipped it looks applied."""
        return len([c for c in self.commands if command_step(c) == "sync"])

    # --- state ---

    def _list(self, command: str) -> CommandResult:
        if self.fail_reads_after_write and self._writes:
            return command_result(command=command, exit_code=1, stderr="pmgsh: node unreachable")
        if self.list_error is not None:
            return self.list_error
        return command_result(command=command, stdout=mynetworks_output(*self.entries))

    def _create(self, command: str, argv: list[str]) -> CommandResult:
        if self.create_error is not None:
            return self.create_error
        self._writes += 1
        if not self.ignore_writes:
            self.entries.append(argv[4])
        return command_result(command=command, stdout="200 OK")

    def _delete(self, command: str, argv: list[str]) -> CommandResult:
        if self.delete_error is not None:
            return self.delete_error
        self._writes += 1
        if not self.ignore_writes:
            removed = argv[2].removeprefix(f"{MYNETWORKS_PATH}/")
            self.entries = [entry for entry in self.entries if entry != removed]
        return command_result(command=command, stdout="200 OK")


def pmg_argv(command: str) -> list[str]:
    """A composed command back as argv, with the environment and `sudo -n` stripped.

    `shlex.split` rather than a substring match, because the whole point of the argv-safe `pmgsh`
    rule is that a CIDR stays one token: a fixture matching on text would pass for a command that
    had split it.
    """
    tokens = shlex.split(command)
    while tokens and _ENV_ASSIGNMENT.match(tokens[0]):
        tokens = tokens[1:]
    if tokens and tokens[0] == "sudo":
        tokens = tokens[1:]
        while tokens and tokens[0].startswith("-"):
            tokens = tokens[1:]
    if tokens:
        # `/usr/bin/pmgsh` and `pmgconfig` are one absolute and one bare name by design
        # (`core.integrations.pmg.pmgsh_cli`), so the fixture compares on the basename.
        tokens = [tokens[0].rsplit("/", 1)[-1], *tokens[1:]]
    return tokens


def command_step(command: str) -> str:
    """Which step of the flow one composed command is: `ls`, `create`, `delete` or `sync`.

    Public because both of the pmg-whitelist tool's lanes assert on the *sequence* — a mutation that
    skipped `pmgconfig sync` looks applied and is not — and two spellings of "which step is this"
    are two chances to read the same command list differently.
    """
    argv = pmg_argv(command)
    if argv[:1] == ["pmgconfig"]:
        return "sync"
    return argv[1] if len(argv) > 1 else ""


def _is_read(command: str) -> bool:
    return command_step(command) == "ls"


def whitelist_change_context(
    monkeypatch: pytest.MonkeyPatch,
    *,
    box: FakePMGWhitelist | None = None,
    servers: list[PMGServer] | None = None,
    ssh_username: str | None = None,
    cipher: SecretCipher | None = None,
) -> tuple[ToolFixture, FakePMGWhitelist]:
    """A tool context whose PMG node is a `FakePMGWhitelist`, and that node.

    Shared by both of the pmg-whitelist tool's lanes — the tool that opens the card and the runner
    that runs after one is approved — because they need the same row, the same cipher and the same
    transport seam, and two copies of this wiring are two things that can stop agreeing.
    """
    resolved = box if box is not None else FakePMGWhitelist()
    fixture, _ = whitelist_context(
        monkeypatch,
        handler=resolved.handle,
        servers=servers,
        ssh_username=ssh_username,
        cipher=cipher,
    )
    return fixture, resolved


async def call_whitelist(
    fixture: ToolFixture,
    *,
    server_ref: str = SERVER_NAME,
    action: str = ACTION_ADD,
    target: str = TARGET,
) -> tuple[Any, UUID]:
    """Call the tool inside a real request context; return its answer and the caller's id.

    The context is not decoration: `open_change_request` reads the requester from the
    authenticated identity rather than from an argument, so a call outside it would be
    asserting against an identity the test planted.
    """
    user, resolved = authenticated_caller()
    with http_request_context({}, user=user):
        answer = await pmg_whitelist(
            server_ref=server_ref, action=action, target=target, context=fixture.context
        )
    return answer, resolved


def execution_request(
    *,
    server_id: UUID | str,
    action: Any = ACTION_ADD,
    target: Any = TARGET,
    normalized_target: Any = None,
    matches: list[dict[str, str]] | None = None,
    reason: str = REASON,
    action_request_id: UUID | None = None,
    server_ref: str = "some-other-node",
) -> ChangeExecutionRequest:
    """What `core.approvals.execution` hands a runner for an approved whitelist change.

    The evidence is what the gate wrote and what the operator saw; the arguments deliberately name
    a `server_ref` the runner must ignore, so every runner test that resolves a target is also a
    context-persisted-at-gate-time assertion.
    """
    resolved = (
        normalized_target
        if normalized_target is not None
        else (normalize_cidr(str(target)) or TARGET_NORMALIZED)
    )
    return ChangeExecutionRequest(
        action_request_id=action_request_id or uuid4(),
        tool_run_id=uuid4(),
        tool_name=TOOL_PMG_WHITELIST,
        arguments={"server_ref": server_ref, "action": action, "target": target},
        evidence={
            EVIDENCE_SERVER_ID: str(server_id),
            EVIDENCE_SERVER_NAME: SERVER_NAME,
            EVIDENCE_ACTION: action,
            EVIDENCE_TARGET: target,
            EVIDENCE_NORMALIZED_TARGET: resolved,
            EVIDENCE_MATCHES: matches if matches is not None else [],
            EVIDENCE_TOTAL_ENTRIES: 1,
            EVIDENCE_ENDPOINT: MYNETWORKS_PATH,
        },
        reason=reason,
    )


def payload_text(payload: Any) -> str:
    """Everything a payload would carry into an audit row, as one string.

    `default=str` for the reason `core.audit.summaries` uses it: this is asserted *against*, so a
    value that would not serialize must still show up rather than raise and leave the assertion
    unmade.
    """
    return json.dumps(payload, default=str)


__all__ = [
    "BYSTANDER",
    "PMG_HOST",
    "SERVER_NAME",
    "SSH_PASSWORD_PLAINTEXT",
    "SSH_PRIVATE_KEY_PLAINTEXT",
    "TARGET",
    "TARGET_NORMALIZED",
    "FakePMGServer",
    "FakePMGWhitelist",
    "call_whitelist",
    "command_step",
    "execution_request",
    "mynetworks_output",
    "payload_text",
    "pmg_argv",
    "whitelist_change_context",
    "whitelist_context",
    "whitelist_server",
]
