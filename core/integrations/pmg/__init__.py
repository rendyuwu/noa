"""Proxmox Mail Gateway integration layer.

Copied from `noa-old` branch `MCP` (`noa_api/pmg/integrations/`) rather than rewritten.
One transport, and it is SSH: PMG's API is not exposed to NOA, so everything runs
`pmgsh` on the box. A `pmg_servers` row therefore carries **only** SSH credentials —
no `base_url`, no API token, no `verify_ssl` (`noa-old` carried `base_url`/`verify_ssl` columns
for PMG and never used them; `core.db.models.PMGServer` dropped both when the schema landed).

Modules:

- `errors`     — `PMGSHCLIError`, a `NoaError` so the one shared handler shapes it.
- `ssh`        — `pmg_servers` row → pinned `SSHConnectionConfig`, with the four refusals that
                 happen before a socket opens. Stricter host validation than WHM's, because PMG
                 stores a bare host that nothing has parsed.
- `pmgsh_cli`  — build and run `pmgsh` / `pmgconfig` over SSH: argv-only, `TERM=dumb`,
                 `sudo -n` iff the resolved user is not root, and the two success
                 predicates PMG needs (a mutation reports `200 OK` in its output, not its exit
                 code).
- `mynetworks` — that command output → normalised CIDR entries. `1.2.3.4` and
                 `1.2.3.4/32` are one whitelist entry, so both the operator's target and every
                 stored line are normalised before anything is compared.

`ssh` decrypts, `pmgsh_cli` runs, and the split is load-bearing: as of the whitelist search
landing, the run functions take a resolved `SSHConnectionConfig` rather than a row plus a
`SecretCipher`, so a caller resolves once, closes its database session, and only then reaches
the box (the session-before-hop rule, the change the dual-backend firewall read made on the
WHM side). A row that cannot produce a usable config is refused by the
caller, where the refusal names the PMG server.

`mynetworks` is the only PMG endpoint this layer touches. Everything it exposes is a read
or a write against `/config/mynetworks`, plus the version probe the admin validate route uses.

Consumers: the PMG READ tools `pmg_whitelist_search` and `pmg_whitelist_list`,
which share one read of `mynetworks` and differ only in what they do with the entries; the
CHANGE tool `pmg_whitelist` (still to come); and the admin server CRUD + validate routes.
Reference doc: `docs/integrations/pmg.md`.

Not ported from `MCP`: `pmg/server_ref.py` — reference resolution is inventory, not integration,
so PMG's landed in `core/servers/reference.py` at the whitelist search's landing exactly where
WHM's did at the server-list tool's — and `core/workflows/pmg/` (dropped with chat presentation).
"""
