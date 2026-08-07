# WHM Integration Reference

Canonical reference for how NOA talks to WHM/cPanel servers. Ported from `noa-old` branch
`MCP` with the integration layer itself (§T.16, C13), scoped to what exists here today.

Update this file whenever a WHM-backed feature, upstream call, or SSH execution path changes —
in the same commit as the code.

## Two transports, one server

WHM splits its surface, so the layer does too:

| Surface | Transport | Module |
|---|---|---|
| Accounts (list, suspend, unsuspend) | HTTPS `/json-api/` + API token | `core/integrations/whm/client.py` |
| Firewall (CSF, Imunify360) | SSH + `sudo -n`, host-key pinned | `core/integrations/whm/{csf,imunify}_cli.py` |

There is no WHM API for CSF or Imunify. That is why a WHM server row carries **both** an API
token and SSH credentials, and why the firewall path inherits every guard in
`core/remote_exec/` (§T.14): banner stripping (§V.56), `sudo -n` escalation (§V.55), host-key
pinning with TOFU refresh (§V.82 — the pin was inert as ported, see §B.2; §V.69 no longer claims
it).

## Connection base

- WHM API base: `https://<whm-host>:<port>` — commonly `2087`.
- SSH host is the **hostname component of `base_url`**; the SSH port is separate and defaults
  to `22`. Port `2087` is the API port and is never used for SSH.
- Auth header: `Authorization: whm <whm-user>:<api-token>`.
- `verify_ssl` is per server row and defaults **on** for WHM (unlike Proxmox).

## Upstream WHM API calls used by code

All go through `WHMClient._get_json_api`, which appends `api.version=1`.

```bash
# List available applications — the cheapest authenticated call; used as the credential probe.
curl -k -H 'Authorization: whm <whm-user>:<api-token>' \
  'https://<whm-host>:2087/json-api/applist?api.version=1'

# List accounts
curl -k -H 'Authorization: whm <whm-user>:<api-token>' \
  'https://<whm-host>:2087/json-api/listaccts?api.version=1'

# Suspend an account
curl -k -H 'Authorization: whm <whm-user>:<api-token>' \
  'https://<whm-host>:2087/json-api/suspendacct?api.version=1&user=<cpanel-user>&reason=<reason>'

# Unsuspend an account
curl -k -H 'Authorization: whm <whm-user>:<api-token>' \
  'https://<whm-host>:2087/json-api/unsuspendacct?api.version=1&user=<cpanel-user>'
```

`suspendacct`'s `reason` is WHM's own suspension note. It is written from the **operator-typed
approval reason** at execute time — it is not a tool-schema parameter, and the LLM never
authors, relays, or sees it (C8, §V.15, §V.43).

### WHM answers a failure with HTTP 200

`metadata.result: 0` is a refusal carried on a success status line. Reading the status code
alone is the single mistake the client exists to prevent. Every outcome normalises to
`{"ok": false, "error_code": ..., "message": ...}`:

