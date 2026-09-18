"""`sudo -n` escalation for remote commands.

Ported from `noa-old` branch `MCP`, where these helpers sat in
`whm/integrations/ssh.py` and the prefix itself was assembled per caller
(`csf_cli.build_csf_command`, `imunify_cli.build_imunify_command`). Two changes, both
required here:

1. **Moved to `core/`.** PMG needs the same escalation as WHM, and this move names
   `sudo -n` as `remote_exec`'s job. One home, never a copy per integration.
2. **The escalation decision is not a caller argument.** `noa-old` exposed
   `build_*_command(args, escalate=…)` and each call site passed
   `escalate=should_escalate(config)`. The sudo-prefix rule is a biconditional — prefix ⟺
   user ≠ `root` — and a
   boolean parameter lets one forgetful call site break it in either direction (root running
   under `sudo`, or a non-root command silently failing on permissions). `build_remote_command`
   reads the resolved username itself, so the ⟺ holds by construction.

`-n` is load-bearing: non-interactive. Without it a host missing the sudoers entry sits on a
password prompt until the command deadline instead of failing fast, and the tool reports a
timeout for what is really a configuration problem.

`is_sudo_rights_failure` is what turns that fast failure into a distinct
`ssh_sudo_required` answer, so an operator is told to fix sudoers rather than hunting a
missing binary.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping

from core.remote_exec.errors import SSHExecutionError
from core.remote_exec.ssh import command_from_argv
from core.remote_exec.types import CommandResult, SSHConnectionConfig

# Dedicated error code for a `sudo -n` missing-rights failure (`noa-old` GH #82):
# distinct from binary-missing (`no_firewall_backend`) and from generic command-failed.
SSH_SUDO_REQUIRED_CODE = "ssh_sudo_required"

# sudo emits these on policy-denial / -n password-required. The plain
# "user is not allowed / not in the sudoers file" messages have NO `sudo:` prefix on
# common sudo versions, so they must be matched explicitly. Avoid matching the bare
# "sudo:" prefix alone — benign warnings like "sudo: unable to resolve host" also
# carry that prefix and would false-positive when the underlying command fails for an
# unrelated reason.
_SUDO_FAILURE_MARKERS = (
    "a password is required",
    "a terminal is required",
    "no tty present",
    "is not allowed to",  # "user X is not allowed to execute '/usr/sbin/csf'"
    "not in the sudoers file",
    "may not run sudo",
    "sudo: no valid sudoers",
    "sudo: sorry",
    "sudo: pam_authenticate",
)

# sudo emits these when the wrapped command itself is absent — distinct from a rights
# failure, so a missing binary is not misreported as ssh_sudo_required.
_SUDO_COMMAND_MISSING_MARKERS = (
    "command not found",  # "sudo: <bin>: command not found"
    "no such file or directory",
)

# POSIX-ish environment name. Validated rather than quoted: a fully quoted `'K=v'` token is
# no longer an assignment to the shell, it is a command name.
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_ROOT_USERNAME = "root"


def requires_escalation(config: SSHConnectionConfig) -> bool:
    """True when the resolved SSH user is not root (csf/imunify/pmgsh need `sudo -n`).

    `noa-old`'s `should_escalate`. Compares the *resolved* username, which the server
    resolvers default to `root` when the column is blank — so an unset username escalates
    only if a resolver deliberately produced something else.
    """
    return config.username != _ROOT_USERNAME


def build_remote_command(
    argv: list[str],
    *,
    config: SSHConnectionConfig,
    env: Mapping[str, str] | None = None,
) -> str:
    """Compose one shell-safe command string, escalating iff the SSH user is not root.

    Token order matches `noa-old` exactly — environment assignments, then `sudo -n`, then the
    binary and its arguments:

        TERM=dumb sudo -n /usr/sbin/csf -g 1.2.3.4

    `TERM=dumb` ahead of `sudo` is intentional: it is the environment csf/pmgsh see, and
    keeping it outside the escalated command means it applies whether or not `sudo` is
    present. Values go through `shlex.quote` individually; keys are validated, because
    quoting a whole `KEY=value` token stops the shell reading it as an assignment.
    """
    parts: list[str] = []
    for key, value in (env or {}).items():
        if not _ENV_NAME_RE.fullmatch(key):
            raise SSHExecutionError(
                code="ssh_command_invalid",
                message="Environment variable name is invalid",
            )
        parts.append(f"{key}={shlex.quote(value)}")

    if requires_escalation(config):
        parts += ["sudo", "-n"]

    # `command_from_argv` quotes every token and rejects an empty argv.
    return " ".join([*parts, command_from_argv(argv)])


def is_sudo_rights_failure(result: CommandResult) -> bool:
    """True when a non-zero result looks like a `sudo -n` missing-rights failure.

    A missing binary (`sudo: <bin>: command not found`) is explicitly excluded so it
    is not misreported as a sudo-rights problem.
    """
    if result.exit_code == 0:
        return False
    stderr = result.stderr.lower()
    if any(marker in stderr for marker in _SUDO_COMMAND_MISSING_MARKERS):
        return False
    return any(marker in stderr for marker in _SUDO_FAILURE_MARKERS)
