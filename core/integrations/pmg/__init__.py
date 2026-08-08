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
- `mynetworks` — that command output → normalised CIDR entries (T31, V59). `1.2.3.4` and
                 `1.2.3.4/32` are one whitelist entry, so both the operator's target and every
                 stored line are normalised before anything is compared.

`ssh` decrypts, `pmgsh_cli` runs, and the split is load-bearing: as of T31 the run functions
take a resolved `SSHConnectionConfig` rather than a row plus a `SecretCipher`, so a caller
resolves once, closes its database session, and only then reaches the box (T21's rule, the
change T24 made on the WHM side). A row that cannot produce a usable config is refused by the
caller, where the refusal names the PMG server.

`mynetworks` is the only PMG endpoint this layer touches (V58). Everything it exposes is a read
or a write against `/config/mynetworks`, plus the version probe T54's validate route uses.

Consumers: the PMG tools `pmg_whitelist` (T29), `pmg_whitelist_list` (T30) and
`pmg_whitelist_search` (T31 — the one that exists), plus the admin server CRUD + validate
routes (T54). Reference doc: `docs/integrations/pmg.md`.

Not ported from `MCP`: `pmg/server_ref.py` — reference resolution is inventory, not integration,
so PMG's landed in `core/servers/pmg_ref.py` at T31 exactly where WHM's did at T19 — and
`core/workflows/pmg/` (C16 drops it with chat presentation).
"""

from core.integrations.pmg.errors import PMGSHCLIError
from core.integrations.pmg.mynetworks import (
    MynetworksEntry,
    find_matching_entries,
    normalize_cidr,
    parse_mynetworks_entries,
)
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
    "MynetworksEntry",
    "PMGSHCLIError",
    "PMGServerSecretLike",
    "build_pmgconfig_command",
    "build_pmgsh_command",
    "find_matching_entries",
    "has_ssh_credentials",
    "normalize_cidr",
    "parse_mynetworks_entries",
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
