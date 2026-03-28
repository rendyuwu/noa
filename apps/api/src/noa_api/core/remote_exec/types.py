from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SSHConnectionConfig:
    host: str
    port: int
    username: str
    password: str | None = None
    private_key: str | None = None
    private_key_passphrase: str | None = None
    host_key_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class CommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
