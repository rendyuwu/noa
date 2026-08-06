"""Doubles for the WHM integration layer (T16).

Three test files reach for the same three things — a `whm_servers`-shaped row, a working
`SecretCipher`, and a stand-in for `ssh_exec` — so they live here rather than three times over
(V66, the same reason `support/auth.py` exists).

No live host and no live WHM: `FakeWHMServer` satisfies the `WHMServerSecretLike` protocol
structurally, and `install_fake_ssh_exec` replaces the transport at the module boundary. The
layer under test is command composition, output parsing and failure classification — none of
which needs a socket.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from types import ModuleType

from cryptography.fernet import Fernet

from core.remote_exec.types import CommandResult, SSHConnectionConfig
from core.secrets.crypto import SecretCipher

PINNED_FINGERPRINT = "SHA256:pinned-fingerprint-value"
SSH_PASSWORD = "operator-ssh-password"

# What sudo prints when the user has no rule for the command. No `sudo:` prefix on common
# versions, which is exactly why `core.remote_exec.sudo` matches the phrase (V55).
SUDO_DENIED_STDERR = "operator is not allowed to execute '/usr/sbin/csf' as root on web16"

# What sudo prints when the wrapped binary is absent — must NOT read as a rights failure.
SUDO_MISSING_BINARY_STDERR = "sudo: imunify360-agent: command not found"


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


def build_cipher() -> SecretCipher:
    """A cipher on a throwaway key. Real Fernet, so `maybe_decrypt_text` is really exercised."""
    return SecretCipher(key=Fernet.generate_key().decode())


def ssh_config(*, username: str = "root") -> SSHConnectionConfig:
    """A resolved connection, for the command-composition tests that need no server row."""
    return SSHConnectionConfig(
        host="whm.example.com",
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

    Per-module because `csf_cli`, `imunify_cli` and `availability` each did
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
    "FakeWHMServer",
    "RecordedRun",
    "build_cipher",
    "command_result",
    "install_fake_ssh_exec",
    "ssh_config",
]
