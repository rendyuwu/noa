# noa — Decision Notes

Why-record, not a spec. Rules are enforced inline (`AGENTS.md` hard boundaries, module
docstrings, tests); this file holds the reasons behind them.

- **Section numbers are cited from code, tests and `docs/`. Never renumber a section.**
- Old-repo ids (`V138`, `T98`, `C17`) resolve only in `noa-old`: `git show MCP:SPEC.md`.
- Decisions are 2026-08-04 unless dated otherwise. Validity pass against the live tree: 2026-09-13.

Owner-owned, not agent tasks: NTP on the Kubernetes node and the database VM (section 17);
phasing (8.7); LibreChat Mongo retention (8.6, go-live gate, not a build blocker).

---

## 1. Why this repo exists

`noa-old` works and is deployed to staging. Two drivers for a rewrite rather than a refactor:

1. **Context bloat** — all 39 registered tools exposed to the model every turn.
2. **Chat maintenance** — maintaining a chat UI is not the product. Owner decided 2026-07-27:
   conversation moves to self-hosted LibreChat, NOA becomes an MCP server.

`noa-old` carries a chat stack, Assistant Transport and an agent loop that all die under that
decision. Rewriting the exposed surface is cheaper than unwinding it in place.

`noa-old` = source of truth for patterns, never a fork base.

---

## 2. Measurements (2026-08-04, measured not estimated)

Tool schema cost, from serializing `noa-old`'s live registry:

| Metric | Value |
|---|---|
| Registered tools | 39 |
| Tool schema JSON | 46,234 chars |
| Approx tokens per turn | **~11,558** |
| READ subtotal | 24,595 ch (~6,148 tok) |
| CHANGE subtotal | 21,561 ch (~5,390 tok) |

Heaviest: `proxmox_move_vms_between_pools` 1,995 ch, `proxmox_preflight_move_vms_between_pools`
1,925, `whm_mail_log_failed_auth_suspects` 1,820, `proxmox_enable_vm_nic` 1,632,
`proxmox_disable_vm_nic` 1,626, `whm_change_primary_domain` 1,615. One workflow plus its preflight
(pool move) burns ~4k chars.

`noa-old` LOC (non-test). Read the column matching the branch you copy from; section 8.5 ports
from `MCP`, and `MCP` agrees with `main`.

| Dies (LibreChat takes over) | `staging` | `MCP` |
|---|---|---|
| `core/agent/` | 2,281 | 2,854 |
| `api/assistant/` | 3,817 | 4,312 |
| `core/prompts/` | 220 | 234 |

| Survives, worth copying | `staging` | `MCP` |
|---|---|---|
| `core/workflows/` | 7,913 | 7,929 |
| `whm/` | 3,548 | 3,738 |
| `core/tools/` | 2,726 | 2,738 |
| `proxmox/` | 2,256 | 2,279 |
| `storage/` | 1,745 | 1,921 |
| `core/auth/` | 1,357 | 1,357 |
| `pmg/` | 793 | 793 |
| `core/remote_exec/` | 335 | 335 |
| `core/secrets/` | 136 | **290** |

`core/secrets/` is load-bearing: on `staging` it holds only `crypto.py` + `redaction.py`.
`yopass.py`, `password.py` and `docs/integrations/yopass.md` — the exact files section 8.5 ports —
exist only on `MCP`/`main`.

Shared between admin and MCP, the number that decided section 4: `storage/` + `core/auth/` +
`whm/integrations/` + `proxmox/integrations/` + `core/remote_exec/` + `pmg/integrations/` +
`core/secrets/` = **~5,348** (`staging`) / **~5,538** (`MCP`). Either total supports section 4; the
argument never turned on the exact figure.

---

## 3. DECIDED — tool granularity: one workflow, one tool

Core design rule. **One major workflow = one exposed tool.** Preflight, check A, check B, do D are
*internal functions*. The model does not need to know preflight exists.

```
noa-old: whm_preflight_account + whm_suspend_account    2 tools, 2 LLM rounds
noa:     whm_suspend_account(server_ref, username)      1 tool, preflight runs inside
```

### 3.1 Why this is the only lever available

`noa-old` solved context bloat with dynamic relevance gating (`tool_selection.py`, 535 LOC). Dead
under MCP: `select_tools_for_turn(messages, registry)` needs the latest user message per turn, but
`tools/list` is per-connection and never sees user messages. Unimplementable. Exposing fewer tools
by design is the only remaining lever.

### 3.2 Why this also shrinks the security work

Separate preflight call means its result must be persisted and the following CHANGE call must find
matching evidence. That generates an evidence store, `conversation_ref` scoping, a freshness
window, fail-closed logic and a `require_preflight` protocol change across 8 sites — old
T143/T144/T145/T146/T155/T160, the largest single block of P2.

Preflight **inside** the CHANGE call: evidence is born in-process, lives milliseconds, belongs to
the same user, never enters a transcript. "Evidence never from LLM args or transcript" becomes
structurally true instead of gate-enforced. `conversation_ref` demotes to an audit label.
Fail-closed stops being necessary. Cheaper in context, safer, materially less code.

### 3.3 Constraint that must survive the merge

Discovery READ tools stay separate — "which accounts exist on server X" cannot fold into suspend.
**Requirement:** an ambiguous identifier returns a structured candidates result and **never a
guess**. Designed in from the start, not patched on later.

### 3.4 Target size

Superseded by 6.8 and 9 (owner-confirmed, then measured against the live catalog). Pre-list
estimate was `39 → ~14`; shape of the reasoning was preflights internal, `validate_server` to
admin, `update_workflow_todo` internal, CHANGE tools collapsed into workflow families.

---

## 4. DECIDED — single repo, three deployables

```
noa/
  apps/api/          one FastAPI, two router sets: /mcp + /admin
  apps/admin-web/    Next — admin panel (servers, users, roles, audit)
  apps/web-embed/    Next — iframed approval card
```

One Alembic, one shared `core/`, three deploy artifacts — the pattern `noa-old` already shipped
(`apps/api` + `apps/web`, one repo, two images).

### 4.1 Reasoning

1. **~5,348 LOC is genuinely shared** (section 2) with no clean cut line. Admin is not DB CRUD:
   `api/whm_admin/service.py` imports `whm.integrations.ssh` and `core.remote_exec.ssh` because
   validating a server means SSH connect, capture fingerprint, TOFU refresh;
   `api/pmg_admin/service.py` imports `pmg.integrations.pmgsh_cli`. Two repos = duplicating or
   packaging ~1,775 LOC of integration code.
2. **Tables are written by one side and read by the other, both directions.** MCP writes
   `tool_runs`, admin audit reads it. Admin writes `role_tool_permissions`, MCP reads it on every
   `tools/list`. Admin CRUDs the `*_servers` tables, MCP reads them at execution. Decisive case:
   admin disabling a user must **cascade-revoke `mcp_tokens`** — split repos mean either admin
   calls an MCP API to revoke or writes another service's table, and both break the property that
   `is_active=False` takes effect immediately.
3. **Repo structure is not the source of the pain.** 39 tools and a chat UI are granularity
   problems; splitting repos cures neither.

### 4.2 Consequence

`apps/web-embed` lives here too. Separate MCP and admin processes later = a **deployment**
decision, not a repo decision: one codebase, run twice, different router sets.

### 4.3 When two repos would be right

Non-technical reasons only: separate teams with different access rights, or a compliance rule that
MCP code must not sit beside admin code. Then: single schema owner plus an internal package for the
~5.3k shared LOC, versioning cost accepted knowingly.

