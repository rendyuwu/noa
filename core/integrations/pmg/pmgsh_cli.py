"""Run `pmgsh` / `pmgconfig` over SSH.

Copied from `noa-old` branch `MCP` (`pmg/integrations/pmgsh_cli.py`). This module's whole design
is **argv-only, never shell string.** Every command is built from a token list through
`command_from_argv`, which `shlex.quote`s each part, so a CIDR that arrives from an LLM tool
argument stays one argument to `pmgsh` instead of becoming shell syntax. There is no remote
`jq`, no remote `awk`, no `pmgdb`, no direct PMG database access — NOA parses stdout locally.

`TERM=dumb` is set for the same reason WHM's csf path sets it: `pmgsh` emits terminal control
sequences when it thinks it has a terminal, and a control sequence in front of a JSON document is
a parse error waiting to happen.

Three structural changes from the source, all noted at their line:

1. **Escalation.** `noa-old` composed `command_from_argv(["TERM=dumb", "/usr/bin/pmgsh", …])`
   directly and had no `sudo -n` path at all, so a non-root `ssh_username` on a `pmg_servers`
   row silently failed on permissions. Both builders now go through
   `core.remote_exec.sudo.build_remote_command`, which reads the resolved username itself — the
   sudo-prefix rule is a biconditional (prefix ⟺ user ≠ `root`) and a boolean parameter lets one
   call site break it in either direction (WHM-port deviation (a), same fix). For `root`, the
   emitted string is
   byte-identical to `noa-old`'s:

       TERM=dumb /usr/bin/pmgsh get /version

   and for anyone else:

       TERM=dumb sudo -n /usr/bin/pmgsh get /version

   `TERM=dumb` stays *ahead* of `sudo`, not inside the escalated command: it is the environment
   `pmgsh` sees either way.

2. **A denied `sudo -n` gets its own code.** Because (1) makes that failure reachable,
   `require_pmgsh_success` splits a non-zero exit the way `require_csf_success` does —
   `ssh_sudo_required` (fix the sudoers entry) vs `pmgsh_command_failed` (carrying pmgsh's own
   output). `noa-old` GH #82: collapsing the two sends an operator hunting a problem that is not
   there. `core.remote_exec.sudo.is_sudo_rights_failure` deliberately excludes
   `command not found`, so a genuinely missing `pmgsh` is not misread as a rights problem.

3. **`command_output_text` is imported, not defined.** `noa-old` carried it three times
   byte-identical, and this module held the third copy; the WHM port moved it to
   `core.remote_exec.output` naming exactly this port as the reason.

`PMGSH_BINARY` is absolute. Under `sudo -n` the `PATH` is the sudoers `secure_path`, and
`/usr/bin` is not always on the invoking user's own `PATH`. `PMGCONFIG_BINARY` is kept as the
bare name `noa-old` used — `secure_path` carries `/usr/bin` on a default PMG box, and inventing a
path the source never used is the rewrite the port-not-rewrite rule forbids.

Two success predicates, because PMG answers reads and writes differently:

- `require_pmgsh_success` — plain non-zero is a failure. Used for reads and for `pmgconfig sync`.
- `require_pmg_mutation_success` — accepts a non-zero exit when stdout carries `200 OK`.
  `pmgsh create`/`delete` print the HTTP status of the underlying API call and do not always
  reflect success in their exit code. The check reads `stdout` only, never the combined text: a
  `200 OK` on *stderr* is not this shape and must stay a failure.

Both `SSHExecutionError` and a non-zero exit surface as `PMGSHCLIError`, so a caller catches one
tree (see `core.integrations.pmg.errors`).

**A resolved `SSHConnectionConfig` comes in, not a `pmg_servers` row**. `noa-old` — and
this module until the whitelist search landed — took the row plus a `SecretCipher` and called
`resolve_pmg_ssh_config` per command. The caller resolves it once instead, which is the change the
dual-backend firewall read made to `core.integrations.whm.csf_cli` for the same reason: the caller
is a tool that has to close its database session *before* the SSH round trip, because holding a
pooled connection open across a hop to someone else's host is how a slow server becomes a
database outage (the session-before-hop rule), and an
ORM row cannot be read after its session closes. Two consequences worth naming:

- the row's four pre-socket refusals (`ssh_invalid_host`, `ssh_invalid_port`,
  `ssh_not_configured`, `ssh_host_key_not_validated`) now raise at the *tool*, where they name
  the PMG server, instead of arriving as a `pmgsh` command failure;
- `require_host_key_fingerprint` becomes the caller's to choose, which is what the admin validate
  flow needs — it connects unpinned on purpose, to capture the value it is about to store, and
  this module hard-coded `True`.
"""

