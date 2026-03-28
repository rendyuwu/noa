from __future__ import annotations

import asyncio
import hmac
import shlex
import time
from typing import Final

import asyncssh

from noa_api.core.remote_exec.types import CommandResult, SSHConnectionConfig

_DEFAULT_TIMEOUT_SECONDS: Final[float] = 20.0


class SSHExecutionError(Exception):
    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _PinnedHostKeySSHClient(asyncssh.SSHClient):
    def __init__(self, *, expected_fingerprint: str) -> None:
        self._expected_fingerprint = expected_fingerprint

    def validate_host_public_key(self, host, addr, port, key) -> bool:  # type: ignore[no-untyped-def]
        presented = key.get_fingerprint("sha256")
        return hmac.compare_digest(presented, self._expected_fingerprint)


class _TrustOnFirstUseSSHClient(asyncssh.SSHClient):
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
        raise SSHExecutionError(
            code="ssh_invalid_private_key",
            message="SSH private key is invalid",
        ) from exc
    return [imported]


def _connection_kwargs(
    config: SSHConnectionConfig, *, timeout_seconds: float
) -> dict[str, object]:
    return {
        "host": config.host,
        "port": config.port,
        "username": config.username,
        "password": config.password,
        "client_keys": _client_keys(config),
        "known_hosts": None,
        "connect_timeout": timeout_seconds,
    }


async def ssh_get_host_fingerprint(
    config: SSHConnectionConfig, *, timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
) -> str:
    client = _TrustOnFirstUseSSHClient()
    try:
        connection, _ = await asyncssh.create_connection(
            lambda: client,
            **_connection_kwargs(config, timeout_seconds=timeout_seconds),
        )
    except asyncio.TimeoutError as exc:
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
            lambda: _PinnedHostKeySSHClient(
                expected_fingerprint=config.host_key_fingerprint or ""
            ),
            **_connection_kwargs(config, timeout_seconds=timeout_seconds),
        )
    except asyncio.TimeoutError as exc:
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
        except asyncio.TimeoutError as exc:
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
        return CommandResult(
            command=normalized_command,
            exit_code=result.exit_status,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=duration_ms,
        )
    finally:
        connection.close()
        await connection.wait_closed()
