"""Doubles for the PMG integration layer and the PMG tool path (T18, T31).

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
`support/secrets.py` (V66).

No live host and no live PMG: everything under test is reference resolution, command
composition, `mynetworks` parsing and refusal shaping.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

import core.integrations.pmg.pmgsh_cli as pmgsh_cli_mod
from core.db.models import PMGServer
from core.remote_exec.types import CommandResult
from core.secrets.crypto import SecretCipher
from support.remote_exec import (
    PINNED_FINGERPRINT,
    SSH_PASSWORD,
    FakeSSH,
    command_result,
    install_fake_ssh_exec,
)
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
    only the one field it is about. No `base_url` and no `verify_ssl`: PMG is SSH-only (V58), and
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
    servers: list[PMGServer] | None = None,
    ssh_username: str | None = None,
    cipher: SecretCipher | None = None,
) -> tuple[ToolFixture, FakeSSH]:
    """A tool context whose PMG node is reachable only through one recorded `ssh_exec`.

    `ssh_username=None` resolves to `root`, which is the plain command shape; the escalation
    case passes one. A test supplying its own `servers` passes the `cipher` it encrypted them
    with, or the fixture would hold a key those rows do not open under.
    """
    resolved_cipher = cipher or build_cipher()
    rows = (
        servers
        if servers is not None
        else [whitelist_server(SERVER_NAME, cipher=resolved_cipher, ssh_username=ssh_username)]
    )
    fixture = build_tool_context(pmg_servers=rows, cipher=resolved_cipher)
    reply = answer if answer is not None else command_result(stdout=mynetworks_output())
    fake = install_fake_ssh_exec(monkeypatch, pmgsh_cli_mod, lambda _command: reply)
    return fixture, fake


__all__ = [
    "PMG_HOST",
    "SERVER_NAME",
    "SSH_PASSWORD_PLAINTEXT",
    "SSH_PRIVATE_KEY_PLAINTEXT",
    "FakePMGServer",
    "mynetworks_output",
    "whitelist_context",
    "whitelist_server",
]
