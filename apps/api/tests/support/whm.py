"""Doubles for the WHM integration layer.

What is left here is the WHM-specific part: a `whm_servers`-shaped row. Everything else these
tests reach for is shared, and lives where its name is honest (V66, the same reason
`support/auth.py` exists):

- `build_cipher` → `support/secrets.py`, once T17 needed it too.
- the SSH transport doubles (`ssh_config`, `command_result`, `FakeSSH`, `install_fake_ssh_exec`,
  the fingerprint/password/sudo constants) → `support/remote_exec.py`, once T18 needed them.

Both moves left a re-export here so the WHM test files could stay untouched; T72 removed them
and pointed those files at the real homes, so this module is not an alias hub. The two
constants imported below are used, not forwarded — `FakeWHMServer` takes them as field
defaults. `test_support_layout.py` holds that line.

No live host and no live WHM: `FakeWHMServer` satisfies the `WHMServerSecretLike` protocol
structurally, and `install_fake_ssh_exec` replaces the transport at the module boundary. The
layer under test is command composition, output parsing and failure classification — none of
which needs a socket.
"""

from __future__ import annotations

from dataclasses import dataclass

from support.remote_exec import PINNED_FINGERPRINT, SSH_PASSWORD


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


__all__ = ["FakeWHMServer"]
