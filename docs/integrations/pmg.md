# PMG Integration Reference

Canonical reference for how NOA talks to Proxmox Mail Gateway nodes. Ported from `noa-old` branch
`MCP` with the integration layer itself, scoped to what exists here today.

Update this file whenever a PMG-backed feature, upstream command, or SSH execution path changes —
in the same commit as the code.

## One transport, and it is SSH

PMG's API is not exposed to NOA, so every operation runs `pmgsh` on the box:

| Surface | Transport | Module |
|---|---|---|
| Whitelist (`mynetworks`) read/add/remove | SSH + `pmgsh`, host-key pinned | `core/integrations/pmg/pmgsh_cli.py` |
| Apply a config change | SSH + `pmgconfig sync --restart 1` | same |
| Reading what `pmgsh ls` printed | — (local parsing) | `core/integrations/pmg/mynetworks.py` |

That is the opposite of WHM (two transports) and of Proxmox (HTTP only), and it is why a
`pmg_servers` row carries **only** SSH credentials — no `base_url`, no API token, no `verify_ssl`.
`noa-old` carried `base_url`/`verify_ssl` columns for PMG and never used them; `core/db/models.py`
dropped both in the initial schema.

The whole path inherits every guard in `core/remote_exec/`: banner stripping,
`sudo -n` escalation, host-key pinning with TOFU refresh (the pin was inert as
ported — upstream's `known_hosts=None` is asyncssh's off switch — fixed here before any doc
called it hardened).

## Connection base

- The configured server should be the PMG cluster **master/admin** node.
- `ssh_host` is a **bare host or IP**, not a URL. Unlike WHM there is no `base_url` to parse a
  hostname out of, so the value is validated here: a scheme (`://`), interior whitespace, any of
  `/?#@`, or bracketed IPv6 notation is refused as `ssh_invalid_host`. Surrounding whitespace is
  trimmed — a pasted trailing newline is an admin artifact, not an unusable row.
- SSH port defaults to `22`. A stored `0` or negative value is refused as `ssh_invalid_port`
  rather than falling through the default, so a typo cannot become a silent connection to 22.
- `ssh_username` NULL or blank means connect as `root`; anything else gets `sudo -n`.
- The host-key fingerprint must already be pinned on the server row before any tool command
  runs. Only the admin validate flow connects unpinned, and only **when the row carries
  no pin yet** — a stored fingerprint that no longer matches answers `ssh_host_key_mismatch`
  rather than being refreshed, so an operator has to clear it deliberately before a new key can
  be trusted.
- **The caller resolves the row into an `SSHConnectionConfig` and the command layer takes that.**
  `run_pmgsh_command` and everything beside it take a resolved config, not a
  `pmg_servers` row plus a cipher, so a tool resolves once, closes its database session, and
  only then reaches the node — a pooled Postgres connection held across an SSH hop to someone
  else's host is how a slow PMG box becomes a database outage. The consequence for an operator:
  the four row refusals below are reported against the *server*, not as a failed `pmgsh`
  command.
- `pmgsh` path is `/usr/bin/pmgsh`, absolute — under `sudo -n` the `PATH` is sudoers'
  `secure_path`. `pmgconfig` is unqualified, as in `noa-old`; `secure_path` carries `/usr/bin` on
  a default PMG node.

## Command discipline

**Argv-only. There is no shell string.** Every command is composed from a token list through
`core.remote_exec.ssh.command_from_argv`, which `shlex.quote`s each token, so a CIDR that reached
NOA as an LLM tool argument stays one argument to `pmgsh` instead of becoming shell syntax.

Not used, deliberately: remote `jq`, remote `awk`, `pmgdb`, direct PMG database access, pipes,
redirection. **NOA parses stdout locally.**

`TERM=dumb` is set on every command and sits *ahead* of `sudo`, not inside the escalated command:
it is the environment `pmgsh` sees either way, and without it `pmgsh` emits terminal control
sequences that end up in front of the JSON document a parser then has to recover from.

## Upstream commands used by code

As `root`:

```bash
# Version probe — cheapest authenticated call; the credential probe for admin validate.
TERM=dumb /usr/bin/pmgsh get /version

# Read the whitelist (also the validate-route mynetworks probe).
TERM=dumb /usr/bin/pmgsh ls /config/mynetworks

# Add one CIDR, then apply.
TERM=dumb /usr/bin/pmgsh create /config/mynetworks -cidr <cidr>
TERM=dumb pmgconfig sync --restart 1

# Remove one exact CIDR, then apply.
TERM=dumb /usr/bin/pmgsh delete /config/mynetworks/<cidr>
TERM=dumb pmgconfig sync --restart 1
```