---

## 5. DECIDED — paths and roles

Rename performed by the owner; it is done.

| Thing | Path | Role |
|---|---|---|
| This repo | `~/noa/noa` | Active work. Named `noa`, covers all three apps. |
| Reference | `~/noa/noa-old` | Patterns source. Abandoned after the port. |
| `noa-admin` | folded in per section 4 | Not a separate repo. |

**Landmine:** `noa-old`'s checked-out branch is `staging` and its `SPEC.md` holds **no
MCP/LibreChat material**. That work is on branch **`MCP`** (top commits `6912ae4` → `6cc2cff` →
`6e2fc52`), carrying C17/C18, V138–V180, T98–T171. `MCP` is spec-only: no MCP dependency in
`pyproject.toml`, no `apps/web-embed`, nothing implemented. Read `git show MCP:SPEC.md`, never the
working tree.

The 2026-07-27 research notes that backed this section lived untracked in the old repo's `tmp/` and
are gone. What replaced them is measured: `docs/spikes/librechat-embed-render-gate.md` (verdict)
and `spikes/librechat-embed-render-gate/` (harness).

---

## 6. DECIDED — tool surface (owner-confirmed 2026-08-04)

Owner supplied the in-use list; all 39 `noa-old` registry tools are classified.

### 6.1 Workflows in use — these get built

| # | Need | Old tools folded in |
|---|---|---|
| 1 | Suspend / unsuspend WHM account | `whm_suspend_account`, `whm_unsuspend_account`, `whm_preflight_account` |
| 2 | List WHM accounts | `whm_list_accounts` — needs the large-output fix, 6.7 |
| 3 | Search WHM accounts | `whm_search_accounts` |
| 4 | Check IP firewall status (CSF **and** Imunify) | `whm_preflight_firewall_entries` — **exposed**, step 1 of the real flow (6.5) |
| 5 | Release IP from deny **and** add to allow | `whm_firewall_unblock` + `whm_firewall_allowlist_add_ttl` → **one tool** (6.5) |
| 6 | Remove IP from allowlist (undo) | `whm_firewall_allowlist_remove` |
| 7 | Reset Proxmox VM password | `proxmox_reset_vm_cloudinit_password` + its preflight |
| 8 | Enable / disable Proxmox VM NIC | `proxmox_enable_vm_nic`, `proxmox_disable_vm_nic` + preflight |
| 9 | PMG whitelist add / remove | `pmg_whitelist_add`, `pmg_whitelist_remove`, `pmg_whitelist_search`, `pmg_whitelist_list` |
| 10 | Server discovery | `whm_list_servers` — **exposed** (6.6) |

Dual-backend CSF+Imunify already worked in `noa-old`
(`whm/tools/firewall_tools/__init__.py`): each tool checks `available["csf"]` / `available["imunify"]`
and acts on both. Copied, not rebuilt — and here the fan-out is behind one door,
`firewall_gate.run_on_usable_backends` (AST-guarded).

Naming asymmetry fixed in the rewrite: `noa-old` allowlist had `add_ttl` + `remove`, denylist
`add_ttl` + `unblock`; `unblock` *is* denylist-remove.

### 6.2 Dropped — never permitted, do not implement

Owner: **not approved by management**. A boundary to preserve, not dead weight; re-adding is a
policy decision, not a technical one. Recorded in code as
`core.auth.tool_catalog.NEVER_IMPLEMENT_TOOLS`, with a test asserting it stays disjoint from
`TOOL_CATALOG`.

- `whm_change_contact_email`
- `whm_change_primary_domain` — sunk cost: earlier work removed DNS-zone verification and dropped
  the `cpanel-api` ACL dependency. That work dies with the tool.
- `proxmox_move_vms_between_pools` ("Change Email PIC") — heaviest schema, most-fixed workflow. Its
  preflight and `proxmox_get_user_by_email` die with it.
- `whm_check_binary_exists` — build-time test tool.
- `whm_firewall_denylist_add_ttl` — **owner reversed course 2026-08-04**: manually adding a deny is
  not part of the workflow; CSF and Imunify auto-deny spam sources. The real job is the opposite
  direction (6.5).

### 6.3 Demoted to internal functions

- `whm_list_servers`, `proxmox_list_servers`, `pmg_list_servers` — server resolution stays
  internal. **Exception: `whm_list_servers` is exposed, see 6.6.**
- `proxmox_get_vm_status_current`, `proxmox_get_vm_config`, `proxmox_get_vm_pending` — a separate
  Telegram bot covers operator-facing VM status. Code stays as preflight input.
- All `*_preflight_*` — per section 3 they run inside their workflow.
- `update_workflow_todo` — todos are plain text in the tool result.
- `get_current_time`, `get_current_date` — the model and LibreChat know the time.
- `whm_validate_server`, `proxmox_validate_server`, `pmg_validate_server` — admin-panel concern.

### 6.4 Mail-log forensics — not exposed, and not ported

`whm_mail_log_failed_auth_suspects` is **not exposed** (owner-decided 2026-08-04): no manual check
outside the unblock flow. Saved 1,820 ch, the third-heaviest schema.

**Not in this tree at all** (verified 2026-09-13: no mail-log code under `core/` or `apps/api/`).
In `noa-old` it was already internal with exactly one caller — `whm_firewall_unblock` inspected the
CSF preflight via `_extract_lfd_auth_line` and, on an LFD auth block (smtpauth / imapd / pop3d),
attached `failed_auth_suspects`, guarded by `status == "changed" and target_ok and not
still_blocked`. That is the shape to port from if the capability is ever wanted here: internal
function, one caller, never a tool.

### 6.5 FINDING — the real firewall flow is one direction, not four

Owner's working pattern, stated 2026-08-04:

```
1. check whether an IP is denied      (usually auto-denied by CSF/Imunify for spam)
2. release it from the denylist
3. add it to the allowlist
```

Nothing in the flow manually *adds* a deny — the four-tool symmetry in `noa-old` modelled a
workflow that does not exist. Consequences:

- **`whm_preflight_firewall_entries` is exposed.** It is step 1, an operator decision point, not a
  gate; the operator reads the verdict and decides. The one genuinely operator-facing preflight, so
  section 3's rule does not apply to it.
- **Steps 2 and 3 merge into one CHANGE tool**, `whm_firewall_release_and_allow(server_ref, target,
  duration_minutes)`. They are almost always run together, so one approval matches the real unit of
  work. No reason parameter — see 10.3, V160.
- **The receipt is two-part and the outcomes never collapse into one "done":** before-state = block
  reason + log evidence (why the IP was blocked), after-state = `released` **and** `allowlisted`,
  each verified separately.
- Before-state presentation rule carried from `noa-old`: show the `csf.deny` / `csf.allow` log
  line, never a raw iptables dump.
- `whm_firewall_allowlist_remove` stays separate — the undo path, run on its own.

### 6.6 RESOLVED — `whm_list_servers` stays exposed

Owner-decided 2026-08-04, correcting the earlier plan to hide it: the model needs to know which
servers exist. Only `whm_list_servers`. `proxmox_list_servers` and `pmg_list_servers` stay internal
— those servers are few and named directly by the operator, and a Telegram bot covers Proxmox
status (6.3). `resolve_*_server_ref` still returns a `choices` list on ambiguity, so 3.3's rule
holds underneath.

### 6.7 DECIDED — large READ results render as a UI resource (option A)

