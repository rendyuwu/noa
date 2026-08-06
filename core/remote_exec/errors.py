"""Remote-execution error (T14, V73).

`noa-old` had `SSHExecutionError(Exception)` with `code` / `message` attributes. Here it
derives from `core.errors.NoaError` instead, because these failures do reach an HTTP
response: T54's `POST /admin/{whm,proxmox,pmg}/servers/{id}/validate` connects over SSH, and
`core/errors.py` is explicit that anything a route may raise subclasses `NoaError` so one
handler shapes every body (V73). A second parallel taxonomy would make "one handler" a lie.

The keyword-only `code=` / `message=` constructor is kept verbatim from `noa-old` so the
~10 copied raise sites in `ssh.py` — and the integration layers landing with T16-T18 — read
identically to the source they were hardened in (C13/V69). `code` maps onto `NoaError`'s
`error_code`, which is the field clients and tests branch on.

Messages here are operator-facing and must stay credential-free (V8): they name *what* the
host refused (auth, host key, timeout), never the key, password or passphrase that was
presented. `ssh_connection_failed` interpolates asyncssh's own text, which reports transport
state, not the secret.

Error codes raised by this package, all stable strings:

- `ssh_command_invalid` — empty command or empty argv; caller bug, ⊥ host fault.
- `ssh_invalid_private_key` — stored key will not import (wrong passphrase, truncated PEM).
- `ssh_host_key_not_validated` — no pin stored yet; `ssh_exec` refuses before connecting.
- `ssh_host_key_mismatch` — host presented a key ≠ the pin. Possible MITM; ⊥ auto-refresh.
- `ssh_host_key_unavailable` — connected, but no host key to capture during TOFU.
- `ssh_auth_failed` — credentials rejected by the host.
- `ssh_timeout` — connect or command exceeded the deadline.
- `ssh_connection_failed` — transport-level failure (DNS, refused, reset).
- `ssh_sudo_required` — see `core.remote_exec.sudo`; raised by callers, ⊥ here.
"""

from __future__ import annotations

from core.errors import NoaError


class SSHExecutionError(NoaError):
    """A remote command could not be run, or could not be trusted to run.

    Instance-level `error_code` / `message` (not class attributes like the auth taxonomy):
    one class covers the whole SSH surface, and splitting it into nine subclasses would be
    the rewrite C13/V69 forbid. `detail` still defaults to `message` via `NoaError`.
    """

    error_code: str = "ssh_execution_failed"
    message: str = "The remote command could not be executed."

    def __init__(self, *, code: str, message: str) -> None:
        self.error_code = code
        self.message = message
        super().__init__(message)


__all__ = ["SSHExecutionError"]