As a non-root SSH user, every one of those gains `sudo -n` after `TERM=dumb`:

```bash
TERM=dumb sudo -n /usr/bin/pmgsh get /version
```

Two details that are easy to break:

- `-cidr <value>` is **two argv tokens**, so a hostile value cannot become a second flag.
- `/config/mynetworks/<cidr>` is **one argv token** even though the CIDR contains `/`. Split into
  two, `pmgsh delete` receives a path it does not recognise.

`pmgconfig sync --restart 1` is required after every mutation: `pmgsh` writes PMG's config, and
Postfix does not pick the change up until the sync runs. A mutation that skips it looks applied
and is not.

`/config/mynetworks` is the **only** PMG endpoint this layer touches. Rule-based / global
mail-filter whitelist paths under `/config/ruledb/who/<id>/*` and the user quarantine whitelist
are not used for sender-IP allowlisting.

### Required sudoers entries

For a non-root SSH user (replace `noa-ops`):

```
noa-ops ALL=(root) NOPASSWD: /usr/bin/pmgsh, /usr/bin/pmgconfig
Defaults:noa-ops !requiretty
```

Without `NOPASSWD`, `sudo -n` fails immediately and NOA reports `ssh_sudo_required` — the
intended outcome, not a bug. The `-n` is load-bearing: without it a node missing the entry sits
on a password prompt until the command deadline, and the tool reports a timeout for what is
really a configuration problem.

## Reading the answer

PMG answers reads and writes differently, so there are two success predicates:

| Predicate | Rule | Used by |
|---|---|---|
| `require_pmgsh_success` | non-zero exit = failure | reads, version probe, `pmgconfig sync` |
| `require_pmg_mutation_success` | non-zero exit tolerated **iff `200 OK` is on stdout** | `create`, `delete` |

`pmgsh create`/`delete` print the HTTP status of the underlying API call and do not always
reflect success in their exit code. The mutation check reads `stdout` alone, never the combined
stdout+stderr text: a `200 OK` that arrived on *stderr* is a different shape and stays a failure.
Accepting it would turn an approved CHANGE that failed into a receipt saying it worked.