`whm_list_accounts` in `noa-old` took only `server_ref` — no limit, no pagination — and returned
every account into LLM context.

**Decision:** a NOA-origin page renders the table in the iframe; the tool result carries a short
summary plus the resource. Zero token cost for the table body, and the iframe is mandatory for the
approval card anyway, so no new component. Built: `mcp_tools/ui_resource.py` +
`mcp_tools/table_surface.py`. Generalized per 8.9 — a shared capability for any large READ result.

CSV-to-S3 was **set aside, not rejected on merit**: it answers "save for offline work", not "look
at it now". If an export need appears, two costs land then — account inventory leaving for object
storage is a data-governance decision of the same class as old T123, and a presigned URL must not
sit in tool-result text (anyone who reads the transcript could fetch it), so it would have to
travel through the iframe.

`whm_search_accounts` already has `limit` 1–100, so filtered lookup was covered.

### 6.8 Surface — projected 2026-08-04, live catalog 2026-09-13

| | Tools | Schema chars | ~Tokens |
|---|---|---|---|
| `noa-old` today | 39 | 46,653 | 11,663 |
| Retained, before enum merges | 15 (9 CHANGE + 6 READ) | 19,045 | 4,761 (−60%) |
| After the section 9 merges | 13 | ~16,035 | ~4,008 (−66%) |

Arithmetic, recounted against the live registry and correct: 14 CHANGE today − 3 unpermitted (6.2)
− 1 denylist-add (6.5) − 1 merged (6.5) = 9, then −2 enum merges (section 9) = 7 CHANGE.

**Live surface is 14 tools** (`core/auth/tool_catalog.py`, asserted equal to the registered set):
the projected 13 plus `noa_get_action_result`, which the 2026-08-04 projection did not count.

- CHANGE (7): `whm_suspend_account`, `whm_unsuspend_account`, `whm_firewall_release_and_allow`,
  `whm_firewall_allowlist_remove`, `proxmox_reset_vm_password`, `proxmox_vm_nic`, `pmg_whitelist`.
- READ (7): `whm_list_accounts`, `whm_search_accounts`, `whm_preflight_firewall_entries`,
  `whm_list_servers`, `pmg_whitelist_list`, `pmg_whitelist_search`, `noa_get_action_result`.

### 6.9 "Get account details" — deferred by owner

No such tool existed; the closest was `whm_preflight_account`, an internal gate for suspend, not an
operator read. Owner: **do not add it now.** Build it later if the need is real.

---

## 7. Upstream re-verification (2026-08-04)

Two load-bearing claims from the 2026-07-27 research no longer held. Neither breaks the design;
both make it cheaper. Verified against the PRs and the spec release post.

### 7.1 LibreChat shipped server-side tool approval (HITL) — and NOA does not use it

Old claim: *"no approval gate — PR #13304 open, #12152 closed not merged."* Reality: **#12938
merged 2026-06-24** (policy + types + job state), **#13942 merged late June 2026** (runtime
interrupt/resume/SSE). Shipped in v0.8.7 or immediately after.

- Config: `endpoints.agents.toolApproval` in `librechat.yaml` — admin/instance config, not a user
  preference. `enabled` is the admin kill switch.
- Shape: `mode: 'default' | 'dontAsk' | 'bypass'` plus `allow` / `deny` / `ask` glob lists, matched
  on qualified names (`mcp:noa:*` can target NOA).
- Runtime: `PreToolUse` hook + `humanInTheLoop` + durable checkpointer; paused jobs get
  `requires_action`; resume is an atomic compare-and-set on status and `expectedActionId`.
- `packages/api/src/agents/hitl/policy.ts` defines **layer** precedence only (`endpoint` baseline
  and sole owner of `enabled`; `agent` may override; `skills` may only tighten), and even that is
  aspirational — `resolveToolApprovalPolicy` returns `layers.endpoint` and nothing else. Per-rule
  evaluation and the unmatched-tool default live in `@librechat/agents` (`ToolPolicyConfig`), not
  inspected. Treat the unmatched-default claim as unverified; 7.3 settles why it does not matter.
- **An MCP server still cannot declare that its own tool requires approval** — no
  `destructiveHint` / `readOnlyHint` / annotation reference in `policy.ts`. NOA cannot force the
  gate from its side.

**DECIDED (owner, 2026-08-04): `toolApproval` is not used.** It gates by tool-name globs at the
instance level; NOA's access model is per-user RBAC. A glob list cannot express that, and
configuring it would create a second, weaker copy of an authorization decision NOA owns. It also
avoids double friction — confirm in LibreChat, then approve again in NOA's iframe, for one action.

Split stays clean: **who may call a tool** → NOA RBAC, in `tools/list` and again at execution;
**whether this change may run** → NOA's gate, read from the DB; **LibreChat** → transport and
rendering only.

#13304 (still open) proposed a `{"confirmationRequired": true}` envelope returned *by the MCP
server*, intercepted before the LLM sees it — closer to what NOA wants. Watch it, do not plan on it.

### 7.2 MCP Apps is an official, stable extension, and #13831 moved to the official SDK

MCP spec **`2026-07-28` is released**. MCP Apps is an official extension: version table
`| 2026-01-26 | Stable |`, header `**Status:** Stable (2026-01-26)`, identifier
**`io.modelcontextprotocol/ui`**, advertised as
`capabilities.extensions["io.modelcontextprotocol/ui"]`; SDK v1.0.0 shipped 2026-01-26; SEP-1865
cited by number in ext-apps and in LibreChat's commit messages.

LibreChat **PR #13831 is still open** and has evolved: dropped `@mcp-ui/client` for the official
Anthropic SDK (`AppBridge` + `PostMessageTransport`); removed the old `handleUIAction`
intent/tool/prompt code — the exact mechanism the never-through-the-LLM rule forbids relying on;
negotiates `io.modelcontextprotocol/ui` per session, propagates `_meta.ui.csp` /
`_meta.ui.permissions`, and gates `allow-same-origin` on the sandbox running on a dedicated origin.

**Real risk to section 11's origin rule.** The cookie-POST path rests on the iframe document
sitting on NOA's origin (`text/uri-list` → `src` mode → `allow-same-origin`). #13831 adds a
same-origin sandbox proxy (`/api/mcp/sandbox`) and classifies only `text/html;profile=mcp-app` as
app-backed, leaving plain `text/html` `ui://` inert as `sandbox=""` srcDoc. Stable spec that
excludes `text/uri-list` + an out-of-tree PR that changes the render path = the proxy-mode
silent-break condition old T98 warned about. See 10.2 for the current status.

### 7.3 Net effect on plan

- Nothing in sections 3–6 changes.
- T98's upgrade checklist is urgent, not hypothetical: pin an exact LibreChat commit, and on every
  bump re-verify (a) no mcp-ui proxy at any render site, (b) `text/uri-list` still maps to `src`.
- **Spike closed 2026-08-04 — do not carry it forward.** `isHITLEnabled(policy)` is
  `policy?.enabled === true`, so both an absent `toolApproval` block and `enabled: false` return
  `false` and the HITL machinery never engages; the glob lists and any unmatched-tool default are
  consulted only after that gate opens. The feared trap — "not configuring it" silently defaulting
  every NOA CHANGE tool into a LibreChat dialog — does not exist. Carried as a rule: **absent
  `toolApproval` config bypasses entirely, and that is not relied on.**

---

## 8. RESOLVED — owner answers (2026-08-04)

All nine answered; nothing here is open.

