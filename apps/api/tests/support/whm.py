"""Doubles for the WHM integration layer (T16).

What is left here is the WHM-specific part: a `whm_servers`-shaped row. Everything else these
tests reach for is shared, and lives where its name is honest (V66, the same reason
`support/auth.py` exists):

- `build_cipher` → `support/secrets.py`, once T17 needed it too.
- the SSH transport doubles (`ssh_config`, `command_result`, `FakeSSH`, `install_fake_ssh_exec`,
  the fingerprint/password/sudo constants) → `support/remote_exec.py`, once T18 needed them.

Both are re-exported below, because four test files already import them from this name.

No live host and no live WHM: `FakeWHMServer` satisfies the `WHMServerSecretLike` protocol
structurally, and `install_fake_ssh_exec` replaces the transport at the module boundary. The
layer under test is command composition, output parsing and failure classification — none of
which needs a socket.
"""

from __future__ import annotations

from dataclasses import dataclass

from support.remote_exec import (
    PINNED_FINGERPRINT,
    SSH_PASSWORD,
    SUDO_DENIED_STDERR,
    SUDO_MISSING_BINARY_STDERR,
    FakeSSH,
    RecordedRun,
    command_result,
    install_fake_ssh_exec,
    ssh_config,
)
from support.secrets import build_cipher


@dataclass
class FakeWHMServer:
    """A `whm_servers` row, structurally satisfying `WHMServerSecretLike`.

    Defaults describe a *usable* server — pinned, credentialed, root — so each test overrides
    only the one field it is about.
    """

    base_url: str = "https://whm.example.com:2087"
    api_username: str = "root"
    api_token: str = "whm-api-token"
    verify_ssl: bool = True
    ssh_username: str | None = None
    ssh_port: int | None = None
    ssh_password: str | None = SSH_PASSWORD
    ssh_private_key: str | None = None
    ssh_private_key_passphrase: str | None = None
    ssh_host_key_fingerprint: str | None = PINNED_FINGERPRINT


__all__ = [
    "PINNED_FINGERPRINT",
    "SSH_PASSWORD",
    "SUDO_DENIED_STDERR",
    "SUDO_MISSING_BINARY_STDERR",
    "FakeSSH",
    "FakeWHMServer",
    "RecordedRun",
    "build_cipher",
    "command_result",
    "install_fake_ssh_exec",
    "ssh_config",
]