`parse_pmgsh_json_output` scans for the first `[` or `{` rather than parsing the whole string,
because `pmgsh` interleaves status lines with its payload and may trail more after it. That scan
also recovers a document with a CloudLinux-style banner in front of it — the real fix strips
banners at the SSH boundary (`noa-old` GH #83); this is the belt-and-braces guard for a
variant the signature gate does not recognise.

Combined-stream reading (`core/remote_exec/output.py`) is shared with WHM: `pmgsh` splits its
status line and its errors across stdout and stderr, so a caller that reads one gets an empty
error message about half the time.

### `mynetworks` entries

`pmgsh ls /config/mynetworks` prints `<id> <cidr>` rows under an `id cidr` header, with the
`200 OK` status line somewhere in it. `core/integrations/pmg/mynetworks.py` reads the second
column of each line and falls back to the first, taking the first token that parses as an
address or a network. Nothing else is needed to skip the framing: `200`, `OK`, `id` and `cidr`
all fail to parse, so no phantom entry is manufactured from a status line.

**A single host and its host route are one entry.** `1.2.3.4` ≡ `1.2.3.4/32`, `2001:db8::1` ≡
`2001:db8::1/128` — so both the operator's target and every stored line go through
`normalize_cidr` before anything is compared. `ipaddress.ip_network(…, strict=False)` also masks
host bits, so `1.2.3.4/24` normalises to `1.2.3.0/24`; a tool therefore reports the normalised
form beside its verdict rather than echoing what it was handed.

Membership is **exact, never containment**: `1.2.3.0/24` in `mynetworks` is not a match for
`1.2.3.4`. Answering otherwise would tell an operator their address is whitelisted when the
entry a removal has to name is a different CIDR.

Two departures from `noa-old`, both deliberate: a line's first parsable candidate ends that line
(there, a candidate already seen fell through to the id column), and repeated CIDRs are kept
rather than deduplicated while parsing (two spellings of one address in `mynetworks` are a fact
about the whitelist).

## Error codes

| Code | Raised by | Meaning |
|---|---|---|
| `ssh_invalid_host` | `resolve_pmg_ssh_config` | `ssh_host` is not a bare host. Bad row. |
| `ssh_invalid_port` | `resolve_pmg_ssh_config` | `ssh_port` ≤ 0. Bad row, never silently 22. |
| `ssh_not_configured` | `resolve_pmg_ssh_config` | No SSH password and no private key stored. |
| `ssh_host_key_not_validated` | `resolve_pmg_ssh_config` | Not pinned yet — run admin validate. |
| `ssh_host_key_mismatch` | `ssh_exec` | Presented key ≠ the pin. Investigate; no auto-refresh. |
| `ssh_sudo_required` | `require_pmgsh_success`, `require_pmg_mutation_success` | `sudo -n` denied. Fix sudoers, not the install. |
| `pmgsh_command_failed` | both predicates | `pmgsh`/`pmgconfig` exited non-zero for another reason. |
| `pmgsh_json_not_found` | `parse_pmgsh_json_output` | exit 0, but no JSON document in the output. |
| `pmgsh_json_invalid` | `parse_pmgsh_json_output` | a document started and failed to decode. |

Every SSH-side failure is converted, so one exception tree leaves this layer: `PMGSHCLIError`, a
`NoaError`, mapped once to **502** in `noa_api/api/errors.py`. A denied `sudo -n` keeps
its own code because the remedy differs — a missing binary (`sudo: …: command not found`) is
explicitly *not* classified as a rights failure, so an operator is not sent hunting an install
that is already there (`noa-old` GH #82).

## Credentials at rest

The SSH password, private key and its passphrase are Fernet-encrypted with the `enc:v1:fernet:`
prefix under `NOA_SECRET_ENCRYPTION_KEY`. Decryption happens in exactly one
place for PMG — `resolve_pmg_ssh_config`, taking an injected `SecretCipher` — via
`maybe_decrypt_text`, so a row written before encryption still works. That is what lets the
column be migrated in place.

## The exposed tools

Three: two READs and one CHANGE. The READs split by **size, not by subject** — a
membership question answers in the transcript, a whole whitelist does not. All three run the same
internal read — `read_pmg_mynetworks` in `noa_api/mcp_tools/pmg_read.py` — which resolves the
server inside one database session, closes it, then runs `pmgsh ls` and parses the output. Only
what they do with the entries differs.

### `pmg_whitelist_search`

`pmg_whitelist_search(server_ref, target)` (READ) answers whether one address is on one
node's `mynetworks` list. It is the discovery step in front of `pmg_whitelist(action)`:
the operator has an address and needs to know whether adding it is a change or a no-op, and
whether removing it has anything to remove.

One `pmgsh ls` per call, and only that — a search must not sync, create or delete. Two guards
run before any I/O: a blank `target`, and a `target` that is neither an address nor a
network (`invalid_whitelist_target`). A hostname is refused rather than resolved — `mynetworks`
holds CIDRs, and turning a name into an address here would answer about whatever DNS said at
that moment.

The result carries `exists`, the matching entries in PMG's own spelling, the `normalized_target`
membership was tested against, and `total_entries`. That last one is not decoration: `exists:
false` against zero parsed entries is a different fact from `exists: false` against three
hundred, and `pmgsh ls` output alone cannot tell an empty `mynetworks` from a format NOA no
longer recognises.

### `pmg_whitelist_list`

`pmg_whitelist_list(server_ref)` (READ) lists the whole whitelist — and the entries do
**not** come back in the conversation. They are parked in `tool_result_tables` and the result
carries a one-line summary, the number of entries, whether the page is capped, and the address
of `/tables/{token}` on NOA's own origin. It is the table surface's second producer
after `whm_list_accounts`, and it added nothing to it: a tool hands over rows, its own columns
and its own order, and the surface does the rest.

Two columns are rendered — PMG's own spelling and the normalised CIDR — because they differ
whenever `mynetworks` stores a bare host, and only the raw one says what is in the file.

Three things worth knowing:

- **Sorted by address before it is handed over.** The table's cap keeps a prefix and
  never re-sorts, and `pmgsh ls` prints in whatever order PMG stores, so without this a capped
  page would be an arbitrary subset that differs between two identical calls. Numerically, not
  as text: `10.9.0.0/24` belongs before `10.10.0.0/24`.
- **Duplicate lines are listed twice.** `mynetworks` really holds both, and a removal has to
  take each.
- **No `limit` argument.** The only bound is the table's own (`RESULT_TABLE_MAX_ROWS`), it
  is applied at the write, and it is stated both in the text the model reads and on the page
  the operator opens. A table that could not be written **refuses the READ**
  (`result_table_unavailable`) rather than answering with the address of a table that is not
  there.

### `pmg_whitelist`

`pmg_whitelist(server_ref, action, target)` (CHANGE) adds one address to `mynetworks` or
removes it. One tool with an `action` enum where `noa-old` had `pmg_whitelist_add` and
`pmg_whitelist_remove` (DECISIONS section 9); the recorded cost is that RBAC gets coarser — a role
cannot be granted one direction without the other.

It is in **two halves on either side of the approval boundary**:
`noa_api/mcp_tools/pmg_whitelist.py` runs the in-process preflight and opens an `action_requests`
row and can change nothing, and `noa_api/mcp_tools/pmg_whitelist_runner.py` performs the change
and is reachable only from `core/approvals/execution.py` after an operator approved. There is no
`reason` parameter and nowhere to add one.

**What membership means, stated once and not elaborated.** Every operator-facing sentence the
runner writes — the no-op, the verified change, the unconfirmed branch — reduces to one clause: an
address in `mynetworks` may relay email through the gateway, one that is not may not, and that is
all it means. No spam scoring, no ports, no delivery guarantee.

**The fact is one fact; the sentence is composed per branch, and nothing binds the six spellings to
each other.** Each branch states membership in the grammar its own moment needs — the verified one
says what is true *now* (`may now relay` / `may no longer relay`), the not-in-force one says what is
not true *yet* (`cannot relay email yet`), the no-op one says the list was already that way, and the
two write-failure branches splice a shared `relay` variable because they quote a reading rather than
assert a state. Only those last two share a built string. So a later author rewording membership has
to edit every branch in `pmg_whitelist_runner.py` that says it, and editing the `relay` variable
alone moves two of six. That is worth knowing before the edit rather than after: there is no test
holding the six in step, because what has to stay identical is the *meaning*, and a check on the
words would only pin whichever spellings happened to exist when it was written.

**Both spellings of the target travel everywhere.** `ipaddress` masks host bits, so
`203.0.113.10/24` is a request about `203.0.113.0/24` — and where the search tool only *answers*
about the masked form, this one **writes** it. The operator's own text and the normalised form are
on the card, in the no-op sentence and in the result.

**Two no-ops, and neither opens a card**: `add` against an address already on the list, and
`remove` against one that is not. There is nothing for an operator to authorise, and the answer is
built from the node's name, the two spellings and one measured boolean rather than from the
`pmgsh` output it was decided from.

**A read that cannot answer refuses.** PMG answers over one transport, so there is no partial case
to bound: a failed `pmgsh ls` keeps its own code (`ssh_sudo_required` vs
`pmgsh_command_failed`) and no card is opened.

Two deliberate departures from `noa-old` on the runner side:

- **A removal takes every matching line, each named by PMG's own spelling.** `mynetworks` can hold
  `1.2.3.4` and `1.2.3.4/32` at once — one entry to a reader, two lines in the file. `noa-old`
  deduplicated while parsing and sent one `delete` for the *normalised* form, which leaves the
  duplicate standing and aims `pmgsh delete /config/mynetworks/<cidr>` at a path segment PMG may
  never have printed. The runner deletes the token `pmgsh ls` emitted.
- **An add writes the normalised form**, because that is what membership was decided on and what
  the operator approved on the card.

**The runner re-reads before it decides.** PMG has no compare-and-set
token, so the approval window is checked against the *fact* the operator approved — is the address
on the list? — re-measured at run time. An address somebody else whitelisted while the card sat
pending is a `no_op`, not a failure.

**The postflight re-reads the list**: not the `200 OK` on stdout, which only says PMG
accepted a write. A read that cannot answer is `status: changed` with `verified: false` and
`verification: unavailable` — never a bare `false`.

**One bound worth stating.** That postflight verifies PMG's **config**. `pmgconfig
sync`'s own success is the only thing saying Postfix picked the change up, because NOA reads
`mynetworks` through `pmgsh` and has no view of Postfix's live table. So a write that landed while
the sync failed is reported as `pmg_sync_failed` rather than folded into the verdict: the entry is
in the config and mail flow has not moved, and neither `changed` nor a bare failure says that. For
the same reason a refused `delete` stops the removal and never syncs — applying a partial removal
is the whole one reported wrongly.

**The kept-from-the-LLM rule has no instance here.** A `mynetworks` entry is a CIDR — no comment,
no note, no
description — so nothing the LLM must never see is written onto a PMG node and nothing NOA wrote
can come back through a later READ. The runner never reads `request.reason`, and that is asserted
against a sentinel on the payload, the summary, the receipt and every composed command. It is also
why a backend failure message travels whole here while the allowlist-remove tool cuts its own:
csf quotes an entry NOA wrote a comment onto, and `pmgsh` has no comment to quote.

| Code | Meaning |
|---|---|
| `invalid_whitelist_target` | Not an IP address or CIDR network. A hostname is refused, not resolved. |
| `target_required` | Blank or whitespace-only target. |
| `invalid_action` | Not `add` or `remove`. The schema publishes the enum; this is the body's own re-check. |
| `pmg_server_unavailable` | The `pmg_servers` row named on the evidence is gone. Post-approval only. |
| `change_evidence_unusable` | The approved request did not survive its JSONB round trip in runnable form. |
| `pmg_sync_failed` | `pmgsh` wrote the entry and `pmgconfig sync` failed — config moved, Postfix did not. |
| `postflight_failed` | The command was accepted and a fresh read says the list did not move. |

## Admin surface

Five routes under `/admin/pmg/servers`, all behind `require_admin`: `GET` / `POST` on
the collection, `PATCH` / `DELETE` on `{id}`, and `POST {id}/validate`. No response carries an
SSH credential and the service encrypts on the way in. Unlike WHM's,
these rows have no `base_url` and no `verify_ssl`: `ssh_host` is required and the pinned host key
is the whole transport-security story.

`ssh_host_key_fingerprint` is writable here, on both create and update, because the panel's PMG
form exposes the field — an operator who already holds a node's key may pin it before NOA has
ever connected. Its *shape* is not validated: a wrong value fails loudly at the next connection
with `ssh_host_key_mismatch`, which names the remedy, whereas a format rule would refuse a
legitimate value the day `asyncssh` spells a digest differently.

**`POST …/validate` runs two commands**, `pmgsh get /version` then a read of
`/config/mynetworks` — a node that authenticates but cannot read `mynetworks` would otherwise
validate green and fail on the first whitelist call. Unreachability is a **200 with
`ok: false`** and the raised code. The host-key rule is WHM's, from the same function: capture
once, store only after the probe passes, and refuse rather than re-pin on a mismatch.

A row with no SSH credentials is still creatable — an admin filling in a host before the key
arrives is a legitimate order of operations, and `resolve_pmg_ssh_config` answers
`ssh_not_configured` in the meantime, which names the remedy.

`noa-old` also exposed `pmg_list_servers` and `pmg_validate_server`. Here those two become
internal functions — they are not on the 14-tool exposed list.

## Code references

- Package overview: `core/integrations/pmg/__init__.py`
- Row → SSH config: `core/integrations/pmg/ssh.py`
- Command build + execution: `core/integrations/pmg/pmgsh_cli.py`
- `mynetworks` parsing + normalisation: `core/integrations/pmg/mynetworks.py`
- Errors: `core/integrations/pmg/errors.py`
- Inventory + reference resolution: `core/servers/pmg_repository.py`,
  `core/servers/repository.py`, `core/servers/reference.py`
- Admin CRUD + validate: `apps/api/src/noa_api/api/routes/admin_servers.py`,
  `core/servers/admin_service.py`, `core/servers/admin_repository.py`,
  `core/servers/validation.py`
- MCP tools: `apps/api/src/noa_api/mcp_tools/pmg_read.py` (READ),
  `apps/api/src/noa_api/mcp_tools/pmg_whitelist.py` (CHANGE, the gate half),
  `apps/api/src/noa_api/mcp_tools/pmg_whitelist_runner.py` (CHANGE, the post-approval half)
- Large-result table surface: `apps/api/src/noa_api/mcp_tools/table_surface.py`,
  `core/results/tables.py`
- Shared SSH layer: `core/remote_exec/`
- Tests: `apps/api/tests/test_pmg_ssh_config.py`, `test_pmg_pmgsh_cli.py`,
  `test_pmg_mynetworks.py`, `test_pmg_server_ref.py`, `test_pmg_server_repository.py`,
  `test_pmg_tools_whitelist_search.py`, `test_pmg_tools_whitelist_list.py`,
  `test_pmg_tools_whitelist.py`, `test_pmg_whitelist_runner.py`