**8.1 Repo name → `noa`** (section 5).

**8.2 Enum collapsing → adopted** (section 9).

**8.3 Admin frontend → PORT, keep BIGSU.** Copy the finished admin surface from `noa-old`'s `MCP`
branch (`apps/web-bigsu/src/app/(protected)/admin/`: users, roles, audit, audit/tool-runs, whm,
proxmox, pmg). Do not rebuild. Carried consequences: internal `@gio/bigsu-ui` / `@gio/bigsu-icons`
from `bigsu.biznetgio.pt/registry/` must resolve here, so registry access and `.npmrc` are part of
the port; the BIGSU governance docs come along (`AGENTS.md`, `CLAUDE.md`, `.claude/skills/bigsu/`,
topology doc); **only the admin surface ports** — the `web-bigsu` chat surface is replaced by
LibreChat, which is old P3 cleanup achieved for free; no legacy `apps/web` to coexist with, so the
old two-frontend rules do not apply.

**8.4 Invariant carry-over → delegated** (section 10).

**8.5 yopass → bring it as a shared internal helper**, not welded to the password-reset tool.
Port `core/secrets/yopass.py` + `core/secrets/password.py` with a stable internal API
(`generate_password()`, `store_secret_and_get_url()`) and `docs/integrations/yopass.md` with them.
Old secrets rule intact: password generated **server-side**, never an LLM argument; plaintext lives
only inside `execute()` scope, never persisted, never crosses the LLM boundary either way; the tool
returns only `yopass_url`. These helpers are not MCP tools. **Locks Python `<3.13`** — `pgpy` 0.6.0
imports the removed stdlib `imghdr` at tool-registry import time, not only when the reset tool runs.
Revisit if `pgpy` ships a 3.13-compatible release.

**8.6 LibreChat Mongo retention / access list → not needed now.** Old T123 stays a go-live gate:
answer before this carries real ops data.

**8.7 Phasing → deferred, owner-owned. Do not design phases here.** Consequence: whether a
READ-only-first phase exists at all is undecided (10.5).

**8.8 `whm_list_accounts` UI-resource page → yes, together with the approval card.** Same app, same
origin rules, same framing headers, one surface to secure.

**8.9 `pmg_whitelist_list` needs it too.** Generalize: the UI-resource path is a shared capability
for any large READ result, built once.

---

## 9. DECIDED — enum collapsing adopted (owner-confirmed 2026-08-04)

Firewall is already settled by 6.5's merge. The two remaining pairs are merged:

| Merge | Before | After |
|---|---|---|
| `proxmox_vm_nic(action: enable\|disable)` | 2 tools, 3,258 ch | ~1,700 ch |
| `pmg_whitelist(action: add\|remove)` | 2 tools, 3,052 ch | ~1,600 ch |

**Not merged: suspend / unsuspend** — opposite risk directions, clearer as two names.

**Accepted cost:** RBAC gets coarser. "PMG whitelist add" without "remove" is no longer grantable,
same for NIC enable without disable, and release-and-allow bundles two mutations under one grant.
This matters more because 7.1 makes NOA RBAC the *only* authorization layer — no LibreChat glob
fallback. A role needing one direction but not the other means undoing the merge for that pair.

---

## 10. DECIDED — invariant carry-over from `noa-old`'s spec (8.4)

Triage input, not a spec. Numbering is `noa-old`'s, kept so each line stays traceable to
`git show MCP:SPEC.md`. Per 8.7 nothing here implies a phase.

### 10.1 Carry over unchanged — the load-bearing set

| Old | Property |
|---|---|
| V138 | MCP = Streamable HTTP, one `/mcp`, thin adapter over the tool registry. Resolve `users.id` + re-check `is_active` every request. `tools/list` RBAC-filtered per user. |
| V139 | Per-user bearer token minted by NOA, not a LibreChat-asserted identity. TOFU binding to `{{LIBRECHAT_USER_ID}}`: mint NULL → bind on first use → thereafter must match, absent counts as mismatch once bound. Residual (a pasted token) stated, not hidden. |
| V140 | READ executes; CHANGE unapproved creates a pending row and does **not** execute; CHANGE approved executes once. "May this run?" always read from the DB. |
| V141 | Approve/deny never travels through the LLM. Only path = cookie POST from the embed iframe. |
| V142 | One `build_change_gate_response()` shapes every CHANGE tool result; three branches (link-out / UI resource / elicitation). |
| V143 | Text content always carries the approval URL, so link-out is a free fallback. Missing `\ui{}` marker = accepted degradation, not failure. |
| V144 | Embed URL carries `action_request_id` only. Assume a LibreChat admin can read tool-result artifacts in Mongo. |
| V146 | Same registrable domain; `noa_session` scoped `Domain=.noa.internal`, SameSite=Lax. |
| V147 | CSRF token server-minted, signed, session-bound. Double-submit alone insufficient — any `*.noa.internal` sibling can plant the cookie. |
| V148 | Embed 401 renders an explicit "cannot authenticate here" state — never a blank card, never a live Approve button. The one place a silent origin failure becomes visible. |
| V149 | Embed access control: requester-match primary (`requested_by_user_id == caller`, caller = cookie identity). Mismatch → 404, not 403, so existence does not leak. |
| V150 | Exactly one `pending → decided` transition per request, under a row lock. Concurrent double-click → one wins, loser gets 409. |
| V151 | Post-decision notification to the LLM is notification only. Ignored means the change still executed and was recorded. |
| V152 | Embed app = own project, own build/deploy, compact and self-scrolling. |
| V155 | MCP identity resolved in exactly one function, called for every MCP request. Auth-mechanism swap = one file. |
| V156 | Tokens **hashed** at rest (not Fernet — NOA only verifies). Plaintext shown once at mint. Revoke = delete row. Never logged. |
| V157 | LDAP is source of truth for employment, not just login. Long-lived tokens revalidate on a staleness interval; LDAP down = fail closed. Cascade revoke fires on admin disable too. |
| V158 | LibreChat auth = same LDAP directory, local registration disabled. Confidentiality requirement: its Mongo holds ops transcripts. |
| V159 | Embed app has no login page, no LDAP form, no credential handling. 401 → top-level "Sign in to NOA" + retry. |
| V161 | Approve → async execution, 202 + `tool_run_id`, embed polls to terminal. State in the DB, not in a connection. |
| V162 | `noa_get_action_result(id)` enforces the V149 predicate against the MCP-token identity; identical not-found shape for foreign and unknown ids, so it is not an enumeration oracle. |
| V167 | Async host = in-process asyncio task with its **own** session, plus a reaper for runs stuck in STARTED. |
| V168 | Explicit per-user cap on in-flight CHANGE executions (default 1). Over limit → 409, never a silent queue. |
| V169 | Workflow todos in the MCP path = plain text in the tool result. No route, no UI resource. |
| V172 | Approval context built at gate time and **persisted on the row**, never rebuilt from a transcript at render time. |
| V173 | One URL per request; `/approvals/[id]` owns the whole lifecycle through receipt. |
| V174 | Pending requests expire on a TTL → terminal `expired`; approve/deny on expired → 409. |
| V175 | Approval card shows provenance: created-at, conversation ref, origin, requesting identity. A token holder *can* create a pending request — the bound is that it cannot execute. |
| V176 | Embed never triggers a decision from an inbound `postMessage`. Any listener validates `event.origin` and is read-only in effect. |
| V179 | Every MCP READ writes a `tool_runs` row: requester, truncated summary, redacted args, queryable in admin audit. |

