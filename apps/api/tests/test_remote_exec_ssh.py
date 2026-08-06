"""Pinned SSH execution (T14, V56, V69, V73).

No host needed: `asyncssh.create_connection` is monkeypatched, and the fake calls the client
factory's `validate_host_public_key` the way the real handshake does — so the host-key pin is
exercised, not just the code around it.

What each group protects:

- banner strip + raw retention — V56, and the parse failures behind `noa-old` GH #83.
- the pin — `ssh_exec` refuses without a stored fingerprint *before* opening a socket, and a
  mismatched key is its own error code rather than a generic connect failure (V69).
- TOFU capture — the one unpinned path, which T54's validate endpoint depends on.
- error shape — `SSHExecutionError` is a `NoaError` with a mapped status, so the shared
  handler answers 502 instead of the unclassified-auth 503 fallback (V73).
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import asyncssh
import pytest

import core.remote_exec.ssh as ssh_mod
from core.remote_exec.errors import SSHExecutionError
from core.remote_exec.ssh import (
    command_from_argv,
    ssh_exec,
    ssh_get_host_fingerprint,
)
from core.remote_exec.types import SSHConnectionConfig
from noa_api.api.errors import FALLBACK_STATUS, error_body, status_for

_LVE_BANNER = (
    "***************************************************************************\n"
    "*             !!!!  WARNING: YOU ARE INSIDE LVE !!!!                      *\n"
    "***************************************************************************"
)

_PINNED_FINGERPRINT = "SHA256:pinned-fingerprint-value"
_OTHER_FINGERPRINT = "SHA256:some-other-host-entirely"

_SSH_PASSWORD = "operator-ssh-password"
_SSH_PASSPHRASE = "key-passphrase"


class _FakeHostKey:
    def __init__(self, fingerprint: str) -> None:
        self._fingerprint = fingerprint

    def get_fingerprint(self, kind: str = "sha256") -> str:
        assert kind == "sha256"
        return self._fingerprint


class _FakeConnection:
    def __init__(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        exit_status: int | None = 0,
        server_host_key: _FakeHostKey | None = None,
    ) -> None:
        self._run_result = SimpleNamespace(stdout=stdout, stderr=stderr, exit_status=exit_status)
        self._server_host_key = server_host_key
        self.closed = False
        self.waited = False
        self.commands: list[str] = []

    # `timeout` mirrors `asyncssh.SSHClientConnection.run`, which is what `ssh_exec` calls.
    async def run(
        self,
        command: str,
        *,
        check: bool = False,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> SimpleNamespace:
        _ = (check, timeout)
        self.commands.append(command)
        return self._run_result

    def get_server_host_key(self) -> _FakeHostKey | None:
        return self._server_host_key

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        self.waited = True


def _config(**overrides: Any) -> SSHConnectionConfig:
    values: dict[str, Any] = {
        "host": "example.test",
        "port": 22,
        "username": "root",
        "host_key_fingerprint": _PINNED_FINGERPRINT,
    }
    values.update(overrides)
    return SSHConnectionConfig(**values)


def _install_connection(
    monkeypatch: pytest.MonkeyPatch,
    connection: _FakeConnection,
    *,
    presented_fingerprint: str | None = _PINNED_FINGERPRINT,
) -> dict[str, Any]:
    """Patch `create_connection` with a fake that runs the host-key validation callback.

    `presented_fingerprint=None` skips validation entirely, which is how the TOFU fallback to
    `get_server_host_key()` is reached.
    """
    captured: dict[str, Any] = {}

    async def _fake_create_connection(
        client_factory: Callable[[], Any], **kwargs: Any
    ) -> tuple[_FakeConnection, None]:
        client = client_factory()
        captured["client"] = client
        captured["kwargs"] = kwargs
        if presented_fingerprint is not None:
            accepted = client.validate_host_public_key(
                kwargs["host"], "203.0.113.10", kwargs["port"], _FakeHostKey(presented_fingerprint)
            )
            captured["accepted"] = accepted
            if not accepted:
                raise asyncssh.HostKeyNotVerifiable("Host key is not trusted")
        return connection, None

    monkeypatch.setattr(ssh_mod.asyncssh, "create_connection", _fake_create_connection)
    return captured


def _install_failing_connection(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> list[bool]:
    attempts: list[bool] = []

    async def _fake_create_connection(
        client_factory: Callable[[], Any], **kwargs: Any
    ) -> tuple[_FakeConnection, None]:
        _ = (client_factory, kwargs)
        attempts.append(True)
        raise error

    monkeypatch.setattr(ssh_mod.asyncssh, "create_connection", _fake_create_connection)
    return attempts


# --- V56: banner stripped at the boundary, raw kept for audit ---


async def test_ssh_exec_strips_banner_from_stdout_and_preserves_raw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = '{"max_count": 0, "items": []}'
    raw_stdout = f"{_LVE_BANNER}\n{payload}"
    stderr_value = "some stderr"
    connection = _FakeConnection(stdout=raw_stdout, stderr=stderr_value, exit_status=0)
    _install_connection(monkeypatch, connection)

    result = await ssh_exec(_config(), command="imunify360-agent ... --json")

    # Boundary strip: stdout banner-free, raw retained for debug/audit (V56).
    assert result.stdout == payload
    assert result.raw_stdout == raw_stdout
    # stderr untouched (banner is stdout-only); raw_stderr mirrors for symmetry.
    assert result.stderr == stderr_value
    assert result.raw_stderr == stderr_value
    assert result.exit_code == 0


async def test_ssh_exec_closes_connection_and_reports_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeConnection(stdout="ok")
    _install_connection(monkeypatch, connection)

    result = await ssh_exec(_config(), command="  csf -g 1.2.3.4  ")

    assert connection.commands == ["csf -g 1.2.3.4"]
    assert result.command == "csf -g 1.2.3.4"
    assert result.duration_ms >= 0
    assert connection.closed is True
    assert connection.waited is True


async def test_ssh_exec_signal_killed_process_reports_negative_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """asyncssh reports `exit_status=None` for a signal kill; `exit_code` stays an int."""
    connection = _FakeConnection(stdout="partial", exit_status=None)
    _install_connection(monkeypatch, connection)

    result = await ssh_exec(_config(), command="csf -g 1.2.3.4")

    assert result.exit_code == -1
    assert isinstance(result.exit_code, int)


# --- V69: host-key pinning ---


def test_pinned_client_accepts_matching_fingerprint() -> None:
    client = ssh_mod._PinnedHostKeySSHClient(expected_fingerprint=_PINNED_FINGERPRINT)

    assert (
        client.validate_host_public_key(
            "example.test", "203.0.113.10", 22, _FakeHostKey(_PINNED_FINGERPRINT)
        )
        is True
    )


def test_pinned_client_rejects_mismatched_fingerprint() -> None:
    client = ssh_mod._PinnedHostKeySSHClient(expected_fingerprint=_PINNED_FINGERPRINT)

    assert (
        client.validate_host_public_key(
            "example.test", "203.0.113.10", 22, _FakeHostKey(_OTHER_FINGERPRINT)
        )
        is False
    )


async def test_ssh_exec_without_stored_fingerprint_never_connects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal precedes the socket: an unvalidated server is unreachable."""
    attempts = _install_failing_connection(monkeypatch, AssertionError("must not connect"))

    with pytest.raises(SSHExecutionError) as excinfo:
        await ssh_exec(_config(host_key_fingerprint=None), command="csf -g 1.2.3.4")

    assert excinfo.value.error_code == "ssh_host_key_not_validated"
    assert attempts == []


