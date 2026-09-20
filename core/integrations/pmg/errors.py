"""PMG `pmgsh` CLI errors.

`noa-old` declared `PMGSHCLIError` inside `pmg/integrations/pmgsh_cli.py` as a bare `Exception`
subclass with hand-rolled `code`/`message` attributes. It lands in its own module here and
derives from `core.errors.NoaError`, for the reason `core/remote_exec/errors.py`,
`core/secrets/errors.py` and `core/integrations/whm/errors.py` already state: this reaches an
HTTP response — the admin validate route's `POST /admin/pmg/servers/{id}/validate` probes
`pmgsh` over SSH — and the shared-error-handler rule requires one handler shaping every body.
A second parallel taxonomy would make "one handler" a lie.

The keyword-only `code=` / `message=` constructor is kept verbatim from `noa-old` so the copied
raise sites read identically to the source they were hardened in. `code` maps onto
`NoaError`'s `error_code`, which is the field clients and tests branch on.

One class rather than one per failure mode, matching `SSHExecutionError`: a caller that must
handle "pmgsh did not answer usably" catches one thing, and one `status_code` covers the tree.
Which failure it was stays in `error_code`.

Codes raised by this package, all stable strings tests and tools branch on:

- `pmgsh_command_failed` — `pmgsh`/`pmgconfig` exited non-zero for a non-sudo reason. For a
                           mutation, also raised when the exit was non-zero *and* stdout carried
                           no `200 OK` (see `require_pmg_mutation_success`).
- `pmgsh_json_not_found` — exit 0, but the output held no JSON document at all.
- `pmgsh_json_invalid`   — a JSON document started and then failed to decode.
- `ssh_sudo_required`    — `core.remote_exec.sudo.SSH_SUDO_REQUIRED_CODE`, re-raised here when a
                           `pmgsh` command dies on `sudo -n` rights. Distinct from a missing
                           binary on purpose: the remedy is a sudoers entry, and reporting a
                           generic command failure would send an operator hunting an install that
                           is already there (`noa-old` GH #82).
- every `SSHExecutionError` code — `_run_ssh_command` converts the transport failure rather than
  letting two exception trees escape one call (`ssh_timeout`, `ssh_auth_failed`,
  `ssh_host_key_mismatch`, `ssh_host_key_not_validated`, `ssh_not_configured`,
  `ssh_invalid_host`, `ssh_invalid_port`, …).

Messages stay credential-free and carry the command's own output, which names PMG's refusal
— never the password or key that was presented.
"""

from __future__ import annotations

from core.errors import NoaError


class PMGSHCLIError(NoaError):
    """A `pmgsh`/`pmgconfig` command could not be run, or could not be trusted to have run.

    Instance-level `error_code` / `message`, matching `SSHExecutionError` and
    `WHMFirewallCLIError`: one class covers the whole surface, and splitting it into a subclass
    per code would be the rewrite the port-not-rewrite rule forbids.
    """

    error_code: str = "pmgsh_command_failed"
    message: str = "The PMG command could not be executed."
    # 502, same reading again: NOA works, `pmgsh`/`pmgconfig` on the PMG node did not answer
    # usably. One entry for the whole surface — `PMGSHCLIError` carries the specific
    # `error_code`, including the `SSHExecutionError` codes it converts. The admin validate
    # route
    # answers 200 with `ok:false` instead (see above).
    status_code = 502

    def __init__(self, *, code: str, message: str) -> None:
        self.error_code = code
        self.message = message
        super().__init__(message)
