"""WHM/cPanel integration layer (T16).

Copied from `noa-old` branch `MCP` (`noa_api/whm/integrations/`) rather than rewritten
(C13, V69). Two transports to one server, because WHM splits its surface across them:

- **HTTP** — `client.WHMClient`, the `/json-api/` endpoints. Accounts and suspension.
- **SSH** — `csf_cli`, `imunify_cli`, on `core.remote_exec` with host-key pinning and `sudo -n`
  (V55, V56). The firewall. WHM has no API for CSF or Imunify.

Modules, one job each:

- `errors`       — `WHMFirewallCLIError` with `CSFCLIError` / `ImunifyCLIError` under it, all
                   `NoaError` so the one shared handler shapes them (V73).
- `client`       — `WHMClient` + `build_whm_client_from_creds`. Normalises WHM's HTTP-200
                   failures into stable `error_code` strings.
- `accounts`     — pure shaping: a `listaccts` row → the fields NOA speaks about, plus the
                   search predicate (T21). Shared by T20-T23, hence `core/` (V66).
- `ssh`          — `whm_servers` row → pinned `SSHConnectionConfig`, with the three refusals
                   that happen before a socket opens.
- `csf`          — pure parsing: target classification (V54) and `csf -g` verdicts.
- `csf_cli`      — build and run `/usr/sbin/csf` over SSH.
- `imunify`      — pure parsing: `ip-list` responses.
- `imunify_cli`  — build and run `imunify360-agent`, decode its `--json`.
- `availability` — is each backend usable here? The `asyncio.gather` dual probe behind V57.

Two things are injected rather than imported, both following T15's precedent: a `SecretCipher`
for credentials at rest (C7 — there is no settings singleton in this repo), and the
`SSHConnectionConfig` that `core.remote_exec.sudo` reads the `sudo -n` decision from (V55).

Consumers: the WHM tools (T19-T26), and the admin server CRUD + validate routes (T54). Nothing
imports this yet. Reference doc: `docs/integrations/whm.md`.

Not ported from `MCP`, each for a stated reason (see the module docstrings): the per-reseller
`token_resolver` (no `whm_server_tokens` table in T4's schema — a spec change, ⊥ a build call),
`server_ref` (named in T19's line), the C22 never-implement client methods, and the uncalled
`addon_csf.cgi` HTTP firewall path.
"""

from core.integrations.whm.accounts import (
    WHMAccount,
    account_matches,
    normalize_whm_account_list,
    normalize_whm_account_summary,
)
from core.integrations.whm.availability import (
    BinaryCheck,
    FirewallAvailability,
    check_csf_binary,
    check_firewall_binaries,
    check_imunify_binary,
)
from core.integrations.whm.client import WHMClient, build_whm_client_from_creds
from core.integrations.whm.csf import (
    CSFGrepParsed,
    CSFGrepVerdict,
    CSFTarget,
    CSFTargetKind,
    parse_csf_grep_output,
    parse_csf_target,
)
from core.integrations.whm.csf_cli import (
    CSF_BINARY,
    build_csf_command,
    require_csf_success,
    run_csf_command,
)
from core.integrations.whm.errors import (
    CSFCLIError,
    ImunifyCLIError,
    WHMFirewallCLIError,
)
from core.integrations.whm.imunify import (
    ImunifyIPEntry,
    ImunifyIPListResult,
    ImunifyPurpose,
    ImunifyVerdict,
    format_imunify_matches,
    imunify_entry_to_dict,
    parse_imunify_ip_list_response,
)
from core.integrations.whm.imunify_cli import (
    IMUNIFY_BINARY,
    build_imunify_command,
    parse_imunify_json_output,
    run_imunify_command,
)
from core.integrations.whm.ssh import (
    WHMClientFactory,
    WHMServerSecretLike,
    build_whm_client,
    has_ssh_credentials,
    resolve_whm_ssh_config,
)

__all__ = [
    "CSF_BINARY",
    "IMUNIFY_BINARY",
    "BinaryCheck",
    "CSFCLIError",
    "CSFGrepParsed",
    "CSFGrepVerdict",
    "CSFTarget",
    "CSFTargetKind",
    "FirewallAvailability",
    "ImunifyCLIError",
    "ImunifyIPEntry",
    "ImunifyIPListResult",
    "ImunifyPurpose",
    "ImunifyVerdict",
    "WHMAccount",
    "WHMClient",
    "WHMClientFactory",
    "WHMFirewallCLIError",
    "WHMServerSecretLike",
    "account_matches",
    "build_csf_command",
    "build_imunify_command",
    "build_whm_client",
    "build_whm_client_from_creds",
    "check_csf_binary",
    "check_firewall_binaries",
    "check_imunify_binary",
    "format_imunify_matches",
    "has_ssh_credentials",
    "imunify_entry_to_dict",
    "normalize_whm_account_list",
    "normalize_whm_account_summary",
    "parse_csf_grep_output",
    "parse_csf_target",
    "parse_imunify_ip_list_response",
    "parse_imunify_json_output",
    "require_csf_success",
    "resolve_whm_ssh_config",
    "run_csf_command",
    "run_imunify_command",
]