from __future__ import annotations

import json
from typing import Final, Protocol

from core.integrations.pmg.errors import PMGSHCLIError
from core.remote_exec.errors import SSHExecutionError
from core.remote_exec.output import command_output_text
from core.remote_exec.ssh import ssh_exec
from core.remote_exec.sudo import (
    SSH_SUDO_REQUIRED_CODE,
    build_remote_command,
    is_sudo_rights_failure,
)
from core.remote_exec.types import CommandResult, SSHConnectionConfig

PMGSH_BINARY: Final[str] = "/usr/bin/pmgsh"

# Bare name, verbatim from `noa-old` — see module docstring before making it absolute.
PMGCONFIG_BINARY: Final[str] = "pmgconfig"

# The single PMG config path NOA touches. Whitelisting is PMG's `mynetworks` list; nothing
# in this layer reaches any other endpoint.
MYNETWORKS_PATH: Final[str] = "/config/mynetworks"

# `pmgsh` paints its output when it thinks it has a terminal; `dumb` turns that off at the source
# rather than relying on downstream stripping to catch every sequence.
_PMG_ENV: Final[dict[str, str]] = {"TERM": "dumb"}


def build_pmgsh_command(args: list[str], *, config: SSHConnectionConfig) -> str:
    """Compose one shell-safe `pmgsh` command, escalating iff the SSH user is not root."""
    return build_remote_command([PMGSH_BINARY, *args], config=config, env=_PMG_ENV)


def build_pmgconfig_command(args: list[str], *, config: SSHConnectionConfig) -> str:
    """Compose one shell-safe `pmgconfig` command, escalating iff the SSH user is not root."""
    return build_remote_command([PMGCONFIG_BINARY, *args], config=config, env=_PMG_ENV)


def require_pmgsh_success(result: CommandResult, *, default_message: str) -> str:
    """Return the command's output, or raise with the code that names the remedy."""
    output = command_output_text(result)
    if result.exit_code != 0:
        if is_sudo_rights_failure(result):
            raise PMGSHCLIError(
                code=SSH_SUDO_REQUIRED_CODE,
                message=output or "pmgsh requires passwordless sudo for the configured SSH user",
            )
        raise PMGSHCLIError(code="pmgsh_command_failed", message=output or default_message)
    return output


def require_pmg_mutation_success(result: CommandResult, *, default_message: str) -> str:
    """As `require_pmgsh_success`, but a `200 OK` on stdout overrides a non-zero exit.

    `pmgsh create`/`delete` report the underlying API status in their output and do not always
    carry it in the exit code. Read from `stdout` alone, never the combined text: a `200 OK` that
    arrived on stderr is a different shape and stays a failure.
    """
    output = command_output_text(result)
    if result.exit_code != 0 and "200 OK" not in result.stdout:
        if is_sudo_rights_failure(result):
            raise PMGSHCLIError(
                code=SSH_SUDO_REQUIRED_CODE,
                message=output or "pmgsh requires passwordless sudo for the configured SSH user",
            )
        raise PMGSHCLIError(code="pmgsh_command_failed", message=output or default_message)
    return output


def parse_pmgsh_json_output(output: str) -> object:
    """Extract the first JSON document from `pmgsh` output.

    Scans for the first `[` or `{` rather than parsing the whole string, because `pmgsh`
    interleaves status lines (`200 OK`) with its payload and may trail more after it. The
    `raw_decode` recovery is what keeps a status line — or a banner variant
    `core.remote_exec.banner_strip` did not recognise (`noa-old` GH #83) — from turning a
    successful command into a parse error.

    Two distinct failures, because they mean different things: no document at all
    (`pmgsh_json_not_found` — the command answered in prose) versus a document that started and
    then failed to decode (`pmgsh_json_invalid` — truncated or corrupt output).
    """
    decoder = json.JSONDecoder()
    for index, char in enumerate(output):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(output[index:])
        except json.JSONDecodeError as exc:
            raise PMGSHCLIError(
                code="pmgsh_json_invalid",
                message="PMG command output contains malformed JSON",
            ) from exc
        return value

    raise PMGSHCLIError(
        code="pmgsh_json_not_found",
        message="PMG command output did not contain JSON",
    )


class _CommandBuilder(Protocol):
    """Either of the two public builders above, structurally."""

    def __call__(self, args: list[str], *, config: SSHConnectionConfig) -> str: ...


