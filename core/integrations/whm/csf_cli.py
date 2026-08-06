"""Run `/usr/sbin/csf` over SSH (T16, V55, V57, V69).

Copied from `noa-old` branch `MCP` (`whm/integrations/csf_cli.py`). One structural change:
`noa-old` had `build_csf_command(args, *, escalate: bool)` and every call site passed
`escalate=should_escalate(config)`. T14 moved that decision into
`core.remote_exec.sudo.build_remote_command`, which reads the resolved username itself — V55 is
a biconditional (prefix ⟺ user ≠ `root`) and a boolean parameter lets one forgetful call site
break it in either direction (T16 deviation (a)). The emitted token order is unchanged:

    TERM=dumb sudo -n /usr/sbin/csf -g 1.2.3.4

`TERM=dumb` sits *ahead* of `sudo`, not inside the escalated command: it is the environment csf
sees either way, and csf without it emits terminal control sequences that the parser in
`core.integrations.whm.csf` then has to strip.

`_CSF_BINARY` is an absolute path, not `csf`. Under `sudo -n` the `PATH` is the sudoers
`secure_path`, and `/usr/sbin` is not always on the invoking user's own `PATH`.

Failure handling is the part worth copying. `require_csf_success` splits a non-zero exit into
two answers, because they have different remedies:

- `sudo -n` was denied → `ssh_sudo_required`. Fix the sudoers entry.
- anything else → `csf_command_failed`, carrying csf's own output.

`noa-old` GH #82: reporting the first as "no firewall tools" sent an operator hunting an
install that was already there. `core.remote_exec.sudo.is_sudo_rights_failure` is what
distinguishes them, and it deliberately excludes `command not found` so a genuinely missing
binary is not misread as a rights problem.

Both `SSHExecutionError` and a non-zero exit surface as `CSFCLIError`, so a caller catches one
tree (see `core.integrations.whm.errors`).

`command_output_text` came from `core.remote_exec.output` rather than being copied a third
time — see that module for the count (T16 deviation (f), V66).
"""

from __future__ import annotations

from core.integrations.whm.errors import CSFCLIError
from core.integrations.whm.ssh import WHMServerSecretLike, resolve_whm_ssh_config
from core.remote_exec.errors import SSHExecutionError
from core.remote_exec.output import command_output_text
from core.remote_exec.ssh import ssh_exec
from core.remote_exec.sudo import (
    SSH_SUDO_REQUIRED_CODE,
    build_remote_command,
    is_sudo_rights_failure,
)
from core.remote_exec.types import CommandResult, SSHConnectionConfig
from core.secrets.crypto import SecretCipher

CSF_BINARY = "/usr/sbin/csf"

# csf paints its tables when it thinks it has a terminal; `dumb` turns that off at the source
# rather than relying on the ANSI strip in `csf.py` to catch every sequence.
_CSF_ENV = {"TERM": "dumb"}


def build_csf_command(args: list[str], *, config: SSHConnectionConfig) -> str:
    """Compose one shell-safe csf command, escalating iff the SSH user is not root (V55)."""
    return build_remote_command([CSF_BINARY, *args], config=config, env=_CSF_ENV)


def require_csf_success(result: CommandResult, *, default_message: str) -> str:
    """Return the command's output, or raise with the code that names the remedy."""
    output = command_output_text(result)
    if result.exit_code != 0:
        if is_sudo_rights_failure(result):
            raise CSFCLIError(
                code=SSH_SUDO_REQUIRED_CODE,
                message=output or "csf requires passwordless sudo for the configured SSH user",
            )
        raise CSFCLIError(code="csf_command_failed", message=output or default_message)
    return output


async def run_csf_command(
    server: WHMServerSecretLike,
    *,
    args: list[str],
    cipher: SecretCipher,
) -> CommandResult:
    """Execute `csf <args>` on `server`. Raises `CSFCLIError` on any SSH-side failure.

    Returns the raw `CommandResult` — the exit code is the caller's to interpret, because
    `csf -g` on a clean IP is a success with a "no matches" body, not an error.
    """
    try:
        ssh_config = resolve_whm_ssh_config(
            server,
            cipher=cipher,
            require_host_key_fingerprint=True,
        )
        return await ssh_exec(ssh_config, command=build_csf_command(args, config=ssh_config))
    except SSHExecutionError as exc:
        # Converted, not wrapped: one exception tree out of this module.
        raise CSFCLIError(code=exc.error_code, message=exc.message) from exc


__all__ = [
    "CSF_BINARY",
    "build_csf_command",
    "require_csf_success",
    "run_csf_command",
]