### 10.2 Embed on NOA's own origin — carried, and re-verified

Requirement holds: the embed iframe document sits on NOA's own origin.

**Gate PASSED 2026-08-08** against LibreChat pin `45cc53c4`: a NOA-served `text/uri-list` UI
resource renders in an iframe on NOA's origin and the session cookie rides into it, so the approval
POST works from the frame. Verdict `docs/spikes/librechat-embed-render-gate.md`, harness
`spikes/librechat-embed-render-gate/`.

7.2's warning is **not** refuted and still holds as a future risk — MCP Apps is stable and excludes
`text/uri-list`, and #13831 is out of tree at that pin. What changed is the timeframe: the path
works today, and re-verification binds to **every LibreChat bump** rather than to a reading of the
spec. The explicit 401 card (V148) stays the only place a silent break surfaces.

### 10.3 Amended — property kept, mechanism changed

| Old | What changes |
|---|---|
| V145 | Framing headers. Half (a) targeted `apps/web` + `web-bigsu`; here the ported admin app sends `frame-ancestors 'none'`. Half (b) unchanged: only the embed app allowlists the LibreChat origin. The token-display route keeps `'none'`. |
| V160 | **Superseded — one field, not two.** `noa-old` split an LLM-written `proposed_reason` (evidence only) from the operator-typed authoritative `reason`. NOA has no second reason of any name: no tool schema carries a reason parameter, no table carries `proposed_reason` (`core/db/models.py`, migration `0004_action_requests`), and the LLM never authors, relays or sees one. The property V160 protected is kept and strengthened — reason is born at decision time, approve and deny both require a non-blank one (409 `change_reason_required`), and DB CHECK `ck_action_requests_decided_reason` holds it against any writer. EXPIRED carries no reason because nobody gave one. |
| V164 | "Evidence never from LLM args or transcript" kept as a rule, but structurally true instead of gate-enforced — 3.2, preflight runs in-process inside the CHANGE call. |
| V166 | "NOA does not own the conversation" holds, with **no migration**: a greenfield schema has no `threads` table and no nullable-`thread_id` retrofit. Old T111/T162/T167 collapse into initial schema design. |
| V178 | Model-facing safety policy (preflight-first, approval gates, no fabrication, argument discipline, the `\ui{}` instruction) lives in the LibreChat agent config. **New work, not a port** — there is no `core/prompts/loader.py` here to lose. |

### 10.4 Dissolved — do not implement

| Old | Why it goes |
|---|---|
| V163 | The NOA-owned evidence store exists so a *separate* preflight call can feed a *later* CHANGE call. 3.2 removes that gap. Largest single deletion — old T143/T144/T155 go with it. |
| V165 | `conversation_ref` as a **security** scope (fail-closed when absent) is unnecessary once evidence never crosses calls. Demoted to an audit/grouping label: keep recording it, stop gating on it. Old T145/T160 go. |
| V170 | "Delete `core/agent/`" — nothing to delete, greenfield never has an agent loop. Its consequence stands: **no NOA-initiated automation**, no cron path, without rebuilding a loop. |
| V177 | Retirement of relevance-based tool gating — moot, `tool_selection.py` is never ported. Section 3 is the replacement lever. |
| V153, V154 | Copy-don't-extract, the freeze rule, `COPIED-FROM.md`. These managed dual maintenance between two live frontends; `noa-old` is abandoned, so freeze is satisfied trivially and there is no ledger. |

### 10.5 Parked — depends on 8.7

V171, READ-only first (`mcp_change_tools_enabled` default false, CHANGE absent from `tools/list`).
Purely a phasing device, so the owner decides it. Not in the tree (verified 2026-09-13).

### 10.6 Not in the MCP range, but must not be dropped

Greenfield cannot inherit these by accident:

- **V1–V14** auth + RBAC — including "admin bypasses known tools but still rejects unregistered
  ones" and "disabled user has zero permissions regardless of roles".
- **V31–V37** tool discipline — argument validation, error sanitization (raw exceptions never reach
  the LLM), machine-stable lifecycle enums. `noa-old`'s "CHANGE requires `reason`" is **not**
  carried as a tool-argument rule: the reason is operator-typed at decision time (10.3, V160).
- **V45 / V53** Fernet-encrypted server secrets at rest. Distinct from hashed tokens — these must
  stay **decryptable**, they are outbound credentials.
- **V68 / V100 / V101** IPv4-only firewall targets; `sudo -n` when the SSH user is not root; SSH
  banner stripping at the boundary with raw output retained for audit.
- **V83–V89** PMG mynetworks semantics, argv-only `pmgsh`, `/32` normalization.
- **V94–V99** tool-run audit shape and sensitive-arg redaction.
- **V72–V79** approval-card presentation — each fact exactly once, no raw iptables dump in
  before-state, activity label ≤ 60 chars, summary plain text with no markdown tables.

---

## 11. Carry-over rules from `noa-old` (still binding)

Paid for in production incidents, not theory.

- **An approve/deny decision never travels through the LLM.** MCP-UI action types
  (`intent` / `tool` / `prompt`) all compose a chat message and submit it, so all are reachable by
  prompt injection. Approve is a POST from the embed iframe to NOA with the `noa_session` cookie.
- **"May this run?" is read from the database**, never from an LLM claim or a tool argument.
- **The embed iframe document must sit on NOA's own origin.** MIME `text/uri-list` is load-bearing:
  it selects mcp-ui's `src` render mode → `allow-same-origin` → the cookie rides. `text/html`
  renders `srcDoc` → opaque origin → cookie never sent → the decision path dies **silently**. The
  only place that surfaces is a 401 on the initial fetch, so that state renders explicitly.
- Copy the mature integration layers (WHM / Proxmox / PMG, `remote_exec`, `secrets`) rather than
  rewriting: SSH banner stripping and `sudo -n` escalation are hardened there. Host-key pinning and
  TOFU refresh were **not** — see `AGENTS.md`, they were fixed here.
- `core/workflows/` (7,913 LOC) is the biggest simplification candidate — much of its weight serves
  chat presentation that is being dropped.
- Hygiene limits still apply: `.ts` ≤ 300, `.tsx` ≤ 450, `.py` ≤ 900.

---

## 12. DECIDED — a change made outside NOA between the gate and the run is not detectable (2026-09-11)

**Not built. Recorded so the next reader does not rediscover it as a bug and build the one thing
this design forbids.**

Case: a card is opened to suspend an account; before the approval executes, somebody suspends it
another way. NOA runs `suspendacct`, WHM accepts, the confirming read finds it suspended, and the
receipt reports an ordinary confirmed suspension.

**No signal exists.** The runner holds three readings — gate-time before-value `suspended: false`,
post-write read `suspended: true`, and WHM's answer that it acted. NOA-only and out-of-band both
yield `false, true, accepted`. The diff does not hold it either: the field change is computed from
the gate-time value against the value asked for, so the post-write read only decides whether the
outcome is reported as verified.

Two ways to get a signal, neither available:

- **A read taken immediately before the write** — refused. Before-state means *the state the
  operator authorised*, read from gate-time evidence, never re-derived. A card saying live and a
  receipt saying already-suspended describe two different decisions, and the operator made the
  first. Adding the read would redefine before-state for every CHANGE tool to address a case none
  of them can observe.
- **A WHM response separating "suspended" from "re-suspended"** — none measured; `suspendacct`
  answers a bare success carrying no data.

