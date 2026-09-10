"""PMG server → SSH connection config.

Copied from `noa-old` branch `MCP` (`pmg/integrations/ssh.py`). PMG has one transport, so this
module is the *only* door into a PMG box — where WHM splits an API client and an SSH path
(`core.integrations.whm.ssh`), everything here goes over SSH to `pmgsh` — the argv-safe
target rule, one of the external-system transports.

That is also why the host validation is stricter than WHM's. A `whm_servers` row stores a
`base_url` and `urlsplit` extracts the hostname component; a `pmg_servers` row stores the bare
host, so nothing has parsed it before it reaches `asyncssh`. The check refuses anything that is
not a plain host: a URL (`://`), embedded whitespace, `/?#@`, or bracketed IPv6 notation. Left
through, `https://pmg:8006` would become a DNS lookup for a name with a scheme glued to it, and
`host/../x` or `user@host` would silently redirect where the command lands.

Four refusals happen here, before any socket, each with the code that names its remedy:

- **Bad `ssh_host`** → `ssh_invalid_host`. A bad row, never an unreachable host.
- **Non-positive `ssh_port`** → `ssh_invalid_port`. `0` would otherwise fall through the
  `or 22` default and read as "unset", so a `0` an admin typed becomes a silent 22.
- **No credentials** → `ssh_not_configured`. Distinct from an auth failure: the remedy is an
  admin filling in the server, not a retry.
- **No pinned fingerprint** (when required) → `ssh_host_key_not_validated`. `ssh_exec` refuses
  this too, but failing here names the PMG server rather than the connection, and it is what
  lets a caller ask "is this server usable?" without opening a connection to find out.

`require_host_key_fingerprint` is a parameter rather than always-on for one caller: the admin
validate route's flow deliberately connects unpinned, via `ssh_get_host_fingerprint`, to
*capture* the value an operator is about to store (TOFU). Every tool path passes `True`.

**Credentials are decrypted here, and only here, for the PMG path** — via an injected
`SecretCipher` (a deviation from the ported PMG layer, matching
`core.integrations.whm.ssh.resolve_whm_ssh_config`).
`noa-old` reached for a module-global `maybe_decrypt_text` that the secrets port deleted with
the settings singleton. The resulting `SSHConnectionConfig` is frozen and `slots=True`
precisely because it now holds plaintext (see `core.remote_exec.types`).

The username default is `root`, and that default is coupled to the sudo-prefix rule:
`requires_escalation`
compares the *resolved* username, so a blank `ssh_username` column resolves to `root` and gets
no `sudo -n` prefix. Change the default here and the escalation rule in
`core.integrations.pmg.pmgsh_cli` changes with it.
"""

from __future__ import annotations

from typing import Protocol

from core.remote_exec.errors import SSHExecutionError
from core.remote_exec.types import SSHConnectionConfig
from core.secrets.crypto import SecretCipher

# Characters that make a string something other than a bare host: a path, a query, a fragment,
# or a userinfo prefix. Verbatim from `noa-old`.
_HOST_FORBIDDEN_CHARS = "/?#@"


class PMGServerSecretLike(Protocol):
    """The `pmg_servers` columns this layer reads (`core.db.models.PMGServer`).

    A `Protocol` rather than the ORM class, verbatim from `noa-old`: it keeps `core/`'s
    integration layer independent of the session that loaded the row, and lets tests pass a
    plain object instead of constructing a mapped instance.

    No `base_url`, no `verify_ssl` — PMG is SSH-only (the argv-safe target rule, one of the
    external-system transports), and `noa-old` carried both
    columns for PMG without ever using them.
    """

    ssh_host: str
    ssh_username: str | None
    ssh_port: int | None
    ssh_password: str | None
    ssh_private_key: str | None
    ssh_private_key_passphrase: str | None
    ssh_host_key_fingerprint: str | None


def has_ssh_credentials(server: PMGServerSecretLike) -> bool:
    """True when the row carries something to authenticate with."""
    return bool(server.ssh_password or server.ssh_private_key)


def _validate_ssh_host(server: PMGServerSecretLike) -> str:
    """Return the bare host, or refuse with `ssh_invalid_host` (see module docstring)."""
    hostname = (server.ssh_host or "").strip()
    if (
        not hostname
        or "://" in hostname
        or any(char.isspace() for char in hostname)
        or any(char in hostname for char in _HOST_FORBIDDEN_CHARS)
        or hostname.startswith("[")
        or hostname.endswith("]")
    ):
        raise SSHExecutionError(
            code="ssh_invalid_host",
            message="PMG server SSH host/IP is invalid",
        )
    return hostname


def resolve_pmg_ssh_config(
    server: PMGServerSecretLike,
    *,
    cipher: SecretCipher,
    require_host_key_fingerprint: bool,
) -> SSHConnectionConfig:
    """Row → one pinned `SSHConnectionConfig`, or refuse with a named code."""
    hostname = _validate_ssh_host(server)

    # `0` is rejected rather than defaulted: `server.ssh_port or 22` below would read it as
    # unset, turning a typo into a silent connection to 22.
    if server.ssh_port is not None and server.ssh_port <= 0:
        raise SSHExecutionError(
            code="ssh_invalid_port",
            message="PMG server SSH port must be greater than 0",
        )

    if not has_ssh_credentials(server):
        raise SSHExecutionError(
            code="ssh_not_configured",
            message="SSH credentials are not configured for this PMG server",
        )

    fingerprint = (server.ssh_host_key_fingerprint or "").strip() or None
    if require_host_key_fingerprint and fingerprint is None:
        raise SSHExecutionError(
            code="ssh_host_key_not_validated",
            message="SSH host key fingerprint is not validated for this PMG server",
        )

    # Blank column → `root` → no `sudo -n` (the sudo-prefix rule; see module docstring).
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
    "PMGServerSecretLike",
    "has_ssh_credentials",
    "resolve_pmg_ssh_config",
]
