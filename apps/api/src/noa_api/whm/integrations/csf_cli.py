from __future__ import annotations

from noa_api.core.remote_exec.ssh import SSHExecutionError, command_from_argv, ssh_exec
from noa_api.core.remote_exec.types import CommandResult
from noa_api.whm.integrations.ssh import WHMServerSecretLike, resolve_whm_ssh_config

_CSF_BINARY = "/usr/sbin/csf"


class CSFCLIError(Exception):
    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def build_csf_command(args: list[str]) -> str:
    return f"TERM=dumb {command_from_argv([_CSF_BINARY, *args])}"


def command_output_text(result: CommandResult) -> str:
    parts: list[str] = []
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(stderr)
    return "\n".join(parts).strip()


def require_csf_success(result: CommandResult, *, default_message: str) -> str:
    output = command_output_text(result)
    if result.exit_code != 0:
        raise CSFCLIError(
            code="csf_command_failed",
            message=output or default_message,
        )
    return output


async def run_csf_command(
    server: WHMServerSecretLike,
    *,
    args: list[str],
) -> CommandResult:
    try:
        ssh_config = resolve_whm_ssh_config(
            server,
            require_host_key_fingerprint=True,
        )
        return await ssh_exec(
            ssh_config,
            command=build_csf_command(args),
        )
    except SSHExecutionError as exc:
        raise CSFCLIError(code=exc.code, message=exc.message) from exc