async def _run_command(
    config: SSHConnectionConfig,
    *,
    args: list[str],
    builder: _CommandBuilder,
) -> CommandResult:
    """Compose → execute, converting SSH failures into this module's tree.

    The command is built from the same config that opens the connection, because the
    composition reads the resolved username to decide escalation — a boolean parameter
    would let one call site disagree with the connection it is running over.
    """
    try:
        return await ssh_exec(config, command=builder(args, config=config))
    except SSHExecutionError as exc:
        # Converted, not wrapped: one exception tree out of this module.
        raise PMGSHCLIError(code=exc.error_code, message=exc.message) from exc


async def run_pmgsh_command(config: SSHConnectionConfig, *, args: list[str]) -> CommandResult:
    """Execute `pmgsh <args>` over `config`. Raises `PMGSHCLIError` on any SSH-side failure.

    Returns the raw `CommandResult` — the exit code is the caller's to interpret, because a
    mutation's non-zero exit with `200 OK` on stdout is a success (see
    `require_pmg_mutation_success`).
    """
    return await _run_command(config, args=args, builder=build_pmgsh_command)


async def run_pmgconfig_command(config: SSHConnectionConfig, *, args: list[str]) -> CommandResult:
    """Execute `pmgconfig <args>` over `config`."""
    return await _run_command(config, args=args, builder=build_pmgconfig_command)


async def run_pmgsh_json(config: SSHConnectionConfig, *, args: list[str]) -> object:
    """Run `pmgsh <args>` and decode its JSON. Success is checked *before* parsing."""
    result = await run_pmgsh_command(config, args=args)
    output = require_pmgsh_success(
        result,
        default_message="PMG command failed",
    )
    return parse_pmgsh_json_output(output)


async def run_pmg_version_probe(config: SSHConnectionConfig) -> object:
    """Cheapest authenticated call — the credential probe for the admin validate route."""
    return await run_pmgsh_json(config, args=["get", "/version"])


async def run_pmg_mynetworks_probe(config: SSHConnectionConfig) -> dict[str, str]:
    """Read `mynetworks` and return it as validate-route evidence, never parsed entries.

    The endpoint travels with the output so a validate receipt records *what* was probed, not
    only that something answered. Entry parsing is the tools' job — see
    `core.integrations.pmg.mynetworks`.
    """
    result = await run_pmgsh_command(config, args=["ls", MYNETWORKS_PATH])
    output = require_pmgsh_success(
        result,
        default_message="PMG mynetworks probe failed",
    )
    return {"endpoint": MYNETWORKS_PATH, "output": output}


async def run_pmg_mynetworks_list(config: SSHConnectionConfig) -> str:
    """Raw `pmgsh ls /config/mynetworks` output.

    Text, not entries: `core.integrations.pmg.mynetworks` turns it into normalised CIDRs
    (the exact-membership rule), and keeping the split means this module stays about running
    commands.
    """
    result = await run_pmgsh_command(config, args=["ls", MYNETWORKS_PATH])
    return require_pmgsh_success(
        result,
        default_message="PMG mynetworks list failed",
    )


async def run_pmg_mynetworks_add(config: SSHConnectionConfig, *, cidr: str) -> str:
    """Add one CIDR to `mynetworks`. `cidr` is already validated/normalised by the caller.

    `-cidr <value>` as two argv tokens, so a hostile value cannot become a second flag.
    """
    result = await run_pmgsh_command(config, args=["create", MYNETWORKS_PATH, "-cidr", cidr])
    return require_pmg_mutation_success(
        result,
        default_message="PMG mynetworks add failed",
    )


async def run_pmg_mynetworks_delete(config: SSHConnectionConfig, *, cidr: str) -> str:
    """Remove one CIDR from `mynetworks`.

    The CIDR is a *path segment* here (`/config/mynetworks/1.2.3.4/32`), not a flag value, and it
    stays one argv token — `command_from_argv` quotes it whole, so the embedded `/` cannot split
    it into two arguments.
    """
    result = await run_pmgsh_command(config, args=["delete", f"{MYNETWORKS_PATH}/{cidr}"])
    return require_pmg_mutation_success(
        result,
        default_message="PMG mynetworks remove failed",
    )


async def run_pmgconfig_sync_restart(config: SSHConnectionConfig) -> str:
    """Apply a `mynetworks` change: `pmgconfig sync --restart 1`.

    Required after every add/remove — `pmgsh` writes PMG's config, and Postfix does not pick the
    change up until this runs. A mutation that skips it looks applied and is not.
    """
    result = await run_pmgconfig_command(config, args=["sync", "--restart", "1"])
    return require_pmgsh_success(
        result,
        default_message="PMG config sync failed",
    )
