"""Run `imunify360-agent` over SSH and parse its JSON (T16, V55, V56, V57, V69).

Copied from `noa-old` branch `MCP` (`whm/integrations/imunify_cli.py`), less the binary-probe
half, which moved to `core.integrations.whm.availability` (T16 deviation (d)) — on `MCP` the
CSF probe and the `asyncio.gather` lived inside *this* module, which left `csf_cli` and
`imunify_cli` asymmetric and the import graph pointing the wrong way.

Same escalation change as `csf_cli`: `build_remote_command` decides `sudo -n` from the resolved
username instead of a caller-passed boolean (V55, T16 deviation (a)). No `TERM=dumb` here —
`imunify360-agent` does not colour its output, and `noa-old` did not set it either.

Unqualified binary name, unlike csf's absolute `/usr/sbin/csf`: `imunify360-agent` installs to
different prefixes across CloudLinux versions, and it is on `PATH` (and on sudoers'
`secure_path`) in all of them.

**The JSON parse has two layers, and the second one is a scar.** `json.loads` on the combined
output is the primary path. When that fails, `_json_object_via_raw_decode` scans forward to the
first `{`/`[` and retries with `JSONDecoder.raw_decode`, so a leading non-JSON prefix does not
lose the document. That prefix was the CloudLinux LVE/PAM login banner (`noa-old` GH #83). The
real fix is `core.remote_exec.banner_strip`, which removes it at the SSH boundary (V56) — this
is a belt-and-braces guard for a banner variant the signature gate does not recognise, and it
is deliberately kept: a banner change should degrade to a parsed result, ⊥ to a failed CHANGE.
The same pattern exists in `noa-old`'s `pmg/integrations/pmgsh_cli.py`.

Failure codes, all distinct because their remedies are:

- `ssh_sudo_required`       — sudoers entry missing (`noa-old` GH #82, V55).
- `imunify_command_failed`  — non-zero exit for any other reason; carries the agent's output.
- `imunify_empty_response`  — exit 0, nothing on either stream. `--json` promised a document.
- `imunify_invalid_response`— valid JSON, but not an object.
- `imunify_json_parse_error`— not JSON, and the raw-decode recovery also failed.

A resolved `SSHConnectionConfig` comes in rather than a `whm_servers` row, for the reasons
`csf_cli` states at length (T24): the caller resolves once, inside its database session, and
does the SSH hop after closing it.
"""

from __future__ import annotations

import json
from typing import Any

from core.integrations.whm.errors import ImunifyCLIError
from core.remote_exec.errors import SSHExecutionError
from core.remote_exec.output import command_output_text
from core.remote_exec.ssh import ssh_exec
from core.remote_exec.sudo import (
    SSH_SUDO_REQUIRED_CODE,
    build_remote_command,
    is_sudo_rights_failure,
)
from core.remote_exec.types import CommandResult, SSHConnectionConfig

IMUNIFY_BINARY = "imunify360-agent"


def build_imunify_command(args: list[str], *, config: SSHConnectionConfig) -> str:
    """Compose one shell-safe agent command, escalating iff the SSH user is not root (V55)."""
    return build_remote_command([IMUNIFY_BINARY, *args], config=config)


def _json_object_via_raw_decode(output: str) -> dict[str, Any] | None:
    """Recover a JSON object that has non-JSON text in front of it.

    Scans to the first `{`/`[` and uses `JSONDecoder.raw_decode`, so a surviving banner
    fragment does not corrupt parsing. Returns `None` when nothing decodes to an object.
    Fallback only — the primary fix strips banners at the SSH boundary (V56).
    """
    decoder = json.JSONDecoder()
    for index, char in enumerate(output):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
        return None
    return None


def parse_imunify_json_output(result: CommandResult) -> dict[str, Any]:
    """Validate exit status, then decode the agent's `--json` document.

    Raises `ImunifyCLIError` with the code that names the remedy (see module docstring).
    """
    output = command_output_text(result)

    if result.exit_code != 0:
        if is_sudo_rights_failure(result):
            raise ImunifyCLIError(
                code=SSH_SUDO_REQUIRED_CODE,
                message=output
                or ("imunify360-agent requires passwordless sudo for the configured SSH user"),
            )
        raise ImunifyCLIError(
            code="imunify_command_failed",
            message=output or f"Imunify command failed with exit code {result.exit_code}",
        )

    if not output.strip():
        raise ImunifyCLIError(
            code="imunify_empty_response",
            message="Imunify command returned empty response",
        )

    try:
        parsed = json.loads(output)
        if not isinstance(parsed, dict):
            raise ImunifyCLIError(
                code="imunify_invalid_response",
                message="Imunify response is not a JSON object",
            )
        return parsed
    except json.JSONDecodeError as exc:
        recovered = _json_object_via_raw_decode(output)
        if recovered is not None:
            return recovered
        raise ImunifyCLIError(
            code="imunify_json_parse_error",
            message=f"Failed to parse Imunify JSON response: {exc}",
        ) from exc


async def run_imunify_command(config: SSHConnectionConfig, *, args: list[str]) -> CommandResult:
    """Execute `imunify360-agent <args>` over `config`. Raises `ImunifyCLIError` on SSH failure.

    Returns the raw `CommandResult`; `parse_imunify_json_output` is the separate step, so a
    caller that only needs the exit status does not pay for a decode.
    """
    try:
        return await ssh_exec(config, command=build_imunify_command(args, config=config))
    except SSHExecutionError as exc:
        # Converted, not wrapped: one exception tree out of this module.
        raise ImunifyCLIError(code=exc.error_code, message=exc.message) from exc


__all__ = [
    "IMUNIFY_BINARY",
    "build_imunify_command",
    "parse_imunify_json_output",
    "run_imunify_command",
]
