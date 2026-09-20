"""Pinned SSH execution.

Two kinds of test here, and the split is deliberate.

Everything *around* the transport — banner stripping, timeouts, failure classification, error
shape — runs against a monkeypatched `asyncssh.create_connection`. Those behaviours need no
socket, and a fake keeps them fast.

The pin itself does **not** use the fake. A double that calls `validate_host_public_key`
itself proves only that the callback works when something calls it; the inert pin shipped
precisely that nothing did. So the pin is exercised against a real `asyncssh.create_server` on
loopback: the rejection has to come out of a real key exchange, and the server has to record
that it was never asked to authenticate anyone.

What each group protects:

- banner strip + raw retention — banners stripped before parsing, raw kept for audit, and the
  parse failures behind `noa-old` GH #83.
- the pin — `ssh_exec` refuses without a stored fingerprint *before* opening a socket, and a
  host presenting any other key is rejected in-handshake, pre-auth.
- TOFU capture — the one unpinned path, which the admin validate endpoint depends on.
- error shape — `SSHExecutionError` is a `NoaError` with a mapped status, so the shared
  handler answers 502 instead of the unclassified-auth 503 fallback.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import asyncssh
import pytest

import core.remote_exec.ssh as ssh_mod
from core.errors import NoaError
from core.remote_exec.errors import SSHExecutionError
from core.remote_exec.ssh import (
    command_from_argv,
    ssh_exec,
    ssh_get_host_fingerprint,
)
from core.remote_exec.types import SSHConnectionConfig
from noa_api.api.errors import error_body
from noa_api.mcp_tools.change_target import NON_ANSWER_ERROR_CODES
from support.remote_exec import loopback_ssh_config, loopback_ssh_server

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


# --- Banner stripped at the boundary, raw kept for audit ---


async def test_ssh_exec_strips_banner_from_stdout_and_preserves_raw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = '{"max_count": 0, "items": []}'
    raw_stdout = f"{_LVE_BANNER}\n{payload}"
    stderr_value = "some stderr"
    connection = _FakeConnection(stdout=raw_stdout, stderr=stderr_value, exit_status=0)
    _install_connection(monkeypatch, connection)

    result = await ssh_exec(_config(), command="imunify360-agent ... --json")

    # Boundary strip: stdout banner-free, raw retained for debug/audit.
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


# --- Host-key pinning ---


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


# --- The pin runs inside a real handshake ---
#
# `known_hosts=None` is the one value that silently switches host-key validation off — asyncssh
# sets `_trusted_host_keys = None` and skips the block that would call the callback. These tests
# stand on a real server precisely because a fake handshake cannot tell the two apart.
#
# The server itself lives in `support/remote_exec.py` since the admin validate landed: its
# validate flow decides *when* a pin gets written and needs the same real handshake, and two
# copies of the rig are two things that can silently stop overlapping with reality — the reason a
# concurrency test must prove overlap, one mechanism over.


async def test_wrong_host_key_is_rejected_by_a_real_handshake_before_auth() -> None:
    """The inert pin's regression test: a wrong pin must stop the connection, not merely be
    compared.

    The auth-attempt assertion is the part that matters. A post-connect fingerprint check
    would also raise `ssh_host_key_mismatch` — after the SSH password had already been handed
    to whatever answered the socket. Rejection has to land in key exchange.
    """
    async with loopback_ssh_server(stdout="attacker-controlled") as server:
        with pytest.raises(SSHExecutionError) as excinfo:
            await ssh_exec(
                loopback_ssh_config(server, fingerprint=_OTHER_FINGERPRINT),
                command="csf -g 1.2.3.4",
                timeout_seconds=10.0,
            )

        assert excinfo.value.error_code == "ssh_host_key_mismatch"
        # Never authenticated, so the credential never crossed the wire.
        assert server.auth_attempts == []


async def test_matching_host_key_runs_the_command_over_a_real_handshake() -> None:
    """The pin must not be so strict it rejects the host it was taken from."""
    async with loopback_ssh_server(stdout="csf: 1.2.3.4 not found\n") as server:
        result = await ssh_exec(
            loopback_ssh_config(server, fingerprint=server.host_key_fingerprint),
            command="csf -g 1.2.3.4",
            timeout_seconds=10.0,
        )

    assert result.exit_code == 0
    assert result.stdout.strip() == "csf: 1.2.3.4 not found"


async def test_get_host_fingerprint_captures_the_key_a_real_server_presents() -> None:
    """TOFU stays unpinned, and now reports the key the handshake actually validated."""
    async with loopback_ssh_server() as server:
        fingerprint = await ssh_get_host_fingerprint(
            loopback_ssh_config(server, fingerprint=None), timeout_seconds=10.0
        )

    assert fingerprint == server.host_key_fingerprint


async def test_ssh_exec_keeps_host_key_validation_switched_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`known_hosts` must stay a list. `None` is the off switch, and it fails silently."""
    connection = _FakeConnection(stdout="ok")
    captured = _install_connection(monkeypatch, connection)

    await ssh_exec(_config(username="noa-ops"), command="csf -v", timeout_seconds=7.5)

    kwargs = captured["kwargs"]
    assert kwargs["known_hosts"] is not None
    # Empty trusted/CA/revoked lists: nothing pre-trusted, so every key reaches the callback.
    assert kwargs["known_hosts"] == ([], [], [])
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
    # **A command that timed out is a command that may have run**, which is why the CHANGE
    # runners read this code as a non-answer rather than as a remote refusal. Asserted here,
    # where production mints the code, because the set is a claim about what this layer emits
    # and a literal typed into that module would be a copy of this value rather than a check on
    # it. Classified the other way, a `pmgsh create` that timed out would make a confirming read
    # that disagrees conclusive — and the read cannot tell "did not happen" from "not yet".
    assert excinfo.value.error_code in NON_ANSWER_ERROR_CODES


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


# --- TOFU capture / refresh (the admin validate route's dependency) ---


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


# --- One error shape, one handler ---


def test_ssh_execution_error_is_a_noa_error_with_mapped_status() -> None:
    error = SSHExecutionError(code="ssh_timeout", message="SSH connection timed out")

    assert error.status_code == 502
    assert error.status_code != NoaError.status_code
    assert error_body(error) == {
        "error_code": "ssh_timeout",
        "message": "SSH connection timed out",
    }


async def test_error_messages_carry_no_credential_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connect failure names the transport, never the credential presented with it."""
    _install_failing_connection(monkeypatch, OSError("connection refused"))

    with pytest.raises(SSHExecutionError) as excinfo:
        await ssh_exec(_config(password=_SSH_PASSWORD), command="csf -v")

    error = excinfo.value
    assert error.error_code == "ssh_connection_failed"
    assert _SSH_PASSWORD not in error.message
    assert _SSH_PASSWORD not in str(error)
    assert _SSH_PASSWORD not in str(error_body(error))
