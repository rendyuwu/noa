"""Pinned SSH execution (T14, V55, V56, V69).

Ported from `noa-old` branch `MCP` (`core/remote_exec/ssh.py`, C13/V69). Three deltas from
the source, all noted at their line; everything else is the hardened original.

Two connection paths, and the split is the whole security model:

- `ssh_exec` — refuses to connect without a stored fingerprint (`ssh_host_key_not_validated`)
  and compares the presented key against it with `hmac.compare_digest`. `known_hosts=None`
  disables asyncssh's own file-based check *because* the pin replaces it; dropping the pin
  without dropping that kwarg would trust any host that answers.
- `ssh_get_host_fingerprint` — the deliberate exception: trust-on-first-use, used by the
  admin validate flow (T54) to capture or refresh the value an operator then stores. It runs
  no command, so an unpinned connection here reveals nothing to a host that lied.

`sudo -n` composition lives in `core.remote_exec.sudo` (V55), not here: this module runs a
command string, it does not decide what escalation that string needs.
"""

from __future__ import annotations

import hmac
import shlex
import time
from typing import Final

import asyncssh

from core.remote_exec.banner_strip import strip_ssh_banners
from core.remote_exec.errors import SSHExecutionError
from core.remote_exec.types import CommandResult, SSHConnectionConfig

# Connect *and* per-command deadline. A module constant, as in `noa-old`: a remote hop that
# needs longer than 20s is a broken host, not a tuning problem, and callers that genuinely
# need more pass `timeout_seconds` explicitly.
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 20.0

# asyncssh reports `exit_status is None` when the remote process was killed by a signal
# (`exit_signal` carries the detail). `CommandResult.exit_code` is an `int`, and every
# caller branches on `exit_code == 0`, so a `None` here would type-lie and read as a
# generic failure. Signal-killed is a failure — say so with a code no shell produces.
_SIGNAL_KILLED_EXIT_CODE: Final[int] = -1


class _PinnedHostKeySSHClient(asyncssh.SSHClient):
    """Accepts exactly one host key: the stored fingerprint.

    `hmac.compare_digest` rather than `==` — fingerprints are public, but a constant-time
    compare costs nothing and keeps the comparison out of timing-oracle arguments.
    """

    def __init__(self, *, expected_fingerprint: str) -> None:
        self._expected_fingerprint = expected_fingerprint

    def validate_host_public_key(self, host, addr, port, key) -> bool:  # type: ignore[no-untyped-def]
        presented = key.get_fingerprint("sha256")
        return hmac.compare_digest(presented, self._expected_fingerprint)


class _TrustOnFirstUseSSHClient(asyncssh.SSHClient):
    """Accepts any host key and records it, for TOFU capture/refresh only."""

    def __init__(self) -> None:
        self.presented_fingerprint: str | None = None

    def validate_host_public_key(self, host, addr, port, key) -> bool:  # type: ignore[no-untyped-def]
        self.presented_fingerprint = key.get_fingerprint("sha256")
        return True


def _client_keys(config: SSHConnectionConfig) -> list[asyncssh.SSHKey] | None:
    private_key = config.private_key
    if private_key is None:
        return None
    try:
        imported = asyncssh.import_private_key(
            private_key,
            config.private_key_passphrase,
        )
    except (asyncssh.KeyImportError, ValueError) as exc:
        # V8: the key and passphrase stay out of the message; `raise from` keeps the
        # asyncssh cause available to the logs.
        raise SSHExecutionError(
            code="ssh_invalid_private_key",
            message="SSH private key is invalid",
        ) from exc
    return [imported]


def _connection_kwargs(config: SSHConnectionConfig, *, timeout_seconds: float) -> dict[str, object]:
    return {
        "host": config.host,
        "port": config.port,
        "username": config.username,
        "password": config.password,
        "client_keys": _client_keys(config),
        # Pin replaces the known_hosts file (see module docstring) — never loosen this
        # without also removing the fingerprint check below it.
        "known_hosts": None,
        "connect_timeout": timeout_seconds,
    }


