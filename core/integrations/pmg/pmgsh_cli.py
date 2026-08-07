"""Run `pmgsh` / `pmgconfig` over SSH (T18, V55, V56, V58, V69).

Copied from `noa-old` branch `MCP` (`pmg/integrations/pmgsh_cli.py`). V58 is the whole design of
this module: **argv-only, ⊥ shell string.** Every command is built from a token list through
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
   `core.remote_exec.sudo.build_remote_command`, which reads the resolved username itself — V55
   is a biconditional (prefix ⟺ user ≠ `root`) and a boolean parameter lets one call site break
   it in either direction (T16 deviation (a), same fix). For `root`, the emitted string is
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
   byte-identical, and this module held the third copy; T16 moved it to
   `core.remote_exec.output` naming exactly this port as the reason (V66).

`PMGSH_BINARY` is absolute. Under `sudo -n` the `PATH` is the sudoers `secure_path`, and
`/usr/bin` is not always on the invoking user's own `PATH`. `PMGCONFIG_BINARY` is kept as the
bare name `noa-old` used — `secure_path` carries `/usr/bin` on a default PMG box, and inventing a
path the source never used is the rewrite C13/V69 forbid.

Two success predicates, because PMG answers reads and writes differently:

- `require_pmgsh_success` — plain non-zero is a failure. Used for reads and for `pmgconfig sync`.
- `require_pmg_mutation_success` — accepts a non-zero exit when stdout carries `200 OK`.
  `pmgsh create`/`delete` print the HTTP status of the underlying API call and do not always
  reflect success in their exit code. The check reads `stdout` only, never the combined text: a
  `200 OK` on *stderr* is not this shape and must stay a failure.

Both `SSHExecutionError` and a non-zero exit surface as `PMGSHCLIError`, so a caller catches one
tree (see `core.integrations.pmg.errors`).
"""

from __future__ import annotations

import json
from typing import Final, Protocol

from core.integrations.pmg.errors import PMGSHCLIError
from core.integrations.pmg.ssh import PMGServerSecretLike, resolve_pmg_ssh_config
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

PMGSH_BINARY: Final[str] = "/usr/bin/pmgsh"

# Bare name, verbatim from `noa-old` — see module docstring before making it absolute.
PMGCONFIG_BINARY: Final[str] = "pmgconfig"

# The single PMG config path NOA touches (V58). Whitelisting is PMG's `mynetworks` list; nothing
# in this layer reaches any other endpoint.
MYNETWORKS_PATH: Final[str] = "/config/mynetworks"

# `pmgsh` paints its output when it thinks it has a terminal; `dumb` turns that off at the source
# rather than relying on downstream stripping to catch every sequence.
_PMG_ENV: Final[dict[str, str]] = {"TERM": "dumb"}


def build_pmgsh_command(args: list[str], *, config: SSHConnectionConfig) -> str:
    """Compose one shell-safe `pmgsh` command, escalating iff the SSH user is not root (V55)."""
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
    carry it in the exit code. Read from `stdout` alone, ⊥ the combined text: a `200 OK` that
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
    `core.remote_exec.banner_strip` did not recognise (V56, `noa-old` GH #83) — from turning a
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
    server: PMGServerSecretLike,
    *,
    args: list[str],
    cipher: SecretCipher,
    builder: _CommandBuilder,
) -> CommandResult:
    """Resolve → compose → execute, converting SSH failures into this module's tree.

    Resolve happens first and once, because the composition reads the resolved username to
    decide escalation (V55) — so a row that cannot produce a usable config is refused before a
    socket opens, and the command that runs is built from the config that opened it.
    """
    try:
        ssh_config = resolve_pmg_ssh_config(
            server,
            cipher=cipher,
            require_host_key_fingerprint=True,
        )
        return await ssh_exec(ssh_config, command=builder(args, config=ssh_config))
    except SSHExecutionError as exc:
        # Converted, not wrapped: one exception tree out of this module.
        raise PMGSHCLIError(code=exc.error_code, message=exc.message) from exc


async def run_pmgsh_command(
    server: PMGServerSecretLike,
    *,
    args: list[str],
    cipher: SecretCipher,
) -> CommandResult:
    """Execute `pmgsh <args>` on `server`. Raises `PMGSHCLIError` on any SSH-side failure.

    Returns the raw `CommandResult` — the exit code is the caller's to interpret, because a
    mutation's non-zero exit with `200 OK` on stdout is a success (see
    `require_pmg_mutation_success`).
    """
    return await _run_command(server, args=args, cipher=cipher, builder=build_pmgsh_command)


