"""WHM firewall CLI errors.

`noa-old` declared `CSFCLIError` in `csf_cli.py` and `ImunifyCLIError` in `imunify_cli.py`,
both bare `Exception` subclasses with hand-rolled `code`/`message` attributes. Here they
collect into one module under a shared base and derive from `core.errors.NoaError`, for the
reason `core/remote_exec/errors.py` and `core/secrets/errors.py` already state: these reach an
HTTP response — the admin validate route's `POST /admin/whm/servers/{id}/validate` probes the
firewall binaries over SSH — and the shared request-id rule requires one handler shaping every
body. A second parallel taxonomy would make
"one handler" a lie.

The keyword-only `code=` / `message=` constructor is kept verbatim from `noa-old` so the copied
raise sites read identically to the source they were hardened in. `code` maps onto
`NoaError`'s `error_code`, which is the field clients and tests branch on.

One base with two subclasses rather than two unrelated classes: a caller that must handle "the
firewall CLI did not answer usably" — the preflight tool and the merged release+allow
tool both do — should catch one thing, and `STATUS_BY_ERROR` needs one entry. Which
backend failed stays in `error_code`.

Codes raised by this package, all stable strings tests and tools branch on:

- `csf_command_failed`      — `/usr/sbin/csf` exited non-zero for a non-sudo reason.
- `imunify_command_failed`  — `imunify360-agent` exited non-zero for a non-sudo reason.
- `imunify_empty_response`  — exit 0 with nothing on either stream; `--json` promised output.
- `imunify_invalid_response`— parsed, but the top level is not a JSON object.
- `imunify_json_parse_error`— output is not JSON, and the raw-decode recovery also failed.
- `ssh_sudo_required`       — `core.remote_exec.sudo.SSH_SUDO_REQUIRED_CODE`, re-raised here
                              when a firewall command dies on `sudo -n` rights. Distinct from
                              a missing binary on purpose: the remedy is a sudoers entry, and
                              reporting "no firewall tools" would send an operator hunting an
                              install that is already there (`noa-old` GH #82).
- every `SSHExecutionError` code — `run_*_command` converts the transport failure rather than
  letting two exception trees escape one call (`ssh_timeout`, `ssh_auth_failed`,
  `ssh_host_key_mismatch`, `ssh_host_key_not_validated`, `ssh_not_configured`, …).

Messages stay credential-free and carry the command's own output, which names the host's
refusal — never the token, password or key that was presented.
"""

from __future__ import annotations

from core.errors import NoaError


class WHMFirewallCLIError(NoaError):
    """A firewall backend command could not be run, or could not be trusted to have run.

    Instance-level `error_code` / `message`, matching `SSHExecutionError`: one class per
    backend covers its whole surface, and splitting each into a subclass per code would be the
    rewrite the port-not-rewrite rule forbids.
    """

    error_code: str = "firewall_command_failed"
    message: str = "The firewall command could not be executed."

    def __init__(self, *, code: str, message: str) -> None:
        self.error_code = code
        self.message = message
        super().__init__(message)


class CSFCLIError(WHMFirewallCLIError):
    """`/usr/sbin/csf` failed, or the SSH hop to it did."""


class ImunifyCLIError(WHMFirewallCLIError):
    """`imunify360-agent` failed, or returned something that is not usable JSON."""
