"""Doubles for the SSH transport, shared by every integration that runs commands.

These started in `support/whm.py`. None of them is WHM-specific — they are about
`core/remote_exec`: a resolved `SSHConnectionConfig`, a `CommandResult`, and a stand-in for
`ssh_exec`. PMG is the second caller and the admin validate routes are the third, so they
live here (the reuse rule, exactly the move `support/secrets.py` made for `build_cipher` when
secrets were ported). This is their one home — the re-export `support/whm.py` carried through
the PMG port is gone since the doubles moved to their one home.

Mostly no live host: the layers under test are command composition, output parsing and failure
classification — none of which needs a socket. **The exception is the loopback `asyncssh` server
at the bottom**, which is not a double at all and moved here with the admin validate routes for
the reason recorded beside it: the host-key pin is only observable against a real key exchange,
and two copies of the server that proves it are two copies that can stop proving it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

import asyncssh

import core.remote_exec.ssh as ssh_module
from core.remote_exec.types import CommandResult, SSHConnectionConfig

PINNED_FINGERPRINT = "SHA256:pinned-fingerprint-value"
SSH_PASSWORD = "operator-ssh-password"

# What sudo prints when the user has no rule for the command. No `sudo:` prefix on common
# versions, which is exactly why `core.remote_exec.sudo` matches the phrase.
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


def patch_ssh_exec(
    monkeypatch,  # type: ignore[no-untyped-def]
    modules: Iterable[ModuleType],
    replacement: object,
) -> None:
    """Bind `replacement` everywhere `modules` can reach `ssh_exec`.

    `core.remote_exec.ssh` itself first: `csf_cli`, `imunify_cli` and `pmgsh_cli` run their
    commands through `run_cli`, which resolves `ssh_exec` from that module's own globals, so
    patching there is what covers them. `availability` still does
    `from core.remote_exec.ssh import ssh_exec`, and that name was bound at import — hence the
    per-module pass as well, for whichever of `modules` carries its own binding.

    Separate from `install_fake_ssh_exec_in` because a test whose stand-in has to *await*
    something cannot express itself as that function's synchronous `handler`, and a second
    hand-written copy of this two-step is a second thing to forget when a module changes how it
    reaches the transport.
    """
    monkeypatch.setattr(ssh_module, "ssh_exec", replacement)
    for module in modules:
        if hasattr(module, "ssh_exec"):
            monkeypatch.setattr(module, "ssh_exec", replacement)


def install_fake_ssh_exec_in(
    monkeypatch,  # type: ignore[no-untyped-def]
    modules: Iterable[ModuleType],
    handler: Callable[[str], CommandResult],
) -> FakeSSH:
    """Replace `ssh_exec` everywhere the named modules can reach it, sharing one recorder.

    Several modules at once because a tool call crosses them: the dual-backend firewall read's
    preflight probes through
    `availability` and then queries through `csf_cli` and `imunify_cli`, and one shared `FakeSSH`
    is what lets a test assert the *whole* command sequence and its order.
    """
    fake = FakeSSH(handler=handler)
    patch_ssh_exec(monkeypatch, modules, fake)
    return fake


def install_fake_ssh_exec(
    monkeypatch,  # type: ignore[no-untyped-def]
    module: ModuleType,
    handler: Callable[[str], CommandResult],
) -> FakeSSH:
    """`install_fake_ssh_exec_in` for the single-module case, which is most of them."""
    return install_fake_ssh_exec_in(monkeypatch, [module], handler)


# --- A real SSH server on loopback ---
#
# **The one rig in this file that is not a double**, and the reason it is here rather than in
# the test file that first needed it. The inert-pin defect shipped because the pin's test called
# `validate_host_public_key` itself: that proves the callback works when something calls it,
# and what was broken was that nothing did. Only a real key exchange can tell the difference,
# and only a real server can report that it was never asked to authenticate anybody.
#
# `test_remote_exec_ssh.py` owns the pin itself; `test_server_host_key_validation.py`
# owns the trust-on-first-use rule that decides *when* a pin is written. Both need the
# same server, so it lives here — the move the expiry sweep made for the concurrency rig rather
# than copying it, and the concurrency-test rule's reason: two copies of the thing that holds a
# property are two things that can silently stop holding it.


@dataclass
class RecordingSSHServer(asyncssh.SSHServer):
    """A loopback server that remembers whether a client ever got as far as authenticating.

    `auth_attempts` staying empty is the assertion the live-pin rule is really about: a pin
    compared *after* the connection would raise the same error, having already handed the
    password to whatever
    answered the socket.
    """

    auth_attempts: list[str] = field(default_factory=list)

    def begin_auth(self, username: str) -> bool:
        self.auth_attempts.append(username)
        return True

    def password_auth_supported(self) -> bool:
        return True

    def validate_password(self, username: str, password: str) -> bool:
        self.auth_attempts.append(f"{username}:{password}")
        return True


@dataclass
class LoopbackSSH:
    """Where the server is listening, which key it presents, and what it was asked."""

    port: int
    host_key_fingerprint: str
    auth_attempts: list[str]


@asynccontextmanager
async def loopback_ssh_server(*, stdout: str = "live-output") -> AsyncIterator[LoopbackSSH]:
    """A real SSH server on 127.0.0.1 with a throwaway host key.

    Ed25519 keygen and a loopback socket, so this costs milliseconds. Everything the pin
    claims — rejection during key exchange, before auth — is only observable here.
    """
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    attempts: list[str] = []

    def _server_factory() -> RecordingSSHServer:
        return RecordingSSHServer(auth_attempts=attempts)

    def _process_factory(process: Any) -> None:
        process.stdout.write(stdout)
        process.exit(0)

    server = await asyncssh.create_server(
        _server_factory,
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        process_factory=_process_factory,
    )
    try:
        yield LoopbackSSH(
            port=server.sockets[0].getsockname()[1],
            host_key_fingerprint=host_key.get_fingerprint("sha256"),
            auth_attempts=attempts,
        )
    finally:
        server.close()
        await server.wait_closed()


def loopback_ssh_config(
    server: LoopbackSSH,
    *,
    fingerprint: str | None,
    username: str = "noa-ops",
) -> SSHConnectionConfig:
    """A config aimed at `server`, pinned to `fingerprint` (or unpinned for TOFU)."""
    return SSHConnectionConfig(
        host="127.0.0.1",
        port=server.port,
        username=username,
        password=SSH_PASSWORD,
        host_key_fingerprint=fingerprint,
    )


__all__ = [
    "PINNED_FINGERPRINT",
    "SSH_PASSWORD",
    "SUDO_DENIED_STDERR",
    "SUDO_MISSING_BINARY_STDERR",
    "FakeSSH",
    "LoopbackSSH",
    "RecordedRun",
    "RecordingSSHServer",
    "command_result",
    "install_fake_ssh_exec",
    "install_fake_ssh_exec_in",
    "loopback_ssh_config",
    "loopback_ssh_server",
    "patch_ssh_exec",
    "ssh_config",
]