The detectable variant is handled at both ends: a preflight finding the account already in the
target state answers instead of opening a card, and where the gate-time value equals the target the
delta states no field change rather than a fabricated one.

### 12.1 Why this does not contradict the rule beside it

Rule decided the same day: **remote explicitly refuses a write (WHM `result: 0`, HTTP 4xx, named
integration error) and the confirming read finds the target state anyway → report the refusal, with
the reading named beside it.**

Two branches, separated by what the remote said about itself:

- **Write succeeded** — the remote acted, so a matching state is what its own act predicts. Nothing
  to attribute elsewhere, and no reading that could.
- **Write refused** — the remote stated it did nothing, so a matching state came from elsewhere.
  Signal *stated by the remote*, not inferred from a comparison. NOA holds no measurement its own
  change took, so the reading is named rather than dropped.

Same dividing line as a timeout: a timeout says NOA does not know what the remote did, so the state
is the better witness; a refusal says the remote knows, so the refusal is.

---

## 13. DECIDED — the WHM read deadline is 120 seconds, and it is a split budget (2026-09-11)

Replaced 20 s, which was inherited not chosen: `docs/integrations/whm.md` named `timeout` only in
error-code tables, this file had no entry, `git log -S "timeout_seconds"` yields one commit (the
port) whose message lists six deliberate deviations with timeouts not among them, and `noa-old`'s
port branch carries the identical `20.0`. Boilerplate, copied twice.

### 13.1 The measurement that settles it

Production WHM server, 2026-09-11, NOA's own credential:

| Call | Wall time | WHM's answer |
|---|---|---|
| `suspendacct` | **12.09 s** | `http=200`, `result: 1` |
| `unsuspendacct` | **52.91 s** | `http=200`, `result: 1` |

**Unsuspend was broken, not at risk:** 52.91 s against 20 s means every `whm_unsuspend_account`
timed out while WHM completed the change — failure reported for a landed change, every call, by
construction. Suspend passed at 12.09 s, yet the run that opened the question exceeded 20 s on that
same call, so same-operation variance on this host is at least 1.65x.

120 s = 2.3x headroom over the measured maximum. 60 rejected as too close on a server the owner
reports as spiky.

### 13.2 Why a split budget and not a bigger number

A scalar timeout sets connect, read, write and pool alike, so covering a slow call would make an
**unreachable** host hang two minutes.

- connect 10 s — a host not answering is not answering.
- read 120 s — the one measured. Socket silence, not wall clock: fires when WHM sends nothing.
- write 30 s, pool 10 s — bounded independently.

Only read is overridable per request: a caller reading state back to learn whether a failed write
landed wants a shorter budget for that read than the write got, and nothing about it argues for a
different connect deadline. Shape ported from the Proxmox client, where this repo had already
solved a per-request override. Operator-facing name: `whm_read_timeout_seconds`.

### 13.3 Two consequences, accepted and instrumented

**Raising a deadline hides a slow server.** So the log event closing every approved change carries
the run's duration beside status and error code, on a **monotonic** clock — a wall clock stepped
backwards by NTP would report a change that finished before it started. "How long did it hang"
becomes a question the logs answer for every CHANGE tool at once.

**A slow change pins a database connection longer.** The executor holds a session across the whole
remote call; pool 5 + 10 overflow, so at a 120 s read deadline fifteen concurrent slow changes
exhaust it. Realistic concurrency is one to three, so this is a stated ceiling marked at the site
holding the session. Upgrade path: close the session before dispatching the runner, reopen it to
write the terminal run and receipt — cost is that those two writes stop sharing one transaction,
making the reaper's already-written-receipt case ordinary instead of rare.

---

## 14. DECIDED — a tool's log lines carry the call's request id, rebound at the call boundary (2026-09-11)

**The rule: an id bound once per HTTP request names the request that opened the MCP session, never
the tool call being served. Anything logged inside a `tools/call` rebinds it from the live request
at the boundary, and no log site reads the ambient binding.**

A Streamable HTTP tool call does not run in the task that served it: the session manager runs the
session in a task created during `initialize`, and a task copies contextvars at creation. So the
middleware-bound `request_id`, read inside a tool, is the `initialize` request's — one value for
every call in that session, never the one the call's response returned in `x-request-id`. Worse
than no id: the operator greps the id the client was handed and lands on the handshake.

### 14.1 The measurement

Asserted over the real mount, where middleware decides the value: logged event id vs the
`x-request-id` of the same call's response. Failed with two different UUIDs.

Both claims asserted together, because the first cannot make the second: two calls in one session
must log two **different** ids. One call matching its own header is also consistent with
coincidence, while the defect was that every call in a session shared one value; pinning each id to
its own response header additionally rules out a per-call *minted* value, which would differ across
calls and correlate with nothing.

### 14.2 A rebind at the seam, not a helper each log site must call

One rebind wraps the whole middleware hook every `tools/call` routes through: tool body, the error
sanitizer's failure line inside the tool's decorator, the CHANGE branch that writes no audit row,
the middleware's own events, and every event nobody has written yet. A per-site helper is the same
number of characters and a worse mechanism — next site wrong by default, failing silently. The one
explicit per-site read that existed was collapsed into the seam; two mechanisms for one fact teach
the next reader the wrong one.

Two wraps, not one: the RBAC gate is registered first and is therefore **outermost**, so a refused
call is logged before the audit middleware's rebind runs. Both gate hooks are wrapped — the denial
event has two emitters, the refused call and the caller whose `users` row disappeared between
authentication and dispatch, which `tools/list` reaches too. Same helper at both.

**Off-request, nothing is bound and nothing is minted.** Direct call, non-HTTP transport, a test
driving a tool alone: no request to name, and a fresh uuid nobody can correlate is the same silence
in a better costume. The rebind is a no-op.

### 14.3 The approval runners are deliberately not on this path

An approved CHANGE does not run during a `tools/call`. The executor creates its task when the
approval lands, so runner log lines inherit the contextvars of **the request that approved the
change** — the request that caused the run. Correct, and must stay correct.

Stamping the tool call's id there is the same error pointing the other way: the call that opened the
card and the request that decided it are two requests, often minutes apart and often from a
different document. Extending the rebind to the runners breaks a correlation that works.

### 14.4 Rejected

- **A fourth middleware ahead of the RBAC gate** — a new class plus a registration to reach one log
  line an existing hook can carry. Reordering the two middlewares was rejected harder: the gate
  sitting outside the audit middleware is what makes a refused call write no audit row, and that is
  asserted.
- **Minting an id when no HTTP request is in scope** — every line gets a plausible field that joins
  to nothing. Absence of the key is the honest answer, asserted through behaviour (the capture sees
  no `request_id`) rather than the returned object's type, so it survives respelling the no-op.
- **Rebinding the plain `ContextVar` the request-context module exposes** — no production code reads
  it inside a tool; its only reader is a test. A production reader on the tool path would need the
  same treatment.

**Known ceiling, same failure one level down:** the rebind works because middleware and tool share a
task (`call_next` awaited directly). Anything under a tool that moves work into its own task stops
carrying the id, silently.

---

## 15. DECIDED — the copied summary carries one identifier, not four (2026-09-12)

**The rule: the block an operator pastes out of the frame carries exactly one identifier an
administrator can look the run up by — `tool_runs.id` — beside the requester's email. The
action-request id, the conversation reference and the LibreChat account id are not in it.** Amended
2026-09-13 in both directions: the raw tool name left, the run id gained a second surface (15.2).

