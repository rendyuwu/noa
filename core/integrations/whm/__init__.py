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