async def test_ssh_exec_rejects_host_presenting_a_different_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeConnection(stdout="should never be read")
    captured = _install_connection(
        monkeypatch, connection, presented_fingerprint=_OTHER_FINGERPRINT
    )

    with pytest.raises(SSHExecutionError) as excinfo:
        await ssh_exec(_config(), command="csf -g 1.2.3.4")

    assert excinfo.value.error_code == "ssh_host_key_mismatch"
    assert captured["accepted"] is False
    assert connection.commands == []


async def test_ssh_exec_disables_known_hosts_because_the_pin_replaces_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeConnection(stdout="ok")
    captured = _install_connection(monkeypatch, connection)

    await ssh_exec(_config(username="noa-ops"), command="csf -v", timeout_seconds=7.5)

    kwargs = captured["kwargs"]
    assert kwargs["known_hosts"] is None
    assert kwargs["connect_timeout"] == 7.5
    assert kwargs["username"] == "noa-ops"


# --- Connect-failure classification ---


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (TimeoutError(), "ssh_timeout"),
        (asyncssh.PermissionDenied("no"), "ssh_auth_failed"),
        (asyncssh.HostKeyNotVerifiable("nope"), "ssh_host_key_mismatch"),
        (asyncssh.Error(2, "protocol error"), "ssh_connection_failed"),
        (OSError("connection refused"), "ssh_connection_failed"),
    ],
)
async def test_ssh_exec_maps_connect_failures(
    monkeypatch: pytest.MonkeyPatch, error: BaseException, expected_code: str
) -> None:
    _install_failing_connection(monkeypatch, error)

    with pytest.raises(SSHExecutionError) as excinfo:
        await ssh_exec(_config(), command="csf -v")

    assert excinfo.value.error_code == expected_code


