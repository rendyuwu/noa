# WHM Integration Reference

Canonical reference for how NOA talks to WHM/cPanel servers. Ported from `noa-old` branch
`MCP` with the integration layer itself (the WHM port — copied, never rewritten), scoped to what exists here today.

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
`core/remote_exec/`: banner stripping before parsing, `sudo -n` escalation, host-key
pinning with TOFU refresh (the pin was inert as ported — an off switch that read as a pin;
upstream provenance no longer claims it).

## Connection base

- WHM API base: `https://<whm-host>:<port>` — commonly `2087`.
- SSH host is the **hostname component of `base_url`**; the SSH port is separate and defaults
  to `22`. Port `2087` is the API port and is never used for SSH.
- Auth header: `Authorization: whm <whm-user>:<api-token>`.
- `verify_ssl` is per server row and defaults **on** for WHM (unlike Proxmox).

### Deadlines

Four of them, and they are deliberately not one number
(`core.integrations.whm.client._split_timeout`):

| Deadline | Value | What it covers |
|---|---|---|
| connect | 10 s | Reaching the host at all. Fixed. |
| read | **120 s**, `WHM_READ_TIMEOUT_SECONDS` | WHM working behind an open socket. |
| write | 30 s | Sending the request. Fixed. |
| pool | 10 s | Waiting for a connection. Fixed. |

Only the read deadline is configurable, and it is the only one a slow WHM call consumes. A
single scalar would set all four alike, so raising the budget for a slow call would also mean an
unreachable host hanging for two minutes before being called unreachable.

**120 s is measured, not inherited.** Against the production server on 2026-09-11,
`unsuspendacct` took **52.91 s** and `suspendacct` **12.09 s**. The previous value — 20 s,
carried over from the reference repository with no decision behind it — was below the first of
those, so *every* unsuspend timed out while WHM went on to complete it, and NOA reported a
failure for a change that had landed. 60 s was rejected as too close to the measured maximum on
a server the operator reports as spiky. Raising a deadline hides how slow a server has got, so
the `approved_change_execution_finished` log event carries `duration_ms` for every change beside
its `status` and `error_code`.

A caller may shorten the **read** deadline for one call, and that override moves nothing else.
Two callers do:

- the confirming read a failed change takes (`WHM_CONFIRM_READ_TIMEOUT_SECONDS`, 30 s) — a read
  taken to find out what a change did has no business waiting as long as the change itself;
- the admin validate probe (`WHM_VALIDATE_READ_TIMEOUT_SECONDS`, 20 s) — `myprivs` is a read, an
  admin is holding an HTTP request open while it runs, and the 120 s above was measured for a
  write. It is applied per call because only the MCP wiring binds the configured deadline onto a
  client factory, so a probe left on the client's default would wait the tool path's length no
  matter what `WHM_READ_TIMEOUT_SECONDS` is set to.

A timed-out call is not a failed change. WHM may have completed the mutation after the socket
gave up, so the runner records `verification: "unavailable"` rather than claiming a re-read it
never made, the approval card headlines `Outcome unknown`, and `noa_get_action_result` answers
`change_verification: "unavailable"` beside the failed run.

## Upstream WHM API calls used by code

All go through `WHMClient._get_json_api`, which appends `api.version=1`.