| Condition | `error_code` |
|---|---|
| connect / read deadline | `timeout` |
| DNS, refused, reset, TLS | `request_failed` |
| HTTP 401 / 403 | `auth_failed` |
| any other HTTP ≥ 400 | `http_error` |
| body not JSON, not an object, or no `metadata` | `invalid_response` |
| `metadata.result != 1` | `whm_api_error` (carries WHM's `reason`) |

These strings are stable; tools branch on them and V19 sanitises one layer up.

## SSH commands

Composed by `core.remote_exec.sudo.build_remote_command`, which quotes every token and adds
`sudo -n` **iff** the resolved SSH user is not `root` (§V.55). A blank `ssh_username` column
resolves to `root`.

```bash
# CSF — as root
TERM=dumb /usr/sbin/csf -g 1.2.3.4
# CSF — as a non-root operator
TERM=dumb sudo -n /usr/sbin/csf -g 1.2.3.4

# Imunify360 — as root
imunify360-agent ip-list local list --by-ip 1.2.3.4 --json
# Imunify360 — as a non-root operator
sudo -n imunify360-agent ip-list local list --by-ip 1.2.3.4 --json
```

`TERM=dumb` sits ahead of `sudo`, not inside the escalated command: it is the environment csf
sees either way, and without it csf paints its tables with ANSI colour. `/usr/sbin/csf` is an
absolute path because under `sudo -n` the `PATH` is sudoers' `secure_path`;
`imunify360-agent` is unqualified because its install prefix varies across CloudLinux versions
and it is on `PATH` in all of them.

### Availability probing (§V.57)

Both backends are probed in parallel (`asyncio.gather`) before any firewall tool acts:

| Resolved user | Probe |
|---|---|
| `root` | `command -v /usr/sbin/csf`, `command -v imunify360-agent` |
| non-root | `sudo -n /usr/sbin/csf -v`, `sudo -n imunify360-agent version` |

Non-root does **not** use `command -v`: a root-owned `0700` binary is invisible to the
unprivileged user but runs fine under `sudo -n`, so presence-checking would report "not
installed" for a working server. It does not use `sudo -l` either — that depends on a
permissive `listpw` sudoers setting for a question the direct probe already answers.

`FirewallAvailability` carries a third field, `sudo_required`. A binary that is present but
whose escalation was denied comes back `usable=False`, which on its own is indistinguishable
from "not installed" — and telling an operator "no firewall tools on this server" when the
truth is a missing sudoers line sends them hunting an install that is already there
(`noa-old` GH #82).

**Zero backends available is an error, not a success.** The tools raise `no_firewall_backend`
rather than reporting an approved CHANGE that changed nothing (§V.57, §T.68). This layer's job
is only to answer honestly.

### Required sudoers entries

For a non-root SSH user (replace `noa-ops`):

```
noa-ops ALL=(root) NOPASSWD: /usr/sbin/csf, /usr/bin/imunify360-agent
Defaults:noa-ops !requiretty
```

Without `NOPASSWD`, `sudo -n` fails immediately and NOA reports `ssh_sudo_required` — which is
the intended outcome, not a bug. The `-n` is load-bearing: without it a host missing the entry
sits on a password prompt until the command deadline, and the tool reports a timeout for what
is really a configuration problem.

## Output parsing

**CSF** has no machine-readable output. `csf -g` prints iptables tables for humans, so
`core/integrations/whm/csf.py` reads verdicts from marker strings:

| Verdict | Read from |
|---|---|
| `blocked` | `csf.deny`, `Temporary Blocks:`, `DENYIN`, `DENYOUT` |
| `allowlisted` | `csf.allow`, `Temporary Allows:`, `ALLOWIN`, `ALLOWOUT` |
| `not_found` | csf's own `No matches found for …` line |
| `unknown` | anything else |

Two rules that are easy to get wrong: **block beats allow** (an IP in both lists is
operationally still blocked, and T25's release-and-allow makes that intermediate state real),
and **`not_found` requires positive evidence** — silence is `unknown`, so a parse regression
cannot read as a clean IP. Matches are bounded at 20 lines; the result heads for an LLM
context.

**Imunify** answers JSON, so the work is validating a shape. `drop` beats `white`, rows for
other IPs are filtered out even though `--by-ip` claims to have done it, and a malformed row is
skipped rather than raised on — one bad row must not turn a dual-backend preflight into an
error.

The JSON decode has a second layer: if `json.loads` fails, a `raw_decode` scan recovers a
document that has non-JSON text in front of it. That text was the CloudLinux LVE/PAM login
banner (`noa-old` GH #83). The real fix strips banners at the SSH boundary (§V.56); this is a
deliberate belt-and-braces guard for a banner variant the signature gate does not recognise.

## Target classification (§V.54)

`parse_csf_target` classifies without deciding policy:

| Input | Kind |
|---|---|
| `1.2.3.4` | `ip` |
| `1.2.3.0/24` | `cidr` |
| `2001:db8::1` | `ipv6` |
| `2001:db8::/32` | `ipv6_cidr` |
| `app-01.example.com` | `hostname` |
| anything else | `unknown` |

Firewall **CHANGE** tools reject everything but `ip` (§V.54). The preflight **READ** accepts
all kinds — "you asked about an IPv6 address and here is what CSF says" is a useful answer even
where changing it is not permitted. A bare address is never normalised to `/32`: that would
erase the distinction the CHANGE tools check.

## Server references (§V.18)

Tools take a `server_ref`, not an id, because operators name servers the way they remember
them. `resolve_whm_server_ref` (`core/servers/whm_ref.py`) tries three forms in a fixed order
and **never guesses**:

1. **UUID** — resolved by direct read. A well-formed id that matches nothing stops at
   `host_not_found`; it does not fall through, or a mistyped id could resolve to a different
   server whose *name* is that string.
2. **`whm_servers.name`**, case-insensitively.
3. **Hostname of `base_url`**, case-insensitively.

Name beats hostname: the name was typed into NOA on purpose, the hostname is derived. Any tie
at step 2 or 3 returns `host_ambiguous` with a `choices` list (≤ 10 entries, each `id` / `name`
/ `base_url` — no credentials, since this text lands in a transcript). A name tie is reachable
despite the unique index, because Postgres uniqueness is case-sensitive and the match is not.

## Exposed MCP tools

| Tool | Risk | Notes |
|---|---|---|
| `whm_list_servers` | READ | Every configured server, via `WHMServer.to_safe_dict()`. Exposed per DECISIONS §6.6 — the model needs to know which servers exist. The only `*_list_servers` that is exposed. |
| `whm_search_accounts` | READ | `server_ref` + `query` + `limit` (1–100, default 20). Case-insensitive substring of the account username **or** its domain. |

Both the RBAC gate (§V.1, `noa_api/mcp_rbac.py`) and error sanitization (§V.19,
`noa_api/mcp_tools/results.py`) sit in front of every tool, so nothing below is per-tool code.

### `whm_search_accounts` (§T.21)

WHM has no server-side account search, so `listaccts` is fetched whole and the match runs
in-process (`fetch_whm_accounts`, internal per C9/§V.17 — `whm_list_accounts` will share it).
Each row is reduced to the fields NOA speaks about by `core/integrations/whm/accounts.py`:
`user`, `domain`, `email`, `contactemail`, `owner`, `suspended`, `suspendreason`, `suspendtime`,
`is_locked`. Everything else WHM sends (`ip`, `plan`, disk counters, theme) is dropped — the
result lands in a LibreChat transcript that persists in their MongoDB (§V.26). A row with no
`user` is dropped entirely: it is not an account any CHANGE tool could then be called on.

`suspended` and `is_locked` arrive as `0`/`1` or `"0"`/`"1"` depending on the cPanel version and
are normalised to booleans — `"0"` is truthy in Python, so a raw read reports a live account as
suspended. `is_locked` falls back to the older `suspendlock`, including when `is_locked` is
present and JSON-null.

Three deliberate departures from `noa-old`'s version of this tool:

- **Per-field matching.** There the username and domain were joined into one haystack, so a
  query containing a space matched *across* the junction (`"acme sho"` matched user `acme` plus
  domain `shop.example.com`) and returned a row matching nothing the operator typed.
- **Truncation is stated.** The result carries `total_matches` and `truncated`; returning the
  first N silently lets the model report "there are twenty accounts" when there are two hundred
  (§V.71).
- **Sorted by username before the cut**, because `listaccts` order is WHM's own and not
  documented as stable, which would make a truncated answer an arbitrary subset.

`limit` is bounded twice: on the tool's JSON schema (`ge`/`le`, which is what refuses a bad call
over MCP) and inside the tool (`limit_invalid`, which is what holds for an in-process call). A
blank or whitespace-only `query` is `query_required` — §V.21's gate, and one a schema cannot
express since `min_length` counts whitespace. Both guards run before any I/O.

## Error codes

| Code | Raised by | Meaning |
|---|---|---|
| `host_required` | `resolve_whm_server_ref` | `server_ref` blank or whitespace (§V.21). |
| `host_not_found` | `resolve_whm_server_ref` | No server matches the id, name or hostname. |
| `host_ambiguous` | `resolve_whm_server_ref` | Several match; `choices` carries the candidates. |
| `query_required` | `whm_search_accounts` | Blank or whitespace-only search text (§V.21). |
| `limit_invalid` | `whm_search_accounts` | `limit` outside 1–100 (§T.21). |
| `tool_not_permitted` | `RbacToolMiddleware` | Caller lacks the grant, or the name is not a registered tool (§V.1, §V.10). |
| `tool_execution_failed` | `sanitize_tool_errors` | Unmapped exception out of a tool (§V.19). |
| `timeout` | `sanitize_tool_errors` | `TimeoutError` out of a tool (§V.19). |
| `ssh_invalid_host` | `resolve_whm_ssh_config` | `base_url` has no hostname. Bad row. |
| `ssh_not_configured` | `resolve_whm_ssh_config` | No SSH password and no private key stored. |
| `ssh_host_key_not_validated` | `resolve_whm_ssh_config` | Not pinned yet — run admin validate. |
| `ssh_host_key_mismatch` | `ssh_exec` | Presented key ≠ the pin. Investigate; no auto-refresh. |
| `ssh_sudo_required` | csf / imunify CLI | `sudo -n` denied. Fix sudoers, not the install. |
| `csf_command_failed` | `require_csf_success` | csf exited non-zero for another reason. |
| `imunify_command_failed` | `parse_imunify_json_output` | agent exited non-zero for another reason. |
| `imunify_empty_response` | `parse_imunify_json_output` | exit 0, nothing on either stream. |
| `imunify_invalid_response` | `parse_imunify_json_output` | valid JSON, but not an object. |
| `imunify_json_parse_error` | `parse_imunify_json_output` | not JSON, and recovery failed. |

`CSFCLIError` and `ImunifyCLIError` share a `WHMFirewallCLIError` base, all `NoaError`, mapped
once to **502** in `noa_api/api/errors.py` (§V.73).

## Credentials at rest

The API token, SSH password, SSH private key and its passphrase are Fernet-encrypted with the
`enc:v1:fernet:` prefix under `NOA_SECRET_ENCRYPTION_KEY` (C7, §V.48, §V.52). Decryption
happens in exactly two places, both taking an injected `SecretCipher`:

- `build_whm_client_from_creds` — the API token.
- `resolve_whm_ssh_config` — the SSH credentials.

Both use `maybe_decrypt_text`, so a row written before encryption still works — that is what
lets the column be migrated in place.

## Not built yet

| Surface | Task |
|---|---|
| MCP tools: list accounts, suspend/unsuspend, firewall preflight, release-and-allow, allowlist-remove | §T.20, §T.22–§T.26 |
| Admin routes `/admin/whm/servers…` + `POST …/validate` (SSH connect, fingerprint capture, TOFU refresh) | §T.54 |
| Write CRUD on `whm_servers` (`create` / `update` / `delete`) — §T.19 ported the reads only | §T.54 |

Audit is no longer on that list: §V.45's `tool_runs` row is written for every MCP READ by
`ToolRunAuditMiddleware`, beside the RBAC gate (§T.73, §V.83b), so each WHM READ tool below
records requester, redacted arguments, a truncated result summary and timing — including
when it fails.

**Per-reseller API tokens are not ported.** cPanel API tokens enforce reseller *ownership*: a
root-created token cannot mutate an account owned by another reseller, even with the
"Everything" ACL. `noa-old` solved this with a `whm_server_tokens` table and an owner → token
resolver. No such table exists here (§T.4 schema v1 is `whm_servers` only) and no §C or §V
mentions it, so adding it is a spec change, not a build decision. §T.22/§T.23 will hit this on
reseller-owned accounts.

**Never implement** (C22, management policy — not a technical limit): `whm_change_contact_email`,
`whm_change_primary_domain`, `whm_check_binary_exists`, `whm_firewall_denylist_add_ttl`. The
matching `WHMClient` methods are deliberately absent, so the capability is not one line from
exposure. Re-adding any of them is an owner decision.

## Code references

- Package overview: `core/integrations/whm/__init__.py`
- WHM API client: `core/integrations/whm/client.py`
- Account row shaping + search predicate: `core/integrations/whm/accounts.py` (§T.21)
- Row → SSH config, row → API client: `core/integrations/whm/ssh.py`
- CSF: `core/integrations/whm/csf.py` (parsing), `csf_cli.py` (execution)
- Imunify: `core/integrations/whm/imunify.py` (parsing), `imunify_cli.py` (execution)
- Backend availability: `core/integrations/whm/availability.py`
- Errors: `core/integrations/whm/errors.py`
- Shared SSH layer: `core/remote_exec/` (§T.14)
- Server inventory + reference resolution: `core/servers/whm_repository.py`,
  `core/servers/whm_ref.py` (§T.19)
- Exposed READ tools: `apps/api/src/noa_api/mcp_tools/whm_read.py` (§T.19, §T.21)
- MCP tool + registry: `apps/api/src/noa_api/mcp_tools/{whm_read,registry,results,context}.py`
- RBAC gate in front of every tool: `apps/api/src/noa_api/mcp_rbac.py` (§V.1)
- Tests: `apps/api/tests/test_whm_{client,ssh_config,csf_parsing,imunify_parsing}.py`,
  `test_whm_firewall_{cli,availability}.py`,
  `test_whm_{server_ref,server_repository,tools_read}.py`, `test_mcp_tool_rbac.py`
