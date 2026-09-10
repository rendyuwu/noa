"""WHM server → SSH connection config.

Copied from `noa-old` branch `MCP` (`whm/integrations/ssh.py`), minus the half already
hoisted: `SSH_SUDO_REQUIRED_CODE`, the sudo failure/missing-binary marker tables,
`should_escalate` and `is_sudo_rights_failure` now live in `core.remote_exec.sudo` as shared
code, because PMG needs the same escalation rules. Importing them from two places
is how a marker list drifts.

What remains is the WHM-specific translation: a `whm_servers` row is not a connection. Three
refusals happen here, before any socket:

- **No SSH hostname.** WHM stores a `base_url` (`https://host:2087`); the SSH host is its
  hostname component. A `base_url` that parses to nothing is a bad row, never an unreachable host.
- **No credentials.** Neither a password nor a private key stored → `ssh_not_configured`.
  Distinct from an auth failure: the remedy is an admin filling in the server, not a retry.
- **No pinned fingerprint** (when required) → `ssh_host_key_not_validated`. `ssh_exec` refuses
  this too, but failing here names the WHM server rather than the connection, and it is what
  lets a caller ask "is this server usable?" without opening a connection to find out.

`require_host_key_fingerprint` is a parameter rather than always-on for one caller: the admin
validate flow deliberately connects unpinned, via `ssh_get_host_fingerprint`, to *capture* the
value an operator is about to store (TOFU). Every tool path passes `True`.

**Credentials are decrypted here, and only here, for the SSH path** — via an injected
`SecretCipher` (a port deviation, matching `client.build_whm_client_from_creds`). The
resulting `SSHConnectionConfig` is frozen and `slots=True` precisely because it now holds
plaintext (see `core.remote_exec.types`).

The username default is `root`, and that default is coupled to the escalation rule:
`requires_escalation` compares the *resolved* username, so a blank `ssh_username` column resolves
to `root` and gets no `sudo -n` prefix. Change the default here and the escalation rule changes
with it.
"""

from __future__ import annotations

from typing import Protocol
from urllib.parse import urlsplit

import httpx

from core.integrations.whm.client import WHMClient, build_whm_client_from_creds
from core.remote_exec.errors import SSHExecutionError
from core.remote_exec.types import SSHConnectionConfig
from core.secrets.crypto import SecretCipher


class WHMServerSecretLike(Protocol):
    """The `whm_servers` columns this layer reads (`core.db.models.WHMServer`).

    A `Protocol` rather than the ORM class, verbatim from `noa-old`: it keeps `core/`'s
    integration layer independent of the session that loaded the row, and lets tests pass a
    plain object instead of constructing a mapped instance.
    """

    base_url: str
    api_username: str
    api_token: str
    verify_ssl: bool
    ssh_username: str | None
    ssh_port: int | None
    ssh_password: str | None
    ssh_private_key: str | None
    ssh_private_key_passphrase: str | None
    ssh_host_key_fingerprint: str | None


def build_whm_client(
    server: WHMServerSecretLike,
    *,
    cipher: SecretCipher,
    transport: httpx.AsyncBaseTransport | None = None,
) -> WHMClient:
    """Row → authenticated WHM API client.

    `transport` is forwarded for the same reason `build_whm_client_from_creds` takes one: it
    is the test seam, and it is a parameter rather than an attribute a test reaches into
    afterwards. A tool test that swaps the transport keeps the real client, the real cipher
    and the real decrypt site in the path — only the socket is doubled.
    """
    return build_whm_client_from_creds(
        base_url=server.base_url,
        api_username=server.api_username,
        encrypted_token=server.api_token,
        verify_ssl=server.verify_ssl,
        cipher=cipher,
        transport=transport,
    )


class WHMClientFactory(Protocol):
    """How the tool path asks for a WHM client.

    `build_whm_client` is the production implementation and the default everywhere. The
    Protocol exists so `McpToolContext` can name the seam in a type instead of a
    `Callable[..., WHMClient]` that would accept any signature — a factory taking the row
    positionally and the cipher by keyword is the shape every caller writes.
    """

    def __call__(
        self,
        server: WHMServerSecretLike,
        *,
        cipher: SecretCipher,
    ) -> WHMClient: ...


def has_ssh_credentials(server: WHMServerSecretLike) -> bool:
    """True when the row carries something to authenticate with."""
    return bool(server.ssh_password or server.ssh_private_key)


def resolve_whm_ssh_config(
    server: WHMServerSecretLike,
    *,
    cipher: SecretCipher,
    require_host_key_fingerprint: bool,
) -> SSHConnectionConfig:
    """Row → one pinned `SSHConnectionConfig`, or refuse with a named code."""
    hostname = urlsplit(server.base_url).hostname
    if not hostname:
        raise SSHExecutionError(
            code="ssh_invalid_host",
            message="WHM server base URL does not include a valid SSH hostname",
        )

    if not has_ssh_credentials(server):
        raise SSHExecutionError(
            code="ssh_not_configured",
            message="SSH credentials are not configured for this WHM server",
        )

    fingerprint = (server.ssh_host_key_fingerprint or "").strip() or None
    if require_host_key_fingerprint and fingerprint is None:
        raise SSHExecutionError(
            code="ssh_host_key_not_validated",
            message="SSH host key fingerprint is not validated for this WHM server",
        )

    # Blank column → `root` → no `sudo -n` (see module docstring).
    username = (server.ssh_username or "").strip() or "root"
    return SSHConnectionConfig(
        host=hostname,
        port=server.ssh_port or 22,
        username=username,
        password=(
            cipher.maybe_decrypt_text(server.ssh_password)
            if server.ssh_password is not None
            else None
        ),
        private_key=(
            cipher.maybe_decrypt_text(server.ssh_private_key)
            if server.ssh_private_key is not None
            else None
        ),
        private_key_passphrase=(
            cipher.maybe_decrypt_text(server.ssh_private_key_passphrase)
            if server.ssh_private_key_passphrase is not None
            else None
        ),
        host_key_fingerprint=fingerprint,
    )


__all__ = [
    "WHMClientFactory",
    "WHMServerSecretLike",
    "build_whm_client",
    "has_ssh_credentials",
    "resolve_whm_ssh_config",
]