async def ssh_get_host_fingerprint(
    config: SSHConnectionConfig, *, timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
) -> str:
    """Connect trust-on-first-use and return the host's sha256 fingerprint.

    The only unpinned path in this module. Runs no command: it exists so T54's validate
    endpoint can show an operator the fingerprint it is about to store, and so a legitimate
    host-key rotation has a refresh path that is an explicit admin action rather than a
    silent accept inside `ssh_exec`.
    """
    client = _TrustOnFirstUseSSHClient()
    try:
        connection, _ = await asyncssh.create_connection(
            lambda: client,
            **_connection_kwargs(config, timeout_seconds=timeout_seconds),
        )
    # `TimeoutError` (not `asyncio.TimeoutError`): identical class on 3.11+, and ruff UP041
    # rejects the alias. Same substitution at the `ssh_exec` sites below.
    except TimeoutError as exc:
        raise SSHExecutionError(
            code="ssh_timeout",
            message="SSH connection timed out",
        ) from exc
    except asyncssh.PermissionDenied as exc:
        raise SSHExecutionError(
            code="ssh_auth_failed",
            message="SSH authentication failed",
        ) from exc
    except asyncssh.Error as exc:
        raise SSHExecutionError(
            code="ssh_connection_failed",
            message=f"SSH connection failed: {exc}",
        ) from exc
    except OSError as exc:
        raise SSHExecutionError(
            code="ssh_connection_failed",
            message=f"SSH connection failed: {exc}",
        ) from exc

    try:
        fingerprint = client.presented_fingerprint
        if fingerprint:
            return fingerprint
        server_key = connection.get_server_host_key()
        if server_key is None:
            raise SSHExecutionError(
                code="ssh_host_key_unavailable",
                message="SSH host key fingerprint is unavailable",
            )
        return server_key.get_fingerprint("sha256")
    finally:
        connection.close()
        await connection.wait_closed()


def command_from_argv(argv: list[str]) -> str:
    """Quote `argv` into one shell-safe command string.

    The only sanctioned way to build a command from operator- or LLM-supplied parts: every
    token goes through `shlex.quote`, so an argument containing `;` or `$(…)` stays one
    argument. PMG's argv-only rule (V58) and the firewall builders (T16) both route here.
    """
    if not argv:
        raise SSHExecutionError(
            code="ssh_command_invalid",
            message="SSH command is required",
        )
    return " ".join(shlex.quote(part) for part in argv)


async def ssh_exec(
    config: SSHConnectionConfig,
    *,
    command: str,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> CommandResult:
    """Run `command` on a host whose key matches the stored pin.

    Order matters: the missing-pin refusal happens *before* any socket is opened, so an
    unvalidated server cannot be reached at all.
    """
    normalized_command = command.strip()
    if not normalized_command:
        raise SSHExecutionError(
            code="ssh_command_invalid",
            message="SSH command is required",
        )
    if not config.host_key_fingerprint:
        raise SSHExecutionError(
            code="ssh_host_key_not_validated",
            message="SSH host key fingerprint is not configured",
        )

    started = time.perf_counter()
    try:
        connection, _ = await asyncssh.create_connection(
            lambda: _PinnedHostKeySSHClient(expected_fingerprint=config.host_key_fingerprint or ""),
            **_connection_kwargs(config, timeout_seconds=timeout_seconds),
        )
    except TimeoutError as exc:
        raise SSHExecutionError(
            code="ssh_timeout",
            message="SSH connection timed out",
        ) from exc
    except asyncssh.PermissionDenied as exc:
        raise SSHExecutionError(
            code="ssh_auth_failed",
            message="SSH authentication failed",
        ) from exc
    except asyncssh.HostKeyNotVerifiable as exc:
        # The pin rejected the presented key. Distinct code because the remedy is an
        # investigation, not a retry: either the host rotated its key (admin re-validates,
        # T54) or something is answering in its place.
        raise SSHExecutionError(
            code="ssh_host_key_mismatch",
            message="SSH host key fingerprint did not match the stored fingerprint",
        ) from exc
    except asyncssh.Error as exc:
        raise SSHExecutionError(
            code="ssh_connection_failed",
            message=f"SSH connection failed: {exc}",
        ) from exc
    except OSError as exc:
        raise SSHExecutionError(
            code="ssh_connection_failed",
            message=f"SSH connection failed: {exc}",
        ) from exc

    try:
        try:
            result = await connection.run(
                normalized_command,
                check=False,
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            raise SSHExecutionError(
                code="ssh_timeout",
                message="SSH command timed out",
            ) from exc
        except asyncssh.ProcessError as exc:
            raise SSHExecutionError(
                code="ssh_command_failed",
                message=str(exc),
            ) from exc
        duration_ms = int((time.perf_counter() - started) * 1000)
        # Strip the CloudLinux LVE/PAM banner from stdout only: it was confirmed
        # to land on stdout (live capture on web16-cpn; `2>/dev/null` still shows
        # it), so stderr is passed through unchanged (`noa-old` GH #83, V56).
        # raw_stdout/raw_stderr retain the unstripped streams for debugging/audit.
        raw_stdout = result.stdout
        exit_status = result.exit_status
        return CommandResult(
            command=normalized_command,
            exit_code=(_SIGNAL_KILLED_EXIT_CODE if exit_status is None else exit_status),
            stdout=strip_ssh_banners(raw_stdout),
            stderr=result.stderr,
            duration_ms=duration_ms,
            raw_stdout=raw_stdout,
            raw_stderr=result.stderr,
        )
    finally:
        connection.close()
        await connection.wait_closed()


__all__ = [
    "SSHExecutionError",
    "command_from_argv",
    "ssh_exec",
    "ssh_get_host_fingerprint",
]