```bash
# Report granted ACLs — the credential probe at Validate (reports the ACL set), replacing `applist`.
curl -k -H 'Authorization: whm <whm-user>:<api-token>' \
  'https://<whm-host>:2087/json-api/myprivs?api.version=1'

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

### `myprivs` and the ACL set

`myprivs` replaced `applist` as the credential probe because `applist` is not in cPanel's ACL
chart at all, so nothing documents what gates it — and a restricted token *is* refused calling an
unmapped function: `version` was measured denied for one (measured on a live host), which is what tells us
`applist` is not a safe stand-in either. So a healthy reseller row could validate red for a
reason that says nothing about whether it may actually suspend an account. `myprivs` answers the
question Validate needs: which ACLs this token holds.

The shape is two traps deep. `data.privileges` is a **list holding exactly one object** — not
the object itself — so the unwrap takes its first element. And the granted value's *type* is not
stable across credentials: on the same host, for the same ACL, root answered the integer `1` and
a reseller answered the string `"1"` (measured on a live host) — so a granted check keyed on type would
silently read a scoped reseller credential as ungranted. Not-granted has three measured
spellings — the integer `0`, the empty string `""`, and the key absent from the object entirely —
and an unrecognised value fails closed alongside them rather than being guessed toward "granted".

A row missing `suspend-acct` is refused at Validate with `whm_token_acl_insufficient`: one ACL
covers both directions (cPanel's chart names it once, "suspend and unsuspend"), so there is no
second grant to check. A row missing `list-accts` is named in the validate message but
**not** refused — a listing is a lesser capability than a mutation, and gating on it would refuse
a row that only ever backs `whm_list_accounts` / `whm_search_accounts`.

`suspendacct`'s `reason` is WHM's own suspension note. It is written from the **operator-typed
approval reason** at execute time — it is not a tool-schema parameter, and the LLM never
authors, relays, or sees it — one operator-typed field, entered at decision time.

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

These strings are stable; tools branch on them and raw exceptions sanitise to a code one layer up.

## SSH commands

Composed by `core.remote_exec.sudo.build_remote_command`, which quotes every token and adds
`sudo -n` **iff** the resolved SSH user is not `root`. A blank `ssh_username` column
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

### A row becomes a connection once

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

### Availability probing

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
(zero backends is an error, never a success). The refusal is raised by `firewall_gate.require_usable_backends`, and every firewall
operation reaches the backends through `firewall_gate.run_on_usable_backends` — one door, so a
tool cannot forget the check and gather nothing. `availability`'s job is only to answer
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
operationally still blocked, and release-and-allow makes that intermediate state real),
and **`not_found` requires positive evidence** — silence is `unknown`, so a parse regression
cannot read as a clean IP.

Matches are bounded at 20 lines, because the result heads for an LLM context — and
`CSFGrepParsed.total_matches` reports how many there were before the cut.
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
banner (`noa-old` GH #83). The real fix strips banners at the SSH boundary; this is a
deliberate belt-and-braces guard for a banner variant the signature gate does not recognise.

## Target classification

`parse_csf_target` classifies without deciding policy:

| Input | Kind |
|---|---|
| `1.2.3.4` | `ip` |
| `1.2.3.0/24` | `cidr` |
| `2001:db8::1` | `ipv6` |
| `2001:db8::/32` | `ipv6_cidr` |
| `app-01.example.com` | `hostname` |
| anything else | `unknown` |

Firewall **CHANGE** tools reject everything but `ip` (IPv4 only). The preflight **READ** accepts
all kinds — "you asked about an IPv6 address and here is what CSF says" is a useful answer even
where changing it is not permitted. A bare address is never normalised to `/32`: that would
erase the distinction the CHANGE tools check.

## Server references

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
| `whm_list_servers` | READ | Every configured server, via `WHMServer.to_safe_dict()`. Exposed per DECISIONS section 6.6 — the model needs to know which servers exist. The only `*_list_servers` that is exposed. |
| `whm_list_accounts` | READ | `server_ref`. Every account on one server, parked at `/tables/{token}` — the rows never enter the transcript (summary plus table URL). |
| `whm_search_accounts` | READ | `server_ref` + `query` + `limit` (1–100, default 20). Case-insensitive substring of the account username **or** its domain. |
| `whm_suspend_account` | **CHANGE** | `server_ref` + `username`. Opens an approval request and suspends nothing; the change runs after an operator decides (READ now, CHANGE through the gate). No reason parameter, ever. |
| `whm_unsuspend_account` | **CHANGE** | `server_ref` + `username`. The mirror, and a separate grant: suspend and unsuspend carry opposite risk, so DECISIONS section 9 leaves them two names rather than one `action` enum. Opens an approval request and lifts nothing. No reason parameter, ever. |
| `whm_preflight_firewall_entries` | READ | `server_ref` + `target`. Asks CSF and Imunify360 what they hold for the target. Exposed per DECISIONS section 6.5 — the one operator-facing preflight. |
| `whm_firewall_release_and_allow` | **CHANGE** | `server_ref` + `target` (IPv4 only) + `duration_minutes` (1–525600, required, no default). Steps 2 and 3 of the firewall flow in one approval (DECISIONS section 6.5). Opens an approval request and changes nothing. No reason parameter, ever. |
| `whm_firewall_allowlist_remove` | **CHANGE** | `server_ref` + `target` (IPv4 only). The undo path, kept a separate tool and a separate approval (DECISIONS section 6.5). Answers `no_op` when the address is on no allow list. Opens an approval request and changes nothing. No reason parameter, ever. |

Both the RBAC gate (`noa_api/mcp_rbac.py`, execution-time re-check) and error sanitization
(`noa_api/mcp_tools/results.py`) sit in front of every tool, so nothing below is per-tool code.

### `whm_search_accounts`

WHM has no server-side account search, so `listaccts` is fetched whole and the match runs
in-process (`fetch_whm_accounts`, internal — one workflow, one tool; `whm_list_accounts` shares it).
Each row is reduced to the fields NOA speaks about by `core/integrations/whm/accounts.py`:
`user`, `domain`, `email`, `contactemail`, `owner`, `suspended`, `suspendreason`, `suspendtime`,
`is_locked`. Everything else WHM sends (`ip`, `plan`, disk counters, theme) is dropped — the
result lands in a LibreChat transcript that persists in their MongoDB. A row with no
`user` is dropped entirely: it is not an account any CHANGE tool could then be called on.

**One of those fields is withheld from this tool's rows: `suspendreason`.** NOA
writes the operator's approval reason into WHM's suspension note when it suspends an account,
and WHM returns it on every later `listaccts` — so a search that reported the field would hand
the model, by round trip, the one string the reason rule says it must never see. The normaliser still carries
it and `whm_list_accounts`' parked table still renders it: that page sits behind the operator's
own session (requester-match), which is where the reason may be read. The list lives at
`ACCOUNT_FIELDS_WITHHELD_FROM_MODEL` in `noa_api/mcp_tools/whm_read.py`.

`suspended` and `is_locked` arrive as `0`/`1` or `"0"`/`"1"` depending on the cPanel version and
are normalised to booleans — `"0"` is truthy in Python, so a raw read reports a live account as
suspended. `is_locked` falls back to the older `suspendlock`, including when `is_locked` is
present and JSON-null — the field `whm_unsuspend_account`'s preflight reads to refuse a lift WHM
would reject (the unsuspend tool's bound).

Three deliberate departures from `noa-old`'s version of this tool:

- **Per-field matching.** There the username and domain were joined into one haystack, so a
  query containing a space matched *across* the junction (`"acme sho"` matched user `acme` plus
  domain `shop.example.com`) and returned a row matching nothing the operator typed.
- **Truncation is stated.** The result carries `total_matches` and `truncated`; returning the
  first N silently lets the model report "there are twenty accounts" when there are two hundred.
- **Sorted by username before the cut**, because `listaccts` order is WHM's own and not
  documented as stable, which would make a truncated answer an arbitrary subset.

The last two are the **capped-READ bound**, generalised out of this tool: any READ that caps rows carries its
own bound and orders them reproducibly first. That is not summary-plus-URL — that one offloads a large
listing to the table surface, which is built (rows parked in `tool_result_tables`, read
back at `/tables/{token}` behind the operator's own session), while this one drops rows
in-process because the operator asked for a limit. `whm_list_accounts` is the tool that
uses it.

The two rules meet at the cap the table surface does apply: a parked table holds at most
`RESULT_TABLE_MAX_ROWS` rows and stores the count before that cut beside them, so a capped page
says so rather than reading as a complete one.

### `whm_list_accounts`

The other half of the account pair, split by **size** rather than by subject. A search
answers in the transcript; a whole listing does not — a dense server carries thousands of
accounts — so this tool fetches the same `listaccts` through the same internal
(`fetch_whm_accounts`), sorts the rows by username, parks them in `tool_result_tables` and
answers with three things: a one-line summary naming the server that was **actually read**
(the resolved row's name, not the `server_ref` the operator typed), the address of
`/tables/{token}` as plain copyable text, and the iframe resource LibreChat renders.

No rows, no sample row and no column value reach the model — that is the whole of summary-plus-URL, and a
"preview" would be exactly the ops data it keeps out of LibreChat's MongoDB.

**No `limit` argument.** The capped-READ bound exists because an operator asked for one; nothing is
dropped on their behalf here. The only bound is the table's own
(`RESULT_TABLE_MAX_ROWS`), applied at the write and reported in the text and in the result
envelope. The rows are still sorted before they are handed over: the cap is a prefix and never
a re-sort, and `listaccts` order is WHM's own.

**A failure is the ordinary `{"ok": false, …}` envelope**, `choices` and all, even though the
success is content blocks. A table that cannot be *parked* refuses the READ with
`result_table_unavailable` rather than handing back an address with nothing behind it — a dead
link in a transcript that persists is discovered later, by an operator, and a refusal is not.

The result also carries a counts-only structured envelope (`ok`, `total_rows`, `stored_rows`,
`truncated`). It exists for the audit trail: every READ is recorded and the run's
status is read off that envelope, so a content-only answer would file every successful listing
as a failure. It carries no rows, no token and no URL.

`limit` is bounded twice: on the tool's JSON schema (`ge`/`le`, which is what refuses a bad call
over MCP) and inside the tool (`limit_invalid`, which is what holds for an in-process call). A
blank or whitespace-only `query` is `query_required` — the field-rules gate, and one a schema cannot
express since `min_length` counts whitespace. Both guards run before any I/O.

### `whm_suspend_account`

**The first CHANGE tool**, and the one place a NOA-held reason leaves the process. A
`tools/call` here changes nothing: it resolves the server and reads the account through the same
internal the two READs use (`fetch_whm_accounts`, one workflow, one tool), writes an `action_requests` row with
that read as its evidence (context persisted at gate time), and answers with the approval card's address plus the
iframe (plain text beside the frame). WHM's `suspendacct` is not called at all — the count of requests to that
endpoint at gate time is zero, and that is what the tests assert rather than the shape of the
payload.

There is **no `reason` parameter** and nowhere to add one — one operator-typed field, entered at decision time. The gate refuses a
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
  (context persisted at gate time).
- **The suspension note is the operator's reason.** `load_authorized` reads
  `action_requests.reason` — non-blank by then, because the table's
  `ck_action_requests_decided_reason` refuses a decided row without one — and the runner sends it
  as `suspendacct`'s `reason`. The runner does **not** echo it back in its payload:
  `tool_runs.result_summary` is derived from that payload and `noa_get_action_result` returns the
  summary to a model, so an echo would reach the LLM through the audit row. Not
  through the receipt — that reader takes two scalars off `action_receipts`, the delta's
  `verification` and `verification_cause` lifted out of the JSONB in SQL, so no receipt row
  (and therefore no `before` half holding the reason) enters that process.
- **The change is re-read, and the read has three answers.** Suspended → done and verified. Still
  live → `postflight_failed`, because WHM accepted a call that did not take. The confirming read
  itself failing → `{"ok": true, "verified": false, "verification": "unavailable"}`, which is
  the verdict rule one system over: verification-unavailable is not verification, and it is not a
  failure either — reporting one would send an operator to re-suspend an account that may already
  be suspended.

A server that has been deleted between approval and execution is `whm_server_unavailable`, before
the mutation rather than after it.

### `whm_unsuspend_account`

`whm_suspend_account`'s mirror, and everything between the two names is one implementation
(one helper, not two): the same preflight (`collect_account_state` over `fetch_whm_accounts`), the same gate,
the same resolution of the approved change onto a client, and one postflight that differs only in
the value `suspended` must hold once the change took. **Not merged into one tool with an `action`
enum** — DECISIONS section 9 keeps the pair as two names because the two directions carry opposite risk,
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
runner never reads `request.reason` and no return path to a model opens on this side. The test
asserts the outgoing query *exactly* rather than the absence of one key name, so a note parameter
added later under any spelling goes red.

What does bite here is the read: an account being unsuspended is suspended right now, so
its `listaccts` row carries `suspendreason` — the operator's own words from the suspension. That
summary goes onto the approval row as evidence, where the card and requester-match are
its only readers (a model cannot reach it — `ActionResultView` has no field for evidence).
The two answers this tool puts in a *transcript* — the no-op and the locked refusal — are
built from the username and the server name rather than from the summary, and both are asserted
for the note.

The postflight has the same three answers as the suspension's, with the direction reversed: no
longer suspended → done and verified; still suspended → `postflight_failed`; the confirming read
itself failing → `verification: "unavailable"`.

### `whm_preflight_firewall_entries`

Step 1 of the firewall flow (DECISIONS section 6.5): *check whether an address is denied → release it →
allowlist it*. It is exposed rather than internal because the decision it feeds is the
operator's — they read the verdict and decide whether to call the release tool at all — so the
"a preflight runs inside its workflow" rule (one workflow, one tool) does not reach it. It is the only
preflight in that position.

Order of operations: refuse a blank `target` (`target_required`) and an unclassifiable one
(`invalid_target`), both before any I/O; resolve the `server_ref` and the row's SSH config in
one database session and close it; probe both backends; query only the usable ones, in parallel.

The result:

| Field | Meaning |
|---|---|
| `combined_verdict` | `blocked` \| `allowlisted` \| `not_found` \| `unknown` (zero answers → `unknown`) |
| `unanswered_backends` | Usable backends that produced no verdict — failed, or CSF's `unknown` |
| `available_backends` | `{"csf": …, "imunify": …}` — what could be run at all |
| `sudo_required` | A backend was present but its `sudo -n` was denied (GH #82) |
| `target_kind` | `parse_csf_target`'s classification, so the model need not classify an address |
| `matches` / `total_matches` / `truncated` | The evidence lines, and their bound |
| `csf` / `imunify` | Each backend's own verdict, or the code its failure carries |

Three deliberate departures from `noa-old`'s version:

- **No raw output.** It returned csf's whole `-g` dump and Imunify's whole JSON document.
  DECISIONS section 6.5 says the before-state shows the `csf.deny` / `csf.allow` log line and
  never a raw iptables table, and the result persists in LibreChat's MongoDB.
- **A verdict read from a subset says so.** It computed the combined verdict from
  whichever backend succeeded and otherwise fell through to `not_found` — so a broken CSF plus a
  clean Imunify reported "this address is not blocked" on a box whose *blocking* backend was
  silent, a fabrication the tool authored. Here the backends that did not answer are named, and
  a call where nothing answered is `unknown`, never `not_found`. The zero-backend error bounds only the zero
  case; this is the partial one.
- **Evidence is not repeated per backend.** The lines already say which system produced them.

A fourth departure landed with release-and-allow and is about what comes *back*: a comment NOA wrote is cut
out of `matches` before a model reads them. See "The operator's reason, out and back" below.

### `whm_firewall_release_and_allow`

Steps 2 and 3 of the flow, merged into one approval because they are almost always run together
(DECISIONS section 6.5). The tool opens an `action_requests` row and changes nothing; the runner on the
far side of the cookie/CSRF boundary does the work after an operator decides.

The runner sends, per usable backend and through `run_on_usable_backends`:

| Backend | Commands, in order | Strict? |
|---|---|---|
| CSF | `-tr <ip>`, `-dr <ip>`, `-ta <ip> <ttl-seconds> <comment>` | The first two tolerate a non-zero exit; `-ta` does not |
| Imunify | `ip-list local delete --purpose drop <ip>`, `ip-list local add --purpose white <ip> --comment <c> --expiration <epoch>` | The delete is tolerated; the add is not |

**Release before allow**, because CSF resolves a conflict block-first: an allow written before
the deny entry is removed buys nothing and reads as success. The removals are tolerated because
"not in that list" is the ordinary case — most addresses are held by one backend, or by a
temporary ban. A **sudo-rights** refusal is never tolerated: sudoers can permit `csf -v`
and refuse the write, and that is not an entry being absent.

One instant feeds both backends — csf takes a TTL in seconds, Imunify an absolute epoch — and
the after-state reports the resolved `expires_at`.

**Two outcomes, never collapsed** (DECISIONS section 6.5). `released` and `allowlisted` are separate
booleans with separate codes (`firewall_release_failed` / `firewall_allow_failed`), so a receipt
cannot say "done" over a half-finished change. There is **no no-op**: the allow entry carries a
TTL, so a repeat always moves the expiry.

### `whm_firewall_allowlist_remove`

The undo path, its own tool and its own approval (DECISIONS section 6.5). `server_ref` + `target`, and
no duration — there is no window to state.

| Backend | Commands, in order | Strict? |
|---|---|---|
| CSF | `-tra <ip>`, `-ar <ip>` | Neither; both tolerate a non-zero exit |
| Imunify | `ip-list local delete --purpose white <ip>` | No |

Both csf commands are sent because an entry lives on the temporary allow list *or* the permanent
one and NOA does not know which: release-and-allow writes temporary entries, an operator's own hand-added
ones are permanent. Nothing here is required to succeed — release-and-allow could keep the `-ta` strict
because that entry is the one the operator asked to *exist*, and a removal has no equivalent — so
**the postflight is the only authority**. The sudo-rights exception above applies here too.

**There is a no-op, and it is gated on a full answer.** An address on no allow list is already in
the state the change would produce, so the tool answers instead of opening a card (the suspend and
unsuspend tools' rule). But "there is nothing to remove" is a claim about absence, and a backend that stayed
silent has not made it — so one unanswered backend opens the card instead.

**"Is it still allowlisted?" is not the combined verdict.** Both backends resolve a conflict
block-first, so an address on a deny list *and* an allow list reports `blocked` and the allow
entry disappears from the verdict. Read that way, a removal that failed on a denied address would
report as done, and the no-op would refuse to open a card for an address that plainly has an
entry to delete. So both questions are asked of `allow_entry`, which `parse_csf_grep_output` and
`parse_imunify_ip_list_response` carry beside the verdict.

The runner answers `removed` — one boolean, because a removal is one claim — and omits it
entirely when a usable backend did not answer the confirming read, reporting `status: changed`,
`verified: false`, `verification: unavailable` and naming the silent backend.
An absent field beats a `false` nobody measured.

### The operator's reason, out and back

The single reason field is typed by an operator on the approval card. The one-field rule lets it *leave* NOA
where the target system has an honest place for it; it must never come back to a
model. On the firewall path that plays out in three places:

- **release-and-allow writes it**, behind a marker NOA authors: `noa:<action_request_id> <reason>`, on both
  the csf allow entry's comment and Imunify's `--comment`.
- **`whm_preflight_firewall_entries` cuts it back out** — `without_noa_comment_text`, from the
  marker to the **end of the line**, because csf gives a comment no closing boundary and a reason
  containing a bracket or a quote would walk through any rule that tried to find its end. The
  stated cost: a token csf places after the comment goes with it, which is why
  `format_imunify_matches` renders `[expires: …]` *before* the comment. The marker survives — it
  is the address of the approval row where the reason is readable behind the operator's cookie.
- **allowlist-remove writes nothing** — a removal takes no comment — but it deletes an entry that carries
  one, so the same bound applies on the way back: a backend failure message is cut
  (`backend_change_failure`), the postflight's `csf -g` lines are read for a verdict and never put
  in the payload, and the **no-op answer** is built from the server name, the address and one
  measured boolean rather than from the lines it was decided from.

The cut is at the surfaces that answer a *model*, never in the parsers. The approval card and the
receipt read the same lines and are the operator's own (requester-match), so a cut in
`parse_csf_grep_output` would take the reason off the two places that exist to show it.

## Error codes

| Code | Raised by | Meaning |
|---|---|---|
| `host_required` | `resolve_whm_server_ref` | `server_ref` blank or whitespace. |
| `host_not_found` | `resolve_whm_server_ref` | No server matches the id, name or hostname. |
| `host_ambiguous` | `resolve_whm_server_ref` | Several match; `choices` carries the candidates. |
| `query_required` | `whm_search_accounts` | Blank or whitespace-only search text. |
| `limit_invalid` | `whm_search_accounts` | `limit` outside 1–100. |
| `target_required` | Every firewall tool | Blank or whitespace-only target. |
| `invalid_target` | `whm_preflight_firewall_entries` | Not an IP, network or hostname. |
| `invalid_target` | Both firewall CHANGE tools | Not a single IPv4 address — they write rules. |
| `duration_invalid` | `whm_firewall_release_and_allow` | `duration_minutes` outside 1–525600, or not a whole number. |
| `no_firewall_backend` | `firewall_gate.require_usable_backends` | Neither backend could be run, on any firewall tool. |
| `invalid_response` | `whm_preflight_firewall_entries` | `csf -g` exited 0 with nothing to read. |
| `firewall_release_failed` | `whm_firewall_release_and_allow` runner | Still blocked after the release ran. |
| `firewall_allow_failed` | `whm_firewall_release_and_allow` runner | Released, but not on an allow list. |
| `firewall_allowlist_remove_failed` | `whm_firewall_allowlist_remove` runner | An allow entry is still there after the removal ran. |
| `change_evidence_unusable` | Both firewall runners | The approved row's evidence no longer carries a runnable target or duration. |
| `whm_server_unavailable` | Every CHANGE runner | The server row the change was approved against is gone. |
| `whm_wrong_credential_for_owner` | `whm_suspend_account` / `whm_unsuspend_account` preflight, re-checked by the runner | The resolved row's `api_username` is not the account's `owner`; the message names the `server_ref` that would work. |
| `tool_not_permitted` | `RbacToolMiddleware` | Caller lacks the grant, or the name is not a registered tool. |
| `tool_execution_failed` | `sanitize_tool_errors` | Unmapped exception out of a tool. |
| `timeout` | `sanitize_tool_errors` | `TimeoutError` out of a tool. |
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
once to **502** in `noa_api/api/errors.py` (one shared error handler).

## Credentials at rest

The API token, SSH password, SSH private key and its passphrase are Fernet-encrypted with the
`enc:v1:fernet:` prefix under `NOA_SECRET_ENCRYPTION_KEY` (server credentials encrypted at rest, Fernet required in production). Decryption
happens in exactly two places, both taking an injected `SecretCipher`:

- `build_whm_client_from_creds` — the API token.
- `resolve_whm_ssh_config` — the SSH credentials.

Both use `maybe_decrypt_text`, so a row written before encryption still works — that is what
lets the column be migrated in place.

## Admin surface

Five routes under `/admin/whm/servers`, all behind `require_admin` (non-admin refused):
`GET` / `POST` on the collection, `PATCH` / `DELETE` on `{id}`, and `POST {id}/validate`. No
response carries `api_token` or an SSH credential — the safe view reports `has_api_token`,
`has_ssh_password` and `has_ssh_private_key` instead, and the service encrypts on
the way in so the stored columns are `enc:v1:fernet:…`.

**`POST …/validate` checks the API token first, then SSH if the row carries credentials.** The
API check is `myprivs`, not a ping — it reports the granted ACL set and refuses a row that cannot
suspend an account (`whm_token_acl_insufficient` when `suspend-acct` is absent; a missing
`list-accts` is only named, not refused — see "`myprivs` and the ACL set" above).
A row with no SSH credentials validates green on the API alone: SSH is what the firewall tools
need, and a WHM server used only for account reads is a legitimate configuration. Unreachability
is a **200 with `ok: false`** and the raised code (`ssh_timeout`, `ssh_auth_failed`,
`ssh_host_key_mismatch`, …) — the operator asked whether the server answers, and "no" is that
question's answer.

**The host key is pinned once, and a mismatch refuses.** A row with no stored fingerprint gets
one captured out of the handshake (`ssh_get_host_fingerprint`) and stored **only if the probe
that follows it passes** — so a failed validate leaves no pin behind. A row whose stored pin no
longer matches answers `ssh_host_key_mismatch` and is **not** re-pinned: `noa-old`'s WHM service
overwrote the pin on every validate, which made the pin worth nothing, and upstream provenance
is no evidence — a ported security control ships with a test against the real mechanism
(`apps/api/tests/test_server_host_key_validation.py`, a live `asyncssh` server).

A legitimate key rotation is therefore two operator actions: `PATCH` with
`clear_ssh_host_key_fingerprint: true` (or edit `base_url` / `ssh_port`, which invalidates the
pin because a pin belongs to one `(host, port)` pair), then Validate.

Audit is no longer on the not-built list: a `tool_runs` row is written for every MCP READ by
`ToolRunAuditMiddleware`, beside the RBAC gate (one seam, never per-tool), so each WHM READ tool below
records requester, redacted arguments, a truncated result summary and timing — including
when it fails.

**Reseller-owned accounts are handled by ownership, not by a per-reseller token table.** cPanel
API tokens enforce reseller *ownership*: a root-created token cannot mutate an account owned by
another reseller. That premise is confirmed, on a live host rather than assumed from the API
chart (measured on a live host) — but the mechanism is **not** an ACL gap. The root credential measured there had
`suspend-acct` granted and `list-accts` granted, with `all` (root-level privilege) absent, so a
root token that is itself ACL-restricted cannot satisfy WHM's root check and the request falls
through to comparing the account's `owner` against the token's `api_username`; `root` is not
that account's reseller, so the call is refused regardless of what the ACL permits.

`noa-old` solved this with a `whm_server_tokens` table and an owner → token resolver. NOA does
not carry that table and does not add one — the design instead is ordinary `whm_servers` rows.
An admin marks a row `is_reseller_credential = true` when its `api_username` names a reseller
rather than root — additive, no backfill, every existing row keeps working at zero
config change. The flag is **visibility and naming, never authorization**: it hides the row from
`whm_list_servers`'s output (16 clusters × ~7 rows is the context problem behind that filter)
and it requires the row's `name` to equal its `api_username` at the admin write
— which is what lets an operator name `server_ref` as the account's `owner` and have
`resolve_whm_server_ref`'s name match land on that same row. The actual gate is owner-as-`server_ref`: the
account CHANGE preflight compares `account["owner"]` against the resolved row's `api_username`
(both normalized) **before** `action_requests` is written, and refuses
`whm_wrong_credential_for_owner` naming the `server_ref` that would have worked — a card that
could only fail would cost an operator a decision and a reason typed for nothing. The
runner re-checks the same equality off the approval's own evidence, since a row is editable
between a request and its decision (context persisted at gate time).

A reseller credential's read scope is its own accounts only — measured 77 of 77 on the live host
— so preflight, mutation and postflight for an account CHANGE all run on the **same**
credential rather than escalating to root "to see everything": the identity that
confirms a change is the identity that made it. The admin surface removed the ported panel's dead
reseller-token surface (the drawer section, its dialog and its five API calls) for the reason
the direct-grant controls went 410: a button aimed at a route that does not exist reads as
a broken deployment. That removal still stands — the replacement is the ownership guard above,
not a revived token sub-resource.

**Never implement** (management policy — not a technical limit): `whm_change_contact_email`,
`whm_change_primary_domain`, `whm_check_binary_exists`, `whm_firewall_denylist_add_ttl`. The
matching `WHMClient` methods are deliberately absent, so the capability is not one line from
exposure. Re-adding any of them is an owner decision.

## Code references

- Package overview: `core/integrations/whm/__init__.py`
- WHM API client: `core/integrations/whm/client.py`
- Account row shaping + search predicate: `core/integrations/whm/accounts.py`
- Row → SSH config, row → API client: `core/integrations/whm/ssh.py`
- CSF: `core/integrations/whm/csf.py` (parsing), `csf_cli.py` (execution)
- Imunify: `core/integrations/whm/imunify.py` (parsing), `imunify_cli.py` (execution)
- Backend availability: `core/integrations/whm/availability.py`
- Errors: `core/integrations/whm/errors.py`
- Shared SSH layer: `core/remote_exec/`
- Server inventory + reference resolution: `core/servers/whm_repository.py`,
  `core/servers/whm_ref.py`
- Admin CRUD + validate: `apps/api/src/noa_api/api/routes/admin_servers.py`,
  `core/servers/admin_service.py`, `core/servers/admin_repository.py`,
  `core/servers/validation.py`, `core/servers/naming.py`, `core/servers/errors.py`
- Exposed READ tools: `apps/api/src/noa_api/mcp_tools/whm_read.py`,
  `whm_firewall.py`
- Account CHANGE tools + runners: `apps/api/src/noa_api/mcp_tools/whm_account_change.py`
- Firewall CHANGE tools + runners: `apps/api/src/noa_api/mcp_tools/whm_firewall_change.py`,
  `whm_firewall_allowlist.py`, with what the two share in
  `whm_firewall_change_common.py`
- Shared across every post-approval runner: `apps/api/src/noa_api/mcp_tools/change_target.py`
- MCP tool + registry: `apps/api/src/noa_api/mcp_tools/{whm_read,registry,results,context}.py`
- RBAC gate in front of every tool: `apps/api/src/noa_api/mcp_rbac.py`
- Tests: `apps/api/tests/test_whm_{client,ssh_config,csf_parsing,imunify_parsing}.py`,
  `test_whm_firewall_{cli,availability,gate}.py`,
  `test_whm_tools_firewall_{preflight,release_and_allow,allowlist_remove}.py`,
  `test_whm_firewall_{release,allowlist}_runner.py`,
  `test_whm_{server_ref,server_repository,tools_read}.py`, `test_mcp_tool_rbac.py`
