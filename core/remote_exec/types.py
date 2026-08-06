"""SSH value objects (T14).

Ported from `noa-old` branch `MCP` (`core/remote_exec/types.py`, C13/V69) unchanged.

Both are frozen with `slots=True`: `SSHConnectionConfig` carries decrypted credentials, so
an accidental mutation mid-flight would be a security bug, and a `__dict__`-less instance
cannot pick up stray attributes that a logger might later serialise (V8).

`CommandResult` keeps *four* output fields, not two, and that split is V56: `stdout` is
banner-stripped and safe to parse, `raw_stdout` is what the host actually sent and is what
an auditor needs when a parse goes wrong. Callers parse `stdout`; audit stores raw.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SSHConnectionConfig:
    """Everything needed to open one pinned SSH connection.

    `host_key_fingerprint` is the pin (V69). `ssh_exec` refuses to connect without it;
    only `ssh_get_host_fingerprint` runs unpinned, and only to capture the value an
    operator is about to store (TOFU).
    """

    host: str
    port: int
    username: str
    password: str | None = None
    private_key: str | None = None
    private_key_passphrase: str | None = None
    host_key_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class CommandResult:
    """One remote command's outcome.

    `stdout` is banner-stripped, `raw_stdout` is verbatim (V56). `stderr` is passed through
    untouched — the CloudLinux LVE/PAM banner was confirmed to land on stdout — and
    `raw_stderr` mirrors it so audit reads one shape for both streams.
    """

    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    raw_stdout: str | None = None
    raw_stderr: str | None = None


__all__ = ["CommandResult", "SSHConnectionConfig"]
