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

### A row becomes a connection once (§T.24)

Every SSH entry point in this package — `run_csf_command`, `run_imunify_command`,
`check_firewall_binaries` — takes a resolved `SSHConnectionConfig`, **not** a `whm_servers` row
plus a cipher. The caller runs `resolve_whm_ssh_config` once, inside its database session, and
does the SSH hops after closing it: holding a pooled Postgres connection across four handshakes
to someone else's host is how a slow WHM server becomes a database outage, and an ORM row
cannot be read once its session is gone.

Two consequences worth knowing. One decrypt of the stored credentials per tool call rather than
one per command. And the row's three pre-socket refusals below are raised **at the tool**, so an
unvalidated server answers `ssh_host_key_not_validated` instead of "no firewall backends" —
which used to send an operator to install software that was already installed.

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

**Zero backends available is an error, not a success.** The answer is `no_firewall_backend`
rather than a firewall state NOA could not read, or an approved CHANGE that changed nothing
(§V.57). The refusal is raised by `firewall_gate.require_usable_backends`, and every firewall
operation reaches the backends through `firewall_gate.run_on_usable_backends` — one door, so a
tool cannot forget the check and gather nothing (§T.68). `availability`'s job is only to answer
honestly; a false positive here is a silent no-op no gate downstream can catch.

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
cannot read as a clean IP.

Matches are bounded at 20 lines, because the result heads for an LLM context — and
`CSFGrepParsed.total_matches` reports how many there were before the cut (§V.85, §T.24).
Twenty lines with nothing beside them read as "there are twenty entries". The kept lines are
csf's own order rather than a sort: `csf -g` renders the current tables and files, so identical
calls against unchanged state yield an identical prefix, and sorting would scramble the
deny/allow grouping that makes the evidence readable.

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
| `whm_list_accounts` | READ | `server_ref`. Every account on one server, parked at `/tables/{token}` — the rows never enter the transcript (§V.64). |
| `whm_search_accounts` | READ | `server_ref` + `query` + `limit` (1–100, default 20). Case-insensitive substring of the account username **or** its domain. |
| `whm_suspend_account` | **CHANGE** | `server_ref` + `username`. Opens an approval request and suspends nothing; the change runs after an operator decides (§V.16). No reason parameter, ever (C8). |
| `whm_unsuspend_account` | **CHANGE** | `server_ref` + `username`. The mirror, and a separate grant: suspend and unsuspend carry opposite risk, so DECISIONS §9 leaves them two names rather than one `action` enum. Opens an approval request and lifts nothing. No reason parameter, ever (C8). |
| `whm_preflight_firewall_entries` | READ | `server_ref` + `target`. Asks CSF and Imunify360 what they hold for the target. Exposed per DECISIONS §6.5 — the one operator-facing preflight. |

Both the RBAC gate (§V.1, `noa_api/mcp_rbac.py`) and error sanitization (§V.19,
`noa_api/mcp_tools/results.py`) sit in front of every tool, so nothing below is per-tool code.

### `whm_search_accounts` (§T.21)

WHM has no server-side account search, so `listaccts` is fetched whole and the match runs
in-process (`fetch_whm_accounts`, internal per C9/§V.17 — `whm_list_accounts` shares it).
Each row is reduced to the fields NOA speaks about by `core/integrations/whm/accounts.py`:
`user`, `domain`, `email`, `contactemail`, `owner`, `suspended`, `suspendreason`, `suspendtime`,
`is_locked`. Everything else WHM sends (`ip`, `plan`, disk counters, theme) is dropped — the
result lands in a LibreChat transcript that persists in their MongoDB (§V.26). A row with no
`user` is dropped entirely: it is not an account any CHANGE tool could then be called on.

**One of those fields is withheld from this tool's rows: `suspendreason`** (§T.22, C8, §V.96a). NOA
writes the operator's approval reason into WHM's suspension note when it suspends an account,
and WHM returns it on every later `listaccts` — so a search that reported the field would hand
the model, by round trip, the one string C8 says it must never see. The normaliser still carries
it and `whm_list_accounts`' parked table still renders it: that page sits behind the operator's
own session (§V.27), which is where the reason may be read. The list lives at
`ACCOUNT_FIELDS_WITHHELD_FROM_MODEL` in `noa_api/mcp_tools/whm_read.py`.