async def test_ssh_exec_command_timeout_is_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _FakeConnection()

    # Same asyncssh-mirroring signature as `_FakeConnection.run`.
    async def _timeout_run(
        command: str,
        *,
        check: bool = False,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> SimpleNamespace:
        _ = (command, check, timeout)
        raise TimeoutError

    connection.run = _timeout_run  # type: ignore[method-assign]
    _install_connection(monkeypatch, connection)

    with pytest.raises(SSHExecutionError) as excinfo:
        await ssh_exec(_config(), command="csf -v")

    assert excinfo.value.error_code == "ssh_timeout"
    # Still closed: the deadline must not leak a connection.
    assert connection.closed is True


async def test_ssh_exec_blank_command_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = _install_failing_connection(monkeypatch, AssertionError("must not connect"))

    with pytest.raises(SSHExecutionError) as excinfo:
        await ssh_exec(_config(), command="   ")

    assert excinfo.value.error_code == "ssh_command_invalid"
    assert attempts == []


async def test_invalid_private_key_is_named_without_leaking_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_failing_connection(monkeypatch, AssertionError("must not connect"))

    with pytest.raises(SSHExecutionError) as excinfo:
        await ssh_exec(
            _config(
                private_key="-----BEGIN NOT A KEY-----", private_key_passphrase=_SSH_PASSPHRASE
            ),
            command="csf -v",
        )

    assert excinfo.value.error_code == "ssh_invalid_private_key"
    assert _SSH_PASSPHRASE not in excinfo.value.message


# --- TOFU capture / refresh (T54's dependency) ---


async def test_get_host_fingerprint_returns_presented_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeConnection()
    captured = _install_connection(
        monkeypatch, connection, presented_fingerprint=_OTHER_FINGERPRINT
    )

    fingerprint = await ssh_get_host_fingerprint(_config(host_key_fingerprint=None))

    # Unpinned on purpose: this is how an operator learns the value to store, and how a
    # rotated key gets refreshed as an explicit admin action.
    assert fingerprint == _OTHER_FINGERPRINT
    assert captured["client"].presented_fingerprint == _OTHER_FINGERPRINT
    assert connection.closed is True
    assert connection.waited is True


async def test_get_host_fingerprint_falls_back_to_server_host_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeConnection(server_host_key=_FakeHostKey(_OTHER_FINGERPRINT))
    _install_connection(monkeypatch, connection, presented_fingerprint=None)

    assert await ssh_get_host_fingerprint(_config()) == _OTHER_FINGERPRINT


async def test_get_host_fingerprint_without_any_key_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeConnection(server_host_key=None)
    _install_connection(monkeypatch, connection, presented_fingerprint=None)

    with pytest.raises(SSHExecutionError) as excinfo:
        await ssh_get_host_fingerprint(_config())

    assert excinfo.value.error_code == "ssh_host_key_unavailable"
    assert connection.closed is True


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (TimeoutError(), "ssh_timeout"),
        (asyncssh.PermissionDenied("no"), "ssh_auth_failed"),
        (asyncssh.Error(2, "protocol error"), "ssh_connection_failed"),
        (OSError("connection refused"), "ssh_connection_failed"),
    ],
)
async def test_get_host_fingerprint_maps_connect_failures(
    monkeypatch: pytest.MonkeyPatch, error: BaseException, expected_code: str
) -> None:
    _install_failing_connection(monkeypatch, error)

    with pytest.raises(SSHExecutionError) as excinfo:
        await ssh_get_host_fingerprint(_config())

    assert excinfo.value.error_code == expected_code


# --- argv quoting ---


def test_command_from_argv_quotes_every_token() -> None:
    out = command_from_argv(["/usr/sbin/csf", "-g", "1.2.3.4; rm -rf /"])

    assert out == "/usr/sbin/csf -g '1.2.3.4; rm -rf /'"


def test_command_from_argv_rejects_empty_argv() -> None:
    with pytest.raises(SSHExecutionError) as excinfo:
        command_from_argv([])

    assert excinfo.value.error_code == "ssh_command_invalid"


# --- V73: one error shape, one handler ---


def test_ssh_execution_error_is_a_noa_error_with_mapped_status() -> None:
    error = SSHExecutionError(code="ssh_timeout", message="SSH connection timed out")

    status = status_for(error)

    assert status == 502
    assert status != FALLBACK_STATUS
    assert error_body(error) == {
        "error_code": "ssh_timeout",
        "message": "SSH connection timed out",
    }


async def test_error_messages_carry_no_credential_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V8: a connect failure names the transport, never the credential presented with it."""
    _install_failing_connection(monkeypatch, OSError("connection refused"))

    with pytest.raises(SSHExecutionError) as excinfo:
        await ssh_exec(_config(password=_SSH_PASSWORD), command="csf -v")

    error = excinfo.value
    assert error.error_code == "ssh_connection_failed"
    assert _SSH_PASSWORD not in error.message
    assert _SSH_PASSWORD not in str(error)
    assert _SSH_PASSWORD not in str(error_body(error))
