"""Remote command execution over SSH (T14).

Copied from `noa-old` branch `MCP` rather than rewritten (C13, V69): banner stripping,
`sudo -n` escalation, host-key pinning and TOFU refresh were each paid for in a production
incident, and the false-positive guards are the expensive part of all four.

Four modules, one job each:

- `types`        — `SSHConnectionConfig` (host + credentials + the pin), `CommandResult`
                   (parsed *and* raw streams, V56).
- `errors`       — `SSHExecutionError`, a `NoaError` so the one shared handler shapes it (V73).
- `banner_strip` — signature-gated removal of CloudLinux LVE/PAM banners (V56).
- `ssh`          — pinned `ssh_exec`, TOFU `ssh_get_host_fingerprint`, `command_from_argv`.
- `sudo`         — `sudo -n` iff the resolved user is not root (V55), and the
                   rights-failure classifier that makes `ssh_sudo_required` distinct.
- `output`       — `command_output_text`, the both-streams view every CLI wrapper needs.
                   Added with T16, which is where the third copy of it would have landed.

Consumers: WHM csf/imunify (T16), Proxmox (T17), PMG `pmgsh` (T18), and the admin
server-validate endpoints (T54), which is where `ssh_get_host_fingerprint` earns its place.
"""

from core.remote_exec.banner_strip import strip_ssh_banners
from core.remote_exec.errors import SSHExecutionError
from core.remote_exec.output import command_output_text
from core.remote_exec.ssh import (
    command_from_argv,
    ssh_exec,
    ssh_get_host_fingerprint,
)
from core.remote_exec.sudo import (
    SSH_SUDO_REQUIRED_CODE,
    build_remote_command,
    is_sudo_rights_failure,
    requires_escalation,
)
from core.remote_exec.types import CommandResult, SSHConnectionConfig

__all__ = [
    "SSH_SUDO_REQUIRED_CODE",
    "CommandResult",
    "SSHConnectionConfig",
    "SSHExecutionError",
    "build_remote_command",
    "command_from_argv",
    "command_output_text",
    "is_sudo_rights_failure",
    "requires_escalation",
    "ssh_exec",
    "ssh_get_host_fingerprint",
    "strip_ssh_banners",
]