`suspended` and `is_locked` arrive as `0`/`1` or `"0"`/`"1"` depending on the cPanel version and
are normalised to booleans — `"0"` is truthy in Python, so a raw read reports a live account as
suspended. `is_locked` falls back to the older `suspendlock`, including when `is_locked` is
present and JSON-null — the field `whm_unsuspend_account`'s preflight reads to refuse a lift WHM
would reject (§T.23).

Three deliberate departures from `noa-old`'s version of this tool:

- **Per-field matching.** There the username and domain were joined into one haystack, so a
  query containing a space matched *across* the junction (`"acme sho"` matched user `acme` plus
  domain `shop.example.com`) and returned a row matching nothing the operator typed.
- **Truncation is stated.** The result carries `total_matches` and `truncated`; returning the
  first N silently lets the model report "there are twenty accounts" when there are two hundred.
- **Sorted by username before the cut**, because `listaccts` order is WHM's own and not
  documented as stable, which would make a truncated answer an arbitrary subset.

The last two are **§V.85**, generalised out of this tool: any READ that caps rows carries its
own bound and orders them reproducibly first. §V.85 is not §V.64 — that one offloads a large
listing to the table surface, which is built (§T.56: rows parked in `tool_result_tables`, read
back at `/tables/{token}` behind the operator's own session), while this one drops rows
in-process because the operator asked for a limit. `whm_list_accounts` (§T.20) is the tool that
uses it.

The two rules meet at the cap §T.56 does apply: a parked table holds at most
`RESULT_TABLE_MAX_ROWS` rows and stores the count before that cut beside them, so a capped page
says so rather than reading as a complete one.

### `whm_list_accounts` (§T.20)

The other half of the account pair, split by **size** rather than by subject (§V.64). A search
answers in the transcript; a whole listing does not — a dense server carries thousands of
accounts — so this tool fetches the same `listaccts` through the same internal
(`fetch_whm_accounts`), sorts the rows by username, parks them in `tool_result_tables` and
answers with three things: a one-line summary naming the server that was **actually read**
(the resolved row's name, not the `server_ref` the operator typed), the address of
`/tables/{token}` as plain copyable text, and the iframe resource LibreChat renders.

No rows, no sample row and no column value reach the model — that is the whole of §V.64, and a
"preview" would be exactly the ops data it keeps out of LibreChat's MongoDB (§V.26).

**No `limit` argument.** §V.85's cap exists because an operator asked for one; nothing is
dropped on their behalf here. The only bound is the table's own
(`RESULT_TABLE_MAX_ROWS`), applied at the write and reported in the text and in the result
envelope. The rows are still sorted before they are handed over: the cap is a prefix and never
a re-sort, and `listaccts` order is WHM's own.

**A failure is the ordinary `{"ok": false, …}` envelope**, `choices` and all, even though the
success is content blocks. A table that cannot be *parked* refuses the READ with
`result_table_unavailable` rather than handing back an address with nothing behind it — a dead
link in a transcript that persists is discovered later, by an operator, and a refusal is not.

The result also carries a counts-only structured envelope (`ok`, `total_rows`, `stored_rows`,
`truncated`). It exists for the audit trail: every READ is recorded (§V.45) and the run's
status is read off that envelope, so a content-only answer would file every successful listing
as a failure. It carries no rows, no token and no URL.

`limit` is bounded twice: on the tool's JSON schema (`ge`/`le`, which is what refuses a bad call
over MCP) and inside the tool (`limit_invalid`, which is what holds for an in-process call). A
blank or whitespace-only `query` is `query_required` — §V.21's gate, and one a schema cannot
express since `min_length` counts whitespace. Both guards run before any I/O.

### `whm_suspend_account` (§T.22)

**The first CHANGE tool**, and the one place a NOA-held reason leaves the process. A
`tools/call` here changes nothing: it resolves the server and reads the account through the same
internal the two READs use (`fetch_whm_accounts`, C9/§V.17), writes an `action_requests` row with
that read as its evidence (§V.33/§V.35), and answers with the approval card's address plus the
iframe (§V.24/§V.25). WHM's `suspendacct` is not called at all — the count of requests to that
endpoint at gate time is zero, and that is what the tests assert rather than the shape of the
payload.

There is **no `reason` parameter** and nowhere to add one (C8, §V.15, §V.43). The gate refuses a
reason-shaped argument under any of its spellings even for a direct in-process call.

**An account that is already suspended opens no request.** The preflight is what discovers it, so
the tool answers `{"ok": true, "status": "no_op", …}` and nothing is asked of an operator. That
answer names the account and the server and carries nothing else — in particular not the account
summary, which holds `suspendreason`.

Once an operator approves, `core/approvals/execution.py` runs the **runner** registered for this
tool name (`noa_api/mcp_tools/change_runners.py`). Three things about it:

- **It acts on the server the card described.** `evidence["server_id"]`, not the `server_ref`
  string the model passed: inventory can be edited between a request and its approval, and
  re-resolving would be a second resolution that can disagree with the one the decision rests on
  (§V.33).
- **The suspension note is the operator's reason.** `load_authorized` reads
  `action_requests.reason` — non-blank by then, because T34's
  `ck_action_requests_decided_reason` refuses a decided row without one — and the runner sends it
  as `suspendacct`'s `reason`. The runner does **not** echo it back in its payload:
  `tool_runs.result_summary` is derived from that payload and `noa_get_action_result` returns the
  summary to a model, so an echo would reach the LLM through §V.45's audit row (§V.96b). Not
  through the receipt — `core/approvals/results.py` leaves `include_receipt` at its default, so
  that reader never joins `action_receipts` (§V.76).
- **The change is re-read, and the read has three answers.** Suspended → done and verified. Still
  live → `postflight_failed`, because WHM accepted a call that did not take. The confirming read
  itself failing → `{"ok": true, "verified": false, "verification": "unavailable"}`, which is
  §V.62's rule one system over: verification-unavailable is not verification, and it is not a
  failure either — reporting one would send an operator to re-suspend an account that may already
  be suspended.

A server that has been deleted between approval and execution is `whm_server_unavailable`, before
the mutation rather than after it.

### `whm_unsuspend_account` (§T.23)

`whm_suspend_account`'s mirror, and everything between the two names is one implementation
(§V.66): the same preflight (`collect_account_state` over `fetch_whm_accounts`), the same gate,
the same resolution of the approved change onto a client, and one postflight that differs only in
the value `suspended` must hold once the change took. **Not merged into one tool with an `action`
enum** — DECISIONS §9 keeps the pair as two names because the two directions carry opposite risk,
which is also what lets a role be granted one and not the other.

Three states, and two of them open no approval request:

- **not suspended** → `{"ok": true, "status": "no_op", …}`. There is nothing to lift, so there is
  nothing for an operator to authorise. The suspend tool's no-op, running the other way.
- **suspension locked** → `account_suspension_locked`, refused before a card exists. WHM's
  `unsuspendacct` will not lift a locked suspension, so the request would buy an operator's
  decision and then a failed run. The lock is on the summary the preflight already read
  (`is_locked`, falling back to the older `suspendlock`).
- **suspended and unlocked** → the approval request, the card address and the iframe, exactly as
  for a suspension.

The lock guard fires on a **positive** lock only. `listaccts` omits the field entirely on cPanel
versions that do not have it, and refusing every unsuspension on those servers would cost more
than the failure it prevents — so WHM's own refusal at execute time stays the authoritative
answer. It arrives as `whm_api_error` carrying WHM's `reason`, which is the sentence that names
what an administrator has to unlock; this guard is the cheap early half of it.

**Nothing is written out to WHM but the username.** `unsuspendacct` has no note field, so the
runner never reads `request.reason` and §V.96's return paths do not open on this side. The test
asserts the outgoing query *exactly* rather than the absence of one key name, so a note parameter
added later under any spelling goes red.

What §V.96a does bite here is the read: an account being unsuspended is suspended right now, so
its `listaccts` row carries `suspendreason` — the operator's own words from the suspension. That
summary goes onto the approval row as evidence, where the card and §V.27's requester-match are
its only readers (a model cannot reach it — `ActionResultView` has no field for evidence,
§V.76). The two answers this tool puts in a *transcript* — the no-op and the locked refusal — are
built from the username and the server name rather than from the summary, and both are asserted
for the note.

The postflight has the same three answers as the suspension's, with the direction reversed: no
longer suspended → done and verified; still suspended → `postflight_failed`; the confirming read
itself failing → `verification: "unavailable"` (§V.62).

### `whm_preflight_firewall_entries` (§T.24)

Step 1 of the firewall flow (DECISIONS §6.5): *check whether an address is denied → release it →
allowlist it*. It is exposed rather than internal because the decision it feeds is the
operator's — they read the verdict and decide whether to call the release tool at all — so §3's
"a preflight runs inside its workflow" rule (C9, §V.17) does not reach it. It is the only
preflight in that position.

Order of operations: refuse a blank `target` (`target_required`) and an unclassifiable one
(`invalid_target`), both before any I/O; resolve the `server_ref` and the row's SSH config in
one database session and close it; probe both backends; query only the usable ones, in parallel.

The result:

| Field | Meaning |
|---|---|
| `combined_verdict` | `blocked` \| `allowlisted` \| `not_found` \| `unknown` (§V.86) |
| `unanswered_backends` | Usable backends that produced no verdict — failed, or CSF's `unknown` |
| `available_backends` | `{"csf": …, "imunify": …}` — what could be run at all |
| `sudo_required` | A backend was present but its `sudo -n` was denied (GH #82) |
| `target_kind` | `parse_csf_target`'s classification, so the model need not classify an address |
| `matches` / `total_matches` / `truncated` | The evidence lines, and their bound (§V.85) |
| `csf` / `imunify` | Each backend's own verdict, or the code its failure carries |

Three deliberate departures from `noa-old`'s version:

- **No raw output.** It returned csf's whole `-g` dump and Imunify's whole JSON document. Old
  V75 (DECISIONS §6.5) says the before-state shows the `csf.deny` / `csf.allow` log line and
  never a raw iptables table, and the result persists in LibreChat's MongoDB (§V.26).
- **A verdict read from a subset says so** (§V.86). It computed the combined verdict from
  whichever backend succeeded and otherwise fell through to `not_found` — so a broken CSF plus a
  clean Imunify reported "this address is not blocked" on a box whose *blocking* backend was
  silent, a fabrication the tool authored. Here the backends that did not answer are named, and
  a call where nothing answered is `unknown`, never `not_found`. §V.57 bounds only the zero
  case; this is the partial one.
- **Evidence is not repeated per backend.** The lines already say which system produced them.

## Error codes

| Code | Raised by | Meaning |
|---|---|---|
| `host_required` | `resolve_whm_server_ref` | `server_ref` blank or whitespace (§V.21). |
| `host_not_found` | `resolve_whm_server_ref` | No server matches the id, name or hostname. |
| `host_ambiguous` | `resolve_whm_server_ref` | Several match; `choices` carries the candidates. |
| `query_required` | `whm_search_accounts` | Blank or whitespace-only search text (§V.21). |
| `limit_invalid` | `whm_search_accounts` | `limit` outside 1–100 (§T.21). |
| `target_required` | `whm_preflight_firewall_entries` | Blank or whitespace-only target (§V.21). |
| `invalid_target` | `whm_preflight_firewall_entries` | Not an IP, network or hostname (§V.54). |
| `no_firewall_backend` | `firewall_gate.require_usable_backends` | Neither backend could be run, on any firewall tool (§V.57, §T.68). |
| `invalid_response` | `whm_preflight_firewall_entries` | `csf -g` exited 0 with nothing to read. |
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
| MCP tools: release-and-allow, allowlist-remove | §T.25–§T.26 |
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
mentions it, so adding it is a spec change, not a build decision. §T.22 and §T.23 are both built
now and both hit this on reseller-owned accounts: WHM refuses the mutation, the runner passes
`whm_api_error` and WHM's own `reason` through to the receipt, and the change is recorded as not
having happened — a named failure rather than a silent one, which is the most this repo can
truthfully do without that table.

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
- MCP tools: `apps/api/src/noa_api/mcp_tools/whm_read.py` (§T.19, §T.21),
  `apps/api/src/noa_api/mcp_tools/whm_firewall.py` (§T.24)
- Exposed READ tools: `apps/api/src/noa_api/mcp_tools/whm_read.py` (§T.19, §T.21)
- MCP tool + registry: `apps/api/src/noa_api/mcp_tools/{whm_read,registry,results,context}.py`
- RBAC gate in front of every tool: `apps/api/src/noa_api/mcp_rbac.py` (§V.1)
- Tests: `apps/api/tests/test_whm_{client,ssh_config,csf_parsing,imunify_parsing}.py`,
  `test_whm_firewall_{cli,availability}.py`,
  `test_whm_{server_ref,server_repository,tools_read}.py`, `test_mcp_tool_rbac.py`
