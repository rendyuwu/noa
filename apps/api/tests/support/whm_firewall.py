"""Doubles for a WHM box's firewall, as the tool layer sees it (T24).

A firewall tool call crosses three modules that each bound their own `ssh_exec` name
(`availability`, `csf_cli`, `imunify_cli`), so the double is installed in all three from one
recorder — that is what lets a test assert the whole command sequence rather than one hop of
it. `FIREWALL_SSH_MODULES` is the list, and it is here rather than in each test file because a
module missing from it means an unreplaced `ssh_exec` and a test that tries to open a socket.

Its own module rather than `support/whm.py`, which owns the server row and nothing else
(`test_support_layout.py` holds that line), and rather than `support/remote_exec.py`, which is
about the transport and knows nothing about csf. T25 and T26 are the next callers.

No live host and no live firewall: everything under test is command composition, output
parsing, verdict combination and refusal shaping.
"""

from __future__ import annotations

import json
from dataclasses import replace
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
    firewall: FakeFirewall | None = None,
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
    "CSF_ALLOW_LINE",
    "CSF_CLEAN_OUTPUT",
    "CSF_DENY_LINE",
    "CSF_UNREADABLE_OUTPUT",
    "FIREWALL_SSH_MODULES",
    "IMUNIFY_CLEAN",
    "IMUNIFY_DROP",
    "IMUNIFY_WHITE",
    "SERVER_NAME",
    "SSH_PASSWORD_PLAINTEXT",
    "SSH_PRIVATE_KEY_PLAINTEXT",
    "TARGET",
    "FakeFirewall",
    "both_backends",
    "csf_answer",
    "firewall_context",
    "imunify_answer",
    "imunify_body",
    "is_probe",
    "is_query",
    "preflight_server",
]
