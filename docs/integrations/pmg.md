# PMG Integration Reference

Canonical reference for how NOA talks to Proxmox Mail Gateway nodes. Ported from `noa-old` branch
`MCP` with the integration layer itself (§T.18, C13), scoped to what exists here today.

Update this file whenever a PMG-backed feature, upstream command, or SSH execution path changes —
in the same commit as the code.

## One transport, and it is SSH

PMG's API is not exposed to NOA, so every operation runs `pmgsh` on the box (§V.58, I.ext):

| Surface | Transport | Module |
|---|---|---|
| Whitelist (`mynetworks`) read/add/remove | SSH + `pmgsh`, host-key pinned | `core/integrations/pmg/pmgsh_cli.py` |
| Apply a config change | SSH + `pmgconfig sync --restart 1` | same |

That is the opposite of WHM (two transports) and of Proxmox (HTTP only), and it is why a
`pmg_servers` row carries **only** SSH credentials — no `base_url`, no API token, no `verify_ssl`.
`noa-old` carried `base_url`/`verify_ssl` columns for PMG and never used them; `core/db/models.py`
dropped both at §T.4.

The whole path inherits every guard in `core/remote_exec/` (§T.14): banner stripping (§V.56),
`sudo -n` escalation (§V.55), host-key pinning with TOFU refresh (§V.82 — the pin was inert as
ported, see §B.2; §V.69 no longer claims it).

## Connection base

- The configured server should be the PMG cluster **master/admin** node.
- `ssh_host` is a **bare host or IP**, not a URL. Unlike WHM there is no `base_url` to parse a
  hostname out of, so the value is validated here: a scheme (`://`), interior whitespace, any of
  `/?#@`, or bracketed IPv6 notation is refused as `ssh_invalid_host`. Surrounding whitespace is
  trimmed — a pasted trailing newline is an admin artifact, not an unusable row.
- SSH port defaults to `22`. A stored `0` or negative value is refused as `ssh_invalid_port`
  rather than falling through the default, so a typo cannot become a silent connection to 22.
- `ssh_username` NULL or blank means connect as `root`; anything else gets `sudo -n` (§V.55).
- The host-key fingerprint must already be pinned on the server row before any tool command
  runs. Only the admin validate flow (§T.54) connects unpinned, and only to capture the value it
  is about to store.
- `pmgsh` path is `/usr/bin/pmgsh`, absolute — under `sudo -n` the `PATH` is sudoers'
  `secure_path`. `pmgconfig` is unqualified, as in `noa-old`; `secure_path` carries `/usr/bin` on
  a default PMG node.

## Command discipline (§V.58)

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
banners at the SSH boundary (§V.56, `noa-old` GH #83); this is the belt-and-braces guard for a
variant the signature gate does not recognise.

Combined-stream reading (`core/remote_exec/output.py`) is shared with WHM: `pmgsh` splits its
status line and its errors across stdout and stderr, so a caller that reads one gets an empty
error message about half the time.

## Error codes

| Code | Raised by | Meaning |
|---|---|---|
| `ssh_invalid_host` | `resolve_pmg_ssh_config` | `ssh_host` is not a bare host. Bad row. |
| `ssh_invalid_port` | `resolve_pmg_ssh_config` | `ssh_port` ≤ 0. Bad row, ⊥ silently 22. |
| `ssh_not_configured` | `resolve_pmg_ssh_config` | No SSH password and no private key stored. |
| `ssh_host_key_not_validated` | `resolve_pmg_ssh_config` | Not pinned yet — run admin validate. |
| `ssh_host_key_mismatch` | `ssh_exec` | Presented key ≠ the pin. Investigate; no auto-refresh. |
| `ssh_sudo_required` | `require_pmgsh_success`, `require_pmg_mutation_success` | `sudo -n` denied. Fix sudoers, not the install. |
| `pmgsh_command_failed` | both predicates | `pmgsh`/`pmgconfig` exited non-zero for another reason. |
| `pmgsh_json_not_found` | `parse_pmgsh_json_output` | exit 0, but no JSON document in the output. |
| `pmgsh_json_invalid` | `parse_pmgsh_json_output` | a document started and failed to decode. |

Every SSH-side failure is converted, so one exception tree leaves this layer: `PMGSHCLIError`, a
`NoaError`, mapped once to **502** in `noa_api/api/errors.py` (§V.73). A denied `sudo -n` keeps
its own code because the remedy differs — a missing binary (`sudo: …: command not found`) is
explicitly *not* classified as a rights failure, so an operator is not sent hunting an install
that is already there (`noa-old` GH #82).

## Credentials at rest

The SSH password, private key and its passphrase are Fernet-encrypted with the `enc:v1:fernet:`
prefix under `NOA_SECRET_ENCRYPTION_KEY` (C7, §V.48, §V.52). Decryption happens in exactly one
place for PMG — `resolve_pmg_ssh_config`, taking an injected `SecretCipher` — via
`maybe_decrypt_text`, so a row written before encryption still works. That is what lets the
column be migrated in place.

## Not built yet

| Surface | Task |
|---|---|
| `resolve_pmg_server_ref` (UUID / name / host → candidates on ambiguity, §V.18) | §T.29–§T.31 |
| `mynetworks` line parsing + `ipaddress` normalisation (`1.2.3.4` ≡ `1.2.3.4/32`, §V.59–§V.61) | §T.29–§T.31 |
| MCP tools `pmg_whitelist(action)`, `pmg_whitelist_list`, `pmg_whitelist_search` | §T.29–§T.31 |
| Large-result table surface for `pmg_whitelist_list` (§V.64) | §T.30, gated on §T.59 |
| Admin routes `/admin/pmg/servers…` + `POST …/validate` | §T.54 |

`noa-old` exposed `pmg_whitelist_add` and `pmg_whitelist_remove` as two tools plus
`pmg_list_servers` and `pmg_validate_server`. Here the pair collapses into one
`pmg_whitelist(action: add|remove)` (DECISIONS §9), and the two server tools become internal
functions (I.mcp) — they are not on the 14-tool exposed list.

## Code references

- Package overview: `core/integrations/pmg/__init__.py`
- Row → SSH config: `core/integrations/pmg/ssh.py`
- Command build + execution: `core/integrations/pmg/pmgsh_cli.py`
- Errors: `core/integrations/pmg/errors.py`
- Shared SSH layer: `core/remote_exec/` (§T.14)
- Tests: `apps/api/tests/test_pmg_ssh_config.py`, `apps/api/tests/test_pmg_pmgsh_cli.py`
