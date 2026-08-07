"""Proxmox Mail Gateway integration layer (T18).

Copied from `noa-old` branch `MCP` (`noa_api/pmg/integrations/`) rather than rewritten
(C13, V69). One transport, and it is SSH: PMG's API is not exposed to NOA, so everything runs
`pmgsh` on the box (V58, I.ext). A `pmg_servers` row therefore carries **only** SSH credentials —
no `base_url`, no API token, no `verify_ssl` (`noa-old` carried `base_url`/`verify_ssl` columns
for PMG and never used them; `core.db.models.PMGServer` dropped both at T4).

Modules:

- `errors`     — `PMGSHCLIError`, a `NoaError` so the one shared handler shapes it (V73).
- `ssh`        — `pmg_servers` row → pinned `SSHConnectionConfig`, with the four refusals that
                 happen before a socket opens. Stricter host validation than WHM's, because PMG
                 stores a bare host that nothing has parsed.
- `pmgsh_cli`  — build and run `pmgsh` / `pmgconfig` over SSH: argv-only, `TERM=dumb`,
                 `sudo -n` iff the resolved user is not root (V55), and the two success
                 predicates PMG needs (a mutation reports `200 OK` in its output, not its exit
                 code).

Both dependencies are injected rather than imported, following T15: a `SecretCipher` for
credentials at rest (C7 — there is no settings singleton in this repo), and the resolved
`SSHConnectionConfig` that `core.remote_exec.sudo` reads the `sudo -n` decision from (V55).

`mynetworks` is the only PMG endpoint this layer touches (V58). Everything it exposes is a read
or a write against `/config/mynetworks`, plus the version probe T54's validate route uses.

Consumers: the PMG tools `pmg_whitelist` (T29), `pmg_whitelist_list` (T30),
`pmg_whitelist_search` (T31), and the admin server CRUD + validate routes (T54). Nothing imports
this yet. Reference doc: `docs/integrations/pmg.md`.

Not ported from `MCP`: `pmg/server_ref.py` (ref resolution is a tool-layer concern, and PMG's
lands with T29-T31 exactly as WHM's deferred to T19), the `ipaddress` normalisation and
`mynetworks` line parsing in `pmg/tools/whitelist_tools.py` (V59/V60/V61 belong to those same
tools), and `core/workflows/pmg/` (C16 drops it with chat presentation).
"""

from core.integrations.pmg.errors import PMGSHCLIError
from core.integrations.pmg.pmgsh_cli import (
    MYNETWORKS_PATH,
    PMGCONFIG_BINARY,
    PMGSH_BINARY,
    build_pmgconfig_command,
    build_pmgsh_command,
    parse_pmgsh_json_output,
    require_pmg_mutation_success,
    require_pmgsh_success,
    run_pmg_mynetworks_add,
    run_pmg_mynetworks_delete,
    run_pmg_mynetworks_list,
    run_pmg_mynetworks_probe,
    run_pmg_version_probe,
    run_pmgconfig_command,
    run_pmgconfig_sync_restart,
    run_pmgsh_command,
    run_pmgsh_json,
)
from core.integrations.pmg.ssh import (
    PMGServerSecretLike,
    has_ssh_credentials,
    resolve_pmg_ssh_config,
)

__all__ = [
    "MYNETWORKS_PATH",
    "PMGCONFIG_BINARY",
    "PMGSH_BINARY",
    "PMGSHCLIError",
    "PMGServerSecretLike",
    "build_pmgconfig_command",
    "build_pmgsh_command",
    "has_ssh_credentials",
    "parse_pmgsh_json_output",
    "require_pmg_mutation_success",
    "require_pmgsh_success",
    "resolve_pmg_ssh_config",
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
