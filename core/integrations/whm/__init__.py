"""WHM/cPanel integration layer.

Copied from `noa-old` branch `MCP` (`noa_api/whm/integrations/`) rather than rewritten —
port, never import, and upstream is no evidence a control works. Two transports to one server,
because WHM splits its surface across them:

- **HTTP** — `client.WHMClient`, the `/json-api/` endpoints. Accounts and suspension.
- **SSH** — `csf_cli`, `imunify_cli`, on `core.remote_exec` with host-key pinning, `sudo -n`
  prefixed for a non-root user, banners stripped before parsing. The firewall. WHM has no API
  for CSF or Imunify.

Modules, one job each:

- `errors`       — `WHMFirewallCLIError` with `CSFCLIError` / `ImunifyCLIError` under it, all
                   `NoaError` so the one shared handler shapes them.
- `client`       — `WHMClient` + `build_whm_client_from_creds`. Normalises WHM's HTTP-200
                   failures into stable `error_code` strings.
- `accounts`     — pure shaping: a `listaccts` row → the fields NOA speaks about, plus the
                   search predicate. Shared by list, search, suspend and unsuspend, hence `core/`.
- `ssh`          — `whm_servers` row → pinned `SSHConnectionConfig`, with the three refusals
                   that happen before a socket opens.
- `csf`          — pure parsing: target classification and `csf -g` verdicts.
- `csf_cli`      — build and run `/usr/sbin/csf` over SSH.
- `imunify`      — pure parsing: `ip-list` responses.
- `imunify_cli`  — build and run `imunify360-agent`, decode its `--json`.
- `availability` — is each backend usable here? The `asyncio.gather` dual probe that backs the
                   zero-backends-means-error rule.
- `firewall_gate` — the one door from that answer to acting on it: zero usable backends is
                   `no_firewall_backend`, never an empty success.

Nothing here reaches for global state. A `SecretCipher` is injected wherever credentials are
decrypted — secrets are Fernet-encrypted at rest and there is no settings singleton in this
repo — and **the SSH side takes a
resolved `SSHConnectionConfig`, not a `whm_servers` row**: `resolve_whm_ssh_config` is
called once by the caller, inside its database session, and every command and probe below runs
off that value. `core.remote_exec.sudo` reads the `sudo -n` decision from the same config.

Consumers: the WHM tools, and the admin server CRUD + validate routes.
Reference doc: `docs/integrations/whm.md`.

Not ported from `MCP`, each for a stated reason (see the module docstrings): the per-reseller
`token_resolver` (no `whm_server_tokens` table in the schema — a spec change, never a build
call), `server_ref` (named against the server-list tool), the never-implement list's client
methods, and the uncalled `addon_csf.cgi` HTTP firewall path.
"""

from core.integrations.whm.accounts import (
    WHMAccount,
    account_matches,
    normalize_whm_account_list,
    normalize_whm_account_summary,
)
from core.integrations.whm.availability import (
    BACKEND_CSF,
    BACKEND_IMUNIFY,
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
from core.integrations.whm.firewall_gate import (
    ERROR_NO_FIREWALL_BACKEND,
    MESSAGE_NO_FIREWALL_BACKEND,
    MESSAGE_SUDO_REQUIRED,
    require_usable_backends,
    run_on_usable_backends,
    usable_backends,
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
    "BACKEND_CSF",
    "BACKEND_IMUNIFY",
    "CSF_BINARY",
    "ERROR_NO_FIREWALL_BACKEND",
    "IMUNIFY_BINARY",
    "MESSAGE_NO_FIREWALL_BACKEND",
    "MESSAGE_SUDO_REQUIRED",
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
    "require_usable_backends",
    "resolve_whm_ssh_config",
    "run_csf_command",
    "run_imunify_command",
    "run_on_usable_backends",
    "usable_backends",
]
