"""Doubles for a WHM box's firewall, as the tool layer sees it.

A firewall tool call crosses three modules that each bound their own `ssh_exec` name
(`availability`, `csf_cli`, `imunify_cli`), so the double is installed in all three from one
recorder — that is what lets a test assert the whole command sequence rather than one hop of
it. `FIREWALL_SSH_MODULES` is the list, and it is here rather than in each test file because a
module missing from it means an unreplaced `ssh_exec` and a test that tries to open a socket.

Its own module rather than `support/whm.py`, which owns the server row and nothing else
(`test_support_layout.py` holds that line), and rather than `support/remote_exec.py`, which is
about the transport and knows nothing about csf. The release-and-allow tool is the second
caller; the allowlist-remove tool is the next.

No live host and no live firewall: everything under test is command composition, output
parsing, verdict combination and refusal shaping.

**Two boxes, because a CHANGE asks a backend more than one thing.** `FakeFirewall` answers
one query per backend and is what a READ needs. `FakeFirewallBox` dispatches on the
sub-command — csf's `-g` / `-tr` / `-dr` / `-ta`, Imunify's `list` / `delete` / `add` — and reads
come from a *queue*, because a CHANGE workflow reads the same backend twice and the whole point
of the second read is that it answers differently from the first. A double with one answer
cannot express that, and a test using one would pass against a postflight that never ran.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any

import pytest

import core.integrations.whm.availability as availability_mod
import core.integrations.whm.csf_cli as csf_cli_mod
import core.integrations.whm.imunify_cli as imunify_cli_mod
from core.db.models import WHMServer
from core.integrations.whm.csf_cli import CSF_BINARY
from core.remote_exec.types import CommandResult
from core.secrets.crypto import SecretCipher
from support.remote_exec import (
    SUDO_DENIED_STDERR,
    FakeSSH,
    command_result,
    install_fake_ssh_exec_in,
)
from support.secrets import build_cipher
from support.servers import ToolFixture, build_tool_context, whm_server

SERVER_NAME = "alpha"
TARGET = "203.0.113.10"

# The plaintexts behind the row's encrypted SSH columns. `preflight_server` encrypts them, so an
# assertion on the config the transport saw proves a decrypt ran rather than a passthrough.
SSH_PASSWORD_PLAINTEXT = "operator-ssh-password"
SSH_PRIVATE_KEY_PLAINTEXT = "-----BEGIN OPENSSH PRIVATE KEY-----"

# Every module that binds its own `ssh_exec` name on a firewall tool's path.
FIREWALL_SSH_MODULES = (availability_mod, csf_cli_mod, imunify_cli_mod)

# Real `csf -g` shapes. Verdicts are read from marker strings in human-facing text, so an
# invented fixture would test nothing.
CSF_DENY_LINE = f"Found {TARGET} in /etc/csf/csf.deny"
CSF_ALLOW_LINE = f"Found {TARGET} in /etc/csf/csf.allow"
CSF_CLEAN_OUTPUT = f"No matches found for {TARGET} in iptables"
CSF_UNREADABLE_OUTPUT = f"{TARGET} appears somewhere we have no marker for"


def imunify_body(*items: dict[str, Any]) -> str:
    """An `ip-list local list --by-ip … --json` document."""
    return json.dumps({"items": list(items), "counts": {}})


IMUNIFY_CLEAN = imunify_body()
IMUNIFY_DROP = imunify_body({"ip": TARGET, "purpose": "drop", "comment": "smtpauth brute force"})
IMUNIFY_WHITE = imunify_body({"ip": TARGET, "purpose": "white", "comment": "office"})


def csf_answer(
    output: str = CSF_CLEAN_OUTPUT, *, exit_code: int = 0, stderr: str = ""
) -> CommandResult:
    """What `csf -g <target>` prints back."""
    return command_result(exit_code=exit_code, stdout=output, stderr=stderr)


def imunify_answer(
    body: str = IMUNIFY_CLEAN, *, exit_code: int = 0, stderr: str = ""
) -> CommandResult:
    """What `imunify360-agent ip-list … --json` prints back."""
    return command_result(exit_code=exit_code, stdout=body, stderr=stderr)


def is_probe(command: str) -> bool:
    """Availability probes: `command -v <bin>` as root, `<bin> -v` / `<bin> version` under sudo."""
    return "command -v" in command or command.endswith((" -v", " version"))


def is_query(command: str) -> bool:
    """The commands that ask about a target, as opposed to asking whether a binary runs."""
    return not is_probe(command)


class FakeFirewall:
    """One WHM box's answers to the commands a firewall tool can send.

    `csf=None` / `imunify=None` means the binary is not there: its probe fails, and the tool
    must then never send a query — the assertion below turns "queried an unavailable backend"
    into a failure rather than a silently absent result. `sudo_denied` makes the probes fail the
    way a missing sudoers entry does: present, but not runnable.
    """

    def __init__(
        self,
        *,
        csf: CommandResult | None = None,
        imunify: CommandResult | None = None,
        sudo_denied: bool = False,
    ) -> None:
        self.csf = csf
        self.imunify = imunify
        self.sudo_denied = sudo_denied

    def __call__(self, command: str) -> CommandResult:
        answer = self.csf if CSF_BINARY in command else self.imunify
        if is_query(command):
            assert answer is not None, f"queried an unavailable backend: {command}"
            return replace(answer, command=command)
        if self.sudo_denied:
            return command_result(command=command, exit_code=1, stderr=SUDO_DENIED_STDERR)
        if answer is None:
            return command_result(command=command, exit_code=127, stderr="command not found")
        return command_result(command=command, exit_code=0, stdout="ok")


def both_backends(
    *, csf: CommandResult | None = None, imunify: CommandResult | None = None
) -> FakeFirewall:
    """A box with both backends installed and answering. The common case."""
    return FakeFirewall(csf=csf or csf_answer(), imunify=imunify or imunify_answer())


# --- A box that also accepts changes ---

# csf's own sub-commands, as `noa-old` sends them and as the CHANGE tools still do. The last two
# are the allowlist-remove tool's: `-tra` drops a temporary allow and `-ar` the `csf.allow`
# entry, which are the two lists a release can have written to.
CSF_READ = "-g"
CSF_TEMP_RELEASE = "-tr"
CSF_DENY_RELEASE = "-dr"
CSF_TEMP_ALLOW = "-ta"
CSF_TEMP_ALLOW_REMOVE = "-tra"
CSF_ALLOW_REMOVE = "-ar"

# Imunify's, read off the `ip-list local <step>` argument.
IMUNIFY_READ = "list"
IMUNIFY_DELETE = "delete"
IMUNIFY_ADD = "add"

# What each backend prints back for a mutation it accepted. csf answers in text and Imunify in
# JSON, because that is what `require_csf_success` and `parse_imunify_json_output` are handed.
CSF_MUTATION_OK = "csf: done"
IMUNIFY_MUTATION_OK = json.dumps({"result": "success"})

# What csf prints when the entry a release names is not in that list. **Exit 1**, and it is the
# ordinary case rather than a failure: an address held by a temporary ban has no `csf.deny` line.
CSF_NOT_IN_LIST = "csf: 203.0.113.10 not found in /etc/csf/csf.deny"

# A `csf -g` line for an address that is on the allow list *and* the deny list. The verdict resolves
# block-first and therefore reports `blocked`, which is why the allowlist-remove tool asks
# `allow_entry` instead — this fixture is what separates the two readings.
CSF_ALLOW_AND_DENY_OUTPUT = f"{CSF_ALLOW_LINE}\n{CSF_DENY_LINE}"

# An Imunify document holding both purposes for one address, for the same reason.
IMUNIFY_WHITE_AND_DROP = imunify_body(
    {"ip": TARGET, "purpose": "white", "comment": "office"},
    {"ip": TARGET, "purpose": "drop", "comment": "smtpauth brute force"},
)

# Imunify's own refusal for a delete of an entry it does not hold. Also ordinary.
IMUNIFY_NOT_IN_LIST = json.dumps({"errors": ["IP is not in the list"]})


def csf_step(command: str) -> str | None:
    """Which csf sub-command a command string carries, or `None` when it is a probe."""
    if CSF_BINARY not in command or is_probe(command):
        return None
    tokens = command.split()
    index = next(i for i, token in enumerate(tokens) if token.endswith(CSF_BINARY))
    return tokens[index + 1]


def imunify_step(command: str) -> str | None:
    """Which `ip-list local <step>` a command string carries, or `None` when it is a probe."""
    if CSF_BINARY in command or is_probe(command):
        return None
    tokens = command.split()
    return tokens[tokens.index("local") + 1]


@dataclass
class BackendScript:
    """One backend's answers: a queue of reads, and one answer per mutation step.

    `reads` is consumed in order and its **last entry repeats**, so a script says "blocked, then
    allowlisted" in two entries and "always clean" in one — without a test having to count how
    many times the code under test reads.

    A mutation step with no scripted answer succeeds. That is the right default because the
    interesting mutation cases are the failures, and spelling out three successes to reach one
    failure is how a test stops saying what it is about.
    """

    reads: list[CommandResult]
    mutations: dict[str, CommandResult] = field(default_factory=dict)
    default_mutation: CommandResult = field(default_factory=lambda: command_result(stdout="ok"))

    def read(self) -> CommandResult:
        return self.reads.pop(0) if len(self.reads) > 1 else self.reads[0]

    def mutation(self, step: str) -> CommandResult:
        return self.mutations.get(step, self.default_mutation)


def csf_backend(
    *reads: CommandResult, mutations: dict[str, CommandResult] | None = None
) -> BackendScript:
    """A CSF that answers `-g` from `reads` and every mutation successfully unless scripted."""
    return BackendScript(
        reads=list(reads) or [csf_answer()],
        mutations=mutations or {},
        default_mutation=command_result(stdout=CSF_MUTATION_OK),
    )


def imunify_backend(
    *reads: CommandResult, mutations: dict[str, CommandResult] | None = None
) -> BackendScript:
    """An Imunify that answers `list` from `reads` and every mutation successfully."""
    return BackendScript(
        reads=list(reads) or [imunify_answer()],
        mutations=mutations or {},
        default_mutation=command_result(stdout=IMUNIFY_MUTATION_OK),
    )


class FakeFirewallBox:
    """One WHM box's answers to every command a firewall CHANGE tool can send.

    `csf=None` / `imunify=None` means the binary is not there: its probe fails, and the tool must
    then never send it anything — the assertion below turns "drove an unavailable backend" into a
    failure rather than a silently absent result. `sudo_denied` makes the probes fail the way a
    missing sudoers entry does: present, but not runnable.
    """

    def __init__(
        self,
        *,
        csf: BackendScript | None = None,
        imunify: BackendScript | None = None,
        sudo_denied: bool = False,
    ) -> None:
        self.csf = csf
        self.imunify = imunify
        self.sudo_denied = sudo_denied

    def __call__(self, command: str) -> CommandResult:
        script = self.csf if CSF_BINARY in command else self.imunify
        if is_probe(command):
            if self.sudo_denied:
                return command_result(command=command, exit_code=1, stderr=SUDO_DENIED_STDERR)
            if script is None:
                return command_result(command=command, exit_code=127, stderr="command not found")
            return command_result(command=command, exit_code=0, stdout="ok")

        assert script is not None, f"drove an unavailable backend: {command}"
        step = csf_step(command) if CSF_BINARY in command else imunify_step(command)
        answer = script.read() if step in (CSF_READ, IMUNIFY_READ) else script.mutation(str(step))
        return replace(answer, command=command)


def working_box(
    *,
    csf: BackendScript | None = None,
    imunify: BackendScript | None = None,
) -> FakeFirewallBox:
    """A box with both backends installed and accepting changes. The common case."""
    return FakeFirewallBox(csf=csf or csf_backend(), imunify=imunify or imunify_backend())


def preflight_server(name: str, *, cipher: SecretCipher, **columns: Any) -> WHMServer:
    """A `whm_servers` row whose SSH credentials really decrypt under `cipher`.

    `support.servers.whm_server`'s defaults are ciphertext-*shaped* and do not decrypt: they
    exist to be asserted absent from a result. A row a tool actually connects with has to carry
    the real thing, so the decrypt site runs instead of being assumed.
    """
    return whm_server(
        name,
        ssh_password=cipher.encrypt_text(SSH_PASSWORD_PLAINTEXT),
        ssh_private_key=cipher.encrypt_text(SSH_PRIVATE_KEY_PLAINTEXT),
        **columns,
    )


def firewall_context(
    monkeypatch: pytest.MonkeyPatch,
    *,
    firewall: FakeFirewall | FakeFirewallBox | None = None,
    servers: list[Any] | None = None,
    ssh_username: str | None = None,
    cipher: SecretCipher | None = None,
) -> tuple[ToolFixture, FakeSSH]:
    """A tool context whose WHM server is reachable only through one recorded `ssh_exec`.

    `ssh_username=None` resolves to `root`, which is the plain command shape; the escalation
    case passes one. A test supplying its own `servers` passes the `cipher` it encrypted them
    with, or the fixture would hold a key those rows do not open under.
    """
    resolved_cipher = cipher or build_cipher()
    rows = (
        servers
        if servers is not None
        else [preflight_server(SERVER_NAME, cipher=resolved_cipher, ssh_username=ssh_username)]
    )
    fixture = build_tool_context(servers=rows, cipher=resolved_cipher)
    fake = install_fake_ssh_exec_in(monkeypatch, FIREWALL_SSH_MODULES, firewall or both_backends())
    return fixture, fake


__all__ = [
    "CSF_ALLOW_AND_DENY_OUTPUT",
    "CSF_ALLOW_LINE",
    "CSF_ALLOW_REMOVE",
    "CSF_CLEAN_OUTPUT",
    "CSF_DENY_LINE",
    "CSF_DENY_RELEASE",
    "CSF_MUTATION_OK",
    "CSF_NOT_IN_LIST",
    "CSF_READ",
    "CSF_TEMP_ALLOW",
    "CSF_TEMP_ALLOW_REMOVE",
    "CSF_TEMP_RELEASE",
    "CSF_UNREADABLE_OUTPUT",
    "FIREWALL_SSH_MODULES",
    "IMUNIFY_ADD",
    "IMUNIFY_CLEAN",
    "IMUNIFY_DELETE",
    "IMUNIFY_DROP",
    "IMUNIFY_MUTATION_OK",
    "IMUNIFY_NOT_IN_LIST",
    "IMUNIFY_READ",
    "IMUNIFY_WHITE",
    "IMUNIFY_WHITE_AND_DROP",
    "SERVER_NAME",
    "SSH_PASSWORD_PLAINTEXT",
    "SSH_PRIVATE_KEY_PLAINTEXT",
    "TARGET",
    "BackendScript",
    "FakeFirewall",
    "FakeFirewallBox",
    "both_backends",
    "csf_answer",
    "csf_backend",
    "csf_step",
    "firewall_context",
    "imunify_answer",
    "imunify_backend",
    "imunify_body",
    "imunify_step",
    "is_probe",
    "is_query",
    "preflight_server",
    "working_box",
]