Reverses the argument that sat in `apps/web-embed/src/lib/approvals/summary.ts` (carry all four, a
ticket is searched by them). What settles it is who reads the block: the operator who cannot open
`/admin` — the whole reason a summary is copyable — who needs one string to hand to somebody who
can. The other three are already in front of whoever can, in the admin drawer
(`apps/admin-web/src/components/admin/audit/action-request-detail-drawer.tsx`), and both the audit
list and that drawer are keyed on `tool_runs.id`. Four identifiers hand a reader a lookup problem in
place of a lookup; one that works beats four of which one works.

### 15.1 The card's own reasoning retires with the change

`apps/web-embed/src/app/approvals/[id]/card-view.tsx` had dropped the LibreChat account id and the
conversation reference, and **one of its two stated reasons was that the copied block carried
them**. Neither surface does now, so that half is gone and the comment at the site says so.

Surviving half, which was always doing the work: those two identify the request to NOA and to
`/admin` rather than to the person deciding it, and a card that fits one screen spends its rows on
what a decision turns on.

Recorded because the failure mode is quiet: a reader who finds the card missing them, checks why,
and reads a reason that is no longer true will put them back — and be right to.

### 15.2 AMENDED 2026-09-13 — the tool name left, and the run id gained the card

**The rule now: the run id renders on the completed card *and* in the copied block, for every tool,
one label and one spelling in the same position on both. The raw tool name renders on neither.**

New evidence: the surfaces were rewritten so every clause is measured, documented or owner-stated
(section 18), with what an engineer or administrator needs reached from `/admin`. Section 15's own
test — who reads the block — separates the pair:

- **Run id** — the one technical value that is also the user's business: pasted into a ticket,
  looked up directly by an administrator. That argument never depended on copying and the card has
  the same reader, so it gains the card rather than losing the block.
- **Raw tool name** — an administrator's string with no such crossing.
  `whm_firewall_release_and_allow` is what somebody greps for; these surfaces carry a headline in
  the operator's words, and the name renders in the audit list's `Tool` column and the drawer.

**A PENDING card carries no run id and nothing is substituted.** A run id exists only once a run has
started; `none` would state an identifier that does not exist, and the action-request id is
explicitly not put in its place — the same absence-renders-as-absence rule the receipt facets obey.

---

## 16. DECIDED — the zone is named once, in a heading, and the stamps under it are bare (2026-09-12)

**The rule: a bare wall-clock stamp may leave the frame only under a heading naming the zone. In the
copied block that heading is `When — all times Jakarta (WIB)`, and the stamps under it carry no
offset.**

The offset moved, not dropped: repeated per line it becomes furniture a reader stops seeing; stated
once above the values it governs it is read first. A stamp with neither is not on the table — a
wall-clock time alone in a ticket is read in the reader's own zone, and pinning `Asia/Jakarta`
(never the machine's) exists so two operators quoting one approval quote one instant.

**Enforceable because there is exactly one stamp function.** `formatJakarta` in
`apps/web-embed/src/lib/format/jakarta-time.ts` is the only thing in the package rendering an
absolute instant. The tempting call, once the offset is off the line, is a shorter variant beside it
for a caller with no heading — which is precisely how a bare stamp reaches a ticket with nothing
above it. There is no second one, and that absence is the control.

**The rule is written at both sites that can break it** — the `formatJakarta` docstring and the
header docstring of `apps/web-embed/src/lib/approvals/summary.ts`. Restating one and leaving the
other hands the next reader a per-line offset to put back with a comment agreeing it belongs.

Heading above it or zone on the value, never neither. The exception this section used to name (the
before-state row rendering `account.suspendtime`, which had no heading and so put `(WIB)` on the
value) went with that block and module (15.2).

### 16.1 AMENDED 2026-09-13 — there is a second stamp function, and it is in Python

**The TypeScript control is intact and was never spent** — no second variant beside `formatJakarta`.
What changed is a stamp on the **Python** side, and a rule enforceable in one language and unstated
in the other is a rule with a door in it.

**`jakarta_stamp` in `apps/api/src/noa_api/mcp_tools/whm_firewall_release_outcome.py`** answers
`12 Sep 2026, 8:26 PM (WIB)` for the one sentence a firewall runner composes around an expiry. **It
appends `(WIB)` unconditionally and takes no parameter that can suppress it** — no bare form to
escape in, no flag a later caller could reach for. An optional suffix would defeat the rule outright.

**Composed in Python because the sentence is** — `The allow entry expires <stamp>.` is the firewall
family's vocabulary, and the branch dropping the sentence altogether is a Python branch. A renderer
composing it would be a table mapping each tool to a phrasing: a second copy of six runners'
vocabularies with nothing reading it against them, blank the day a runner is added, stale the day
one is reworded. The card renders the runner's bytes untransformed, so the stamp is born where the
sentence is.

**A card has no zone-naming heading the way the copied block does** — one sentence, nowhere to put
one, so the zone rides on the value or is not stated at all. Section 16's trade, taken in the only
direction the surface leaves open.

---

## 17. DECIDED — every timestamp NOA writes comes from the application clock (2026-09-12)

**The rule: `created_at` and `updated_at` are stamped by `core.clock.now_utc` through the column
helpers in `core/db/columns.py` — the clock `decided_at`, `expires_at` and `completed_at` already
used. `server_default=func.now()` stays on both columns, only as the fallback for rows inserted
outside the ORM (Alembic, `psql`). No DDL changed, so no migration.**

### 17.1 The measurement that forced it

2026-09-12, live deployment: **the API pod's host and the database host are 38 seconds apart.** Four
readings agree, one a direct probe from inside the API pod, so the gap belongs to the deployment.

- `completed_at - created_at` was the true duration **minus 38 s**: runs shorter than 38 s yielded a
  negative, so a duration rendered only for longer runs and every faster run printed a completion
  stamped before its own start.
- A decision taken within 38 s of its request printed as decided before it was asked for.

Both are an audit trail stating an impossible order, which is the one thing an audit trail cannot do
and remain worth reading.

### 17.2 Why the application clock rather than the database's

Worse arithmetic the other way: the database clock means four writers instead of one helper, and two
of the four are values the code reasons about before any row exists — the expiry the gate computes
and the approval window the card counts down are decided in Python, not returned by an INSERT.

Spelled `default=lambda: now_utc()` rather than `default=now_utc`, with the reason at the site: the
lambda resolves the name at call time, which lets a test pin the clock and watch its assertion fail
against unmodified production code. A bare reference captures the function at import, making the pin
a no-op — and an assertion that cannot be made to fail is not evidence.
`apps/api/tests/test_tool_runs_schema.py` holds both halves: a pinned sentinel no Postgres would
return, read back with a column-level SELECT so the assertion is against what was stored rather than
the flushed instance; and the plain start-before-finish check, stated as only biting on a lane whose
database clock runs ahead rather than left to read as broader coverage.

### 17.3 Accepted cost, and the part that is not code

**Audit-list ordering now rests on pod clocks agreeing across nodes** rather than one database
clock: two API pods on drifting hosts can write rows whose `created_at` order is not their real
order. The list sorts `created_at DESC, id DESC` (`core/audit/tool_run_reads.py`) and that
tiebreaker predates this for another reason (two calls in one millisecond are ordinary on the MCP
path), so the page boundary stays reproducible under drift even where order inside a tied group is
not wall-clock truth. Trades a defect firing on every run shorter than 38 s for a risk needing two
nodes to drift.

What remains is one host stepping its clock backwards mid-run, which is what an NTP correction is.
`_duration_ms` in `core/audit/tool_run_reads.py` clamps at zero rather than rendering a negative.
The embed's second guard — a duration formatter answering `null` rather than averaging a backwards
step away — went with the card's execution block in the rewrite (15.2); no elapsed time renders on
those surfaces now, so `/admin` is the one surface stating a duration and the one holding the clamp.

**NTP on the Kubernetes node and the database VM is a separate task and the owner's.** Not blocked
by this change, not replaced by it: the code fix makes NOA's stamps comparable **to each other**,
which is what a duration and a decision order are made of; NTP makes them comparable to anything NOA
did not write — a screenshot's clock, a target's log line, a certificate's validity window.

---

## 18. DECIDED — every clause on an approval surface is measured, documented or owner-stated (2026-09-13)

**The rule: a clause reaching the PENDING card, the completed card or the copied block carries one
of exactly three provenances. A clause with none does not get written — not softened, not hedged,
not deferred.**

- **MEASURED** — restates a value NOA read on this run, present in the evidence the gate wrote or
  the delta the runner published, pointable at the field it came from.
- **DOCUMENTED** — states behaviour written down in this repo, in `docs/` or in the implementing
  module. `<server> has no deny entry and no allow entry for <address>` is documented: that is what
  the `not_found` verdict means here, stated rather than softened into a friendlier word.
- **OWNER-STATED** — a fact the owner supplied about how these systems behave. Both shipping
  examples: *the whole account is suspended, nothing on it is reachable*, and *an address in
  `mynetworks` may relay mail through the gateway, one that is not may not, and that is all it
  means*.

**Translating a measured value into plain words is welcome. Adding a consequence nobody read is
not.** That line is the whole distinction and the one an author crosses without noticing, because
the sentence crossing it is always the helpful one. `103.94.170.25 is no longer blocked on
web08cpnpool03` is a measured verdict in words. *You can tell the customer to try again now* is a
consequence nobody measured, and it is wrong on precisely the branch where it matters — the one
where a second backend went silent and NOA cannot say the address is fully unblocked.

**An owner-stated fact must be recorded in `docs/integrations/*` as part of shipping**, or it stops
being citable the day someone checks. A card sentence whose only record is a conversation cannot be
verified or corrected, and will eventually be deleted as unsourced — or kept and extended.

**Why this is a decision and not a style note.** These surfaces are read to decide whether to touch a
production machine, and the failure is not a wrong fact but a *confident* one: a clause with no
provenance reads exactly like a clause with one, and no test can separate them. The nearest
mechanical guard is that no text on these surfaces is authored at runtime — the runner composes it
in Python beside the family holding the vocabulary, and the card renders those bytes untransformed —
which narrows *where* an unsourced clause can be written to one file per family, but does not stop
one being written there. The rest is this rule, stated at the sites that can break it.

**The corollary, and the half that gets lost:** a fact whose row is deleted from these surfaces has
to land somewhere or be declared gone. Four honesty properties survived the rewrite by moving rather
than staying — the names of sources that could not answer moved into the runner's sentence (named,
never counted); the cap on a capped reading moved onto the evidence block's closing line; the split
between "nothing was measured" and "the runner compared and nothing moved" moved into the
before-clause the runner composes; and the four verification states moved into the corner word, all
four still distinguishable. Deleting the assertion that guarded one of those because it went red is
how an honesty property leaves without anyone deciding it should.