async def run_pmgconfig_command(
    server: PMGServerSecretLike,
    *,
    args: list[str],
    cipher: SecretCipher,
) -> CommandResult:
    """Execute `pmgconfig <args>` on `server`."""
    return await _run_command(server, args=args, cipher=cipher, builder=build_pmgconfig_command)


async def run_pmgsh_json(
    server: PMGServerSecretLike,
    *,
    args: list[str],
    cipher: SecretCipher,
) -> object:
    """Run `pmgsh <args>` and decode its JSON. Success is checked *before* parsing."""
    result = await run_pmgsh_command(server, args=args, cipher=cipher)
    output = require_pmgsh_success(
        result,
        default_message="PMG command failed",
    )
    return parse_pmgsh_json_output(output)


async def run_pmg_version_probe(server: PMGServerSecretLike, *, cipher: SecretCipher) -> object:
    """Cheapest authenticated call — the credential probe for T54's validate route."""
    return await run_pmgsh_json(server, args=["get", "/version"], cipher=cipher)


async def run_pmg_mynetworks_probe(
    server: PMGServerSecretLike, *, cipher: SecretCipher
) -> dict[str, str]:
    """Read `mynetworks` and return it as validate-route evidence, ⊥ parsed entries.

    The endpoint travels with the output so a validate receipt records *what* was probed, not
    only that something answered. Entry parsing is the tools' job (T30, T31).
    """
    result = await run_pmgsh_command(server, args=["ls", MYNETWORKS_PATH], cipher=cipher)
    output = require_pmgsh_success(
        result,
        default_message="PMG mynetworks probe failed",
    )
    return {"endpoint": MYNETWORKS_PATH, "output": output}


async def run_pmg_mynetworks_list(server: PMGServerSecretLike, *, cipher: SecretCipher) -> str:
    """Raw `pmgsh ls /config/mynetworks` output. Parsed by the tool layer (V59, T30/T31)."""
    result = await run_pmgsh_command(server, args=["ls", MYNETWORKS_PATH], cipher=cipher)
    return require_pmgsh_success(
        result,
        default_message="PMG mynetworks list failed",
    )


async def run_pmg_mynetworks_add(
    server: PMGServerSecretLike, *, cidr: str, cipher: SecretCipher
) -> str:
    """Add one CIDR to `mynetworks` (V60). `cidr` is already validated/normalised by the caller.

    `-cidr <value>` as two argv tokens, so a hostile value cannot become a second flag.
    """
    result = await run_pmgsh_command(
        server,
        args=["create", MYNETWORKS_PATH, "-cidr", cidr],
        cipher=cipher,
    )
    return require_pmg_mutation_success(
        result,
        default_message="PMG mynetworks add failed",
    )


async def run_pmg_mynetworks_delete(
    server: PMGServerSecretLike, *, cidr: str, cipher: SecretCipher
) -> str:
    """Remove one CIDR from `mynetworks` (V61).

    The CIDR is a *path segment* here (`/config/mynetworks/1.2.3.4/32`), not a flag value, and it
    stays one argv token — `command_from_argv` quotes it whole, so the embedded `/` cannot split
    it into two arguments.
    """
    result = await run_pmgsh_command(
        server,
        args=["delete", f"{MYNETWORKS_PATH}/{cidr}"],
        cipher=cipher,
    )
    return require_pmg_mutation_success(
        result,
        default_message="PMG mynetworks remove failed",
    )


async def run_pmgconfig_sync_restart(server: PMGServerSecretLike, *, cipher: SecretCipher) -> str:
    """Apply a `mynetworks` change: `pmgconfig sync --restart 1` (V60, V61).

    Required after every add/remove — `pmgsh` writes PMG's config, and Postfix does not pick the
    change up until this runs. A mutation that skips it looks applied and is not.
    """
    result = await run_pmgconfig_command(server, args=["sync", "--restart", "1"], cipher=cipher)
    return require_pmgsh_success(
        result,
        default_message="PMG config sync failed",
    )


__all__ = [
    "MYNETWORKS_PATH",
    "PMGCONFIG_BINARY",
    "PMGSH_BINARY",
    "build_pmgconfig_command",
    "build_pmgsh_command",
    "parse_pmgsh_json_output",
    "require_pmg_mutation_success",
    "require_pmgsh_success",
    "run_pmg_mynetworks_add",
    "run_pmg_mynetworks_delete",
    "run_pmg_mynetworks_list",
    "run_pmg_mynetworks_probe",
    "run_pmg_version_probe",
    "run_pmgconfig_command",
    "run_pmgconfig_sync_restart",
    "run_pmgsh_command",
    "run_pmgsh_json",
]
