"""Remote-execution error.

`noa-old` had `SSHExecutionError(Exception)` with `code` / `message` attributes. Here it derives
from `core.errors.NoaError` instead, because these failures do reach an HTTP response: the admin
validate route's `POST /admin/{whm,proxmox,pmg}/servers/{id}/validate` connects over SSH, and
`core/errors.py` is explicit that anything a route may raise subclasses `NoaError` so one handler
shapes every body. A second parallel taxonomy would make "one handler" a lie.

The keyword-only `code=` / `message=` constructor is kept verbatim from `noa-old` so the ~10 copied
raise sites in `ssh.py` — and the integration layers landing with the WHM/Proxmox/PMG ports — read
identically to the source they were hardened in. `code` maps onto `NoaError`'s `error_code`, which
is the field clients and tests branch on.

Messages here are operator-facing and must stay credential-free: they name *what* the
host refused (auth, host key, timeout), never the key, password or passphrase that was
presented. `ssh_connection_failed` interpolates asyncssh's own text, which reports transport
state, not the secret.

Error codes raised by this package, all stable strings:

- `ssh_command_invalid` — empty command or empty argv; caller bug, never host fault.
- `ssh_invalid_private_key` — stored key will not import (wrong passphrase, truncated PEM).
- `ssh_host_key_not_validated` — no pin stored yet; `ssh_exec` refuses before connecting.
- `ssh_host_key_mismatch` — host presented a key ≠ the pin. Possible MITM; never auto-refresh.
- `ssh_host_key_unavailable` — connected, but no host key to capture during TOFU.
- `ssh_auth_failed` — credentials rejected by the host.
- `ssh_timeout` — connect or command exceeded the deadline.
- `ssh_connection_failed` — transport-level failure (DNS, refused, reset).
- `ssh_sudo_required` — see `core.remote_exec.sudo`; raised by callers, never here.
"""

from __future__ import annotations

from core.errors import NoaError


class SSHExecutionError(NoaError):
    """A remote command could not be run, or could not be trusted to run.

    Instance-level `error_code` / `message` (not class attributes like the auth taxonomy): one class
    covers the whole SSH surface, and splitting it into nine subclasses would be the rewrite the
    port-not-rewrite rule forbids. `detail` still defaults to `message` via `NoaError`.
    """

    error_code: str = "ssh_execution_failed"
    message: str = "The remote command could not be executed."
    # 502: a host NOA depends on refused, timed out, or presented an unexpected host key.
    # Declared so it does not take `NoaError`'s 503, which reads as "authentication is
    # unclassified and NOA may be down", which is the wrong answer for a working NOA and a
    # broken remote. One entry for the whole SSH surface because `SSHExecutionError` carries
    # the specific `error_code`.
    #
    # The admin validate route does **not** refine this per code, contrary to what this
    # comment predicted when `core/remote_exec/` was ported.
    # Its validate route catches the whole tree and answers **200** with
    # `{ok:false, error_code, message}`: an operator pressing Validate asked "does this server
    # answer?", and "no, `ssh_timeout`" is that question's *answer*, not a failure of the
    # request. The panel reads `result.ok` for exactly that reason
    # (`apps/admin-web/.../whm-status.ts::deriveWhmValidationStatus`), so a 502 there would
    # throw at the transport and render as an unhandled error instead of a red status chip.
    # This 502 therefore covers the *tool* paths only, where the tools sanitise raw exceptions
    # to a code.
    status_code = 502

    def __init__(self, *, code: str, message: str) -> None:
        self.error_code = code
        self.message = message
        super().__init__(message)
