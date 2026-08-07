"""Doubles for the SSH transport, shared by every integration that runs commands (T14, T16, T18).

These started in `support/whm.py` (T16). None of them is WHM-specific — they are about
`core/remote_exec`: a resolved `SSHConnectionConfig`, a `CommandResult`, and a stand-in for
`ssh_exec`. PMG (T18) is the second caller and T54's validate routes will be the third, so they
live here and `support/whm.py` re-exports them under the names four test files already import
(V66, exactly the move `support/secrets.py` made for `build_cipher` at T17).

No live host: the layers under test are command composition, output parsing and failure
classification — none of which needs a socket.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from types import ModuleType

from core.remote_exec.types import CommandResult, SSHConnectionConfig

PINNED_FINGERPRINT = "SHA256:pinned-fingerprint-value"
SSH_PASSWORD = "operator-ssh-password"

# What sudo prints when the user has no rule for the command. No `sudo:` prefix on common
# versions, which is exactly why `core.remote_exec.sudo` matches the phrase (V55).
SUDO_DENIED_STDERR = "operator is not allowed to execute '/usr/sbin/csf' as root on web16"

# What sudo prints when the wrapped binary is absent — must NOT read as a rights failure.
SUDO_MISSING_BINARY_STDERR = "sudo: imunify360-agent: command not found"


def ssh_config(*, username: str = "root", host: str = "whm.example.com") -> SSHConnectionConfig:
    """A resolved connection, for the command-composition tests that need no server row."""
    return SSHConnectionConfig(
        host=host,
        port=22,
        username=username,
        password=SSH_PASSWORD,
        host_key_fingerprint=PINNED_FINGERPRINT,
    )


def command_result(
    *,
    command: str = "cmd",
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> CommandResult:
    """A `CommandResult` with the banner-stripped and raw streams set to the same text."""
    return CommandResult(
        command=command,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=1,
        raw_stdout=stdout,
        raw_stderr=stderr,
    )


@dataclass
class RecordedRun:
    """One intercepted `ssh_exec` call."""

    config: SSHConnectionConfig
    command: str


@dataclass
class FakeSSH:
    """Records the commands a module tried to run, and answers each from `handler`."""

    handler: Callable[[str], CommandResult]
    runs: list[RecordedRun] = field(default_factory=list)

    @property
    def commands(self) -> list[str]:
        return [run.command for run in self.runs]

    async def __call__(self, config: SSHConnectionConfig, *, command: str, **_: object):
        self.runs.append(RecordedRun(config=config, command=command))
        return self.handler(command)


def install_fake_ssh_exec(
    monkeypatch,  # type: ignore[no-untyped-def]
    module: ModuleType,
    handler: Callable[[str], CommandResult],
) -> FakeSSH:
    """Replace `ssh_exec` inside `module`'s namespace.

    Per-module because `csf_cli`, `imunify_cli`, `availability` and `pmgsh_cli` each did
    `from core.remote_exec.ssh import ssh_exec` — patching the source module would leave those
    names bound to the original.
    """
    fake = FakeSSH(handler=handler)
    monkeypatch.setattr(module, "ssh_exec", fake)
    return fake


__all__ = [
    "PINNED_FINGERPRINT",
    "SSH_PASSWORD",
    "SUDO_DENIED_STDERR",
    "SUDO_MISSING_BINARY_STDERR",
    "FakeSSH",
    "RecordedRun",
    "command_result",
    "install_fake_ssh_exec",
    "ssh_config",
]
