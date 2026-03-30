from __future__ import annotations

import json
from typing import Any

from noa_api.core.remote_exec.ssh import SSHExecutionError, command_from_argv, ssh_exec
from noa_api.core.remote_exec.types import CommandResult
from noa_api.whm.integrations.ssh import WHMServerSecretLike, resolve_whm_ssh_config

_IMUNIFY_BINARY = "imunify360-agent"


class ImunifyCLIError(Exception):
    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def build_imunify_command(args: list[str]) -> str:
    """Build an Imunify360 command string with proper quoting."""
    return command_from_argv([_IMUNIFY_BINARY, *args])


def command_output_text(result: CommandResult) -> str:
    """Extract combined stdout/stderr text from command result."""
    parts: list[str] = []
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(stderr)
    return "\n".join(parts).strip()


def parse_imunify_json_output(result: CommandResult) -> dict[str, Any]:
    """
    Parse Imunify JSON output from command result.

    Raises ImunifyCLIError if:
    - Command failed (non-zero exit code)
    - Output is not valid JSON
    """
    output = command_output_text(result)

    if result.exit_code != 0:
        raise ImunifyCLIError(
            code="imunify_command_failed",
            message=output
            or f"Imunify command failed with exit code {result.exit_code}",
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
        raise ImunifyCLIError(
            code="imunify_json_parse_error",
            message=f"Failed to parse Imunify JSON response: {exc}",
        ) from exc


async def run_imunify_command(
    server: WHMServerSecretLike,
    *,
    args: list[str],
) -> CommandResult:
    """
    Execute an Imunify360 command over SSH.

    Raises ImunifyCLIError on SSH execution failure.
    """
    try:
        ssh_config = resolve_whm_ssh_config(
            server,
            require_host_key_fingerprint=True,
        )
        return await ssh_exec(
            ssh_config,
            command=build_imunify_command(args),
        )
    except SSHExecutionError as exc:
        raise ImunifyCLIError(code=exc.code, message=exc.message) from exc


async def check_imunify_binary(server: WHMServerSecretLike) -> bool:
    """
    Check if imunify360-agent binary is available on the server.

    Returns True if found, False otherwise.
    """
    try:
        ssh_config = resolve_whm_ssh_config(
            server,
            require_host_key_fingerprint=True,
        )
        result = await ssh_exec(
            ssh_config,
            command=f"command -v {_IMUNIFY_BINARY}",
        )
        return result.exit_code == 0 and bool(result.stdout.strip())
    except SSHExecutionError:
        return False


async def check_csf_binary(server: WHMServerSecretLike) -> bool:
    """
    Check if CSF binary is available on the server.

    Returns True if found, False otherwise.
    """
    try:
        ssh_config = resolve_whm_ssh_config(
            server,
            require_host_key_fingerprint=True,
        )
        result = await ssh_exec(
            ssh_config,
            command="command -v /usr/sbin/csf",
        )
        return result.exit_code == 0 and bool(result.stdout.strip())
    except SSHExecutionError:
        return False


async def check_firewall_binaries(
    server: WHMServerSecretLike,
) -> dict[str, bool]:
    """
    Check availability of both CSF and Imunify binaries.

    Returns dict with 'csf' and 'imunify' boolean values.
    """
    import asyncio

    csf_task = check_csf_binary(server)
    imunify_task = check_imunify_binary(server)

    csf_available, imunify_available = await asyncio.gather(csf_task, imunify_task)

    return {
        "csf": csf_available,
        "imunify": imunify_available,
    }
