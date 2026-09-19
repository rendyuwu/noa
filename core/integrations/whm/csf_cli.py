"""Run `/usr/sbin/csf` over SSH.

Copied from `noa-old` branch `MCP` (`whm/integrations/csf_cli.py`). One structural change:
`noa-old` had `build_csf_command(args, *, escalate: bool)` and every call site passed
`escalate=should_escalate(config)`. That decision moved into
`core.remote_exec.sudo.build_remote_command`, which reads the resolved username itself — the
escalation rule is a biconditional (prefix ⟺ user ≠ `root`) and a boolean parameter lets one
forgetful call site break it in either direction. The emitted token order is unchanged:

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

**A resolved `SSHConnectionConfig` comes in, not a `whm_servers` row**. `noa-old` — and
this module until the firewall read — took the row plus a `SecretCipher` and called
`resolve_whm_ssh_config`
per command. The caller resolves it once instead, because the caller is a tool that has to
close its database session *before* the SSH round trip: holding a pooled connection open across
a hop to someone else's host is how a slow server becomes a database outage — the account
search's rule — and an
ORM row cannot be read after its session closes. One resolve per call also means one decrypt of
the stored credentials rather than one per command, and the row's three pre-socket refusals
(`ssh_invalid_host`, `ssh_not_configured`, `ssh_host_key_not_validated`) now name the WHM server
to the operator instead of arriving as a firewall-command failure.

`command_output_text` came from `core.remote_exec.output` rather than being copied a third
time — one helper, not two; see that module for the count.
"""

from __future__ import annotations

from core.integrations.whm.errors import CSFCLIError
from core.remote_exec.output import command_output_text
from core.remote_exec.ssh import run_cli
from core.remote_exec.sudo import (
    SSH_SUDO_REQUIRED_CODE,
    build_remote_command,
    is_sudo_rights_failure,
)
from core.remote_exec.types import CommandResult, SSHConnectionConfig

CSF_BINARY = "/usr/sbin/csf"

# csf paints its tables when it thinks it has a terminal; `dumb` turns that off at the source
# rather than relying on the ANSI strip in `csf.py` to catch every sequence.
_CSF_ENV = {"TERM": "dumb"}


def build_csf_command(args: list[str], *, config: SSHConnectionConfig) -> str:
    """Compose one shell-safe csf command, escalating iff the SSH user is not root."""
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


async def run_csf_command(config: SSHConnectionConfig, *, args: list[str]) -> CommandResult:
    """Execute `csf <args>` over `config`. Raises `CSFCLIError` on any SSH-side failure.

    Returns the raw `CommandResult` — the exit code is the caller's to interpret, because
    `csf -g` on a clean IP is a success with a "no matches" body, not an error.
    """
    return await run_cli(config, build_csf_command(args, config=config), error_cls=CSFCLIError)