## 19. DECIDED — a capability probe asks its question with an argv NOA already uses for work (2026-09-14)

**The rule: the argv the firewall availability probe sends is drawn from the set of commands NOA
sends for work. Never a command chosen only because it looks harmless.**

A sudoers grant scoped correctly is *per-argument*. `NOPASSWD: /usr/sbin/csf -g *` permits `csf -g`
and nothing else, so a probe outside the granted set is denied on a server where every working
command is permitted — and `is_sudo_rights_failure` correctly reads that denial as a rights
failure, which `firewall_gate.require_usable_backends` correctly turns into `ssh_sudo_required`.
Every layer behaves as designed and the operator is told to fix a sudoers line that is already
right.

### 19.1 The measurement

Read on `web16-cpn`, 2026-09-14, with the account NOA connects as. The probe was sending
`TERM=dumb sudo -n /usr/sbin/csf -v` and `sudo -n imunify360-agent version`; both answered
`sudo: a password is required`, and `whm_preflight_firewall_entries` returned
`ssh_sudo_required`. The grant on the box — published in `docs/integrations/whm.md` under
"Required sudoers entries", not copied here — covers `csf` with `-g`, `-tr`, `-dr`, `-ta`, `-tra`,
`-ar` and `imunify360-agent` with `ip-list`. Those seven are exactly the argv NOA sends for work;
the two it was probing with were the only two it never otherwise sends. Re-derived during review
by walking the AST of every production argv, which is now the standing guard
(`test_whm_firewall_gate.py::test_every_firewall_argv_sits_inside_the_sudoers_grant`, keyed on
call names — a wrapper under a new name is outside it, stated in that test rather than implied
away).

Measured on the same box, same day, same account: `TERM=dumb sudo -n /usr/sbin/csf -g 127.0.0.1`
and `sudo -n imunify360-agent ip-list local list --by-ip 127.0.0.1 --json` both exit 0. The probe
now sends those. A loopback address is the target because only the exit status is read, never the
body, and no firewall holds an entry for it.

That the same grant is issued fleet-wide by the infra team is **owner-stated**, not measured here.

### 19.2 Rejected — ask the infra team to add the two sudoers entries

Owner decision: the request would not be approved, and it would have to be repeated on every host
in the fleet. It is also the weaker fix — it widens a grant to make a probe work rather than
making the probe ask a question the grant already answers, and the next argv NOA adds would have
the same problem again.

The operator-facing message says so: `firewall_gate.MESSAGE_SUDO_REQUIRED` names the subcommands
rather than the binaries, because "grant NOPASSWD for csf" is the request that gets refused.

### 19.3 Rejected — delete the probe and derive availability from the real call

It removes the same failure class, and it is the structurally cleaner answer: the only fully
honest capability question is the work itself. It was refused on cost, not on merit. It rewrites
`firewall_gate` and the five `check_firewall_binaries` call sites in `whm_firewall.py`,
`whm_firewall_allowlist.py` and `whm_firewall_change.py`, plus the invariant tests behind them —
and once the argv fix is in, it buys two saved SSH handshakes per call. Recorded here rather than
left in a plan so it is not re-litigated from scratch; new evidence about the handshake cost would
reopen it.

### 19.4 Accepted costs

On the READ path the probe and the lookup are now the same command shape against different
targets, so a preflight makes four SSH connections where two would do — unchanged from the count
before this change.

The Imunify probe is **stricter** than `version` was: an agent that is installed but not running
fails `ip-list local list`. Before, the probe passed and the lookup surfaced Imunify's own
`imunify_command_failed` text; now the backend reports unusable and is skipped, with
`available_backends.imunify: false` naming it. Named here rather than discovered later. Revisit
only if it is seen.

### 19.5 PMG is unaffected

`core/servers/validation.py` probes PMG with `run_pmg_version_probe`, whose argv is
`["get", "/version"]` — the same exposure in principle. The owner states PMG's grant is not scoped
per-argument, so nothing under `core/integrations/pmg/` or `core/servers/validation.py` changes.
If a PMG grant is ever narrowed, this rule is the one to apply there.
