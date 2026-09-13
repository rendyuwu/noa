# noa — Decision Notes

**Status:** design decisions from the 2026-08-04 discussion. Why-record, not a spec.
**Date:** 2026-08-04
**Amended:** 2026-08-04 — three factual corrections applied after a fact-check pass against the old
repo and upstream sources. See the inline correction blocks in sections 2, 7.1, 7.2, plus the
resolved spike in section 7.3.

**Rules live inline where enforced** — `AGENTS.md` hard boundaries, module docstrings, tests. No
numbered registry. This file keeps the why; amend a rule at its enforcement site.

Every question raised in this session is answered. Nothing is marked OPEN. Two items are
deliberately deferred rather than open: phasing (section 8.7, owner-owned) and LibreChat Mongo
retention (section 8.6, a go-live gate, not a build blocker). One item needs re-verification against
upstream before it is relied on: section 10.2 — tracked as the render-path gate.

> **Resolved 2026-08-08 — render-path gate PASSED.** The re-verification ran live against LibreChat pin
> `45cc53c4`: a NOA-served `text/uri-list` UI resource renders in an iframe on NOA's origin and
> the session cookie rides into it, so the approval POST works from the frame. Verdict and
> numbers: `docs/spikes/librechat-embed-render-gate.md`; harness:
> `spikes/librechat-embed-render-gate/`. Section 7.2's warning is **not** refuted and still holds as a
> *future* risk — MCP Apps is stable and excludes `text/uri-list`, and PR #13831 remains out of
> tree at that pin. What changed is the timeframe: the path works today, and re-verification now
> binds to every LibreChat bump rather than to a reading of the spec.

**Corrected 2026-08-04 — what this file got wrong:**

| Section | Was | Actually |
|---|---|---|
| 2 | LOC table unlabelled | Measured on `staging`; section 8.5 ports from `MCP`, where `core/secrets` is 290 not 136 and the yopass files exist at all |
| 7.1 | Precedence chain + "unmatched → `ask`" verified in `policy.ts` | That file has no rule chain and no `dontAsk`; claim unverified, and moot per section 7.3 |
| 7.2 | MCP Apps has no stability label, no namespaced ID, SEP-1865 uncited | Stable (2026-01-26), id `io.modelcontextprotocol/ui`, SEP-1865 cited |
| 7.3 | Spike needed: does unconfigured `toolApproval` still gate? | Resolved — `isHITLEnabled` is `policy?.enabled === true`; fully off. Spike closed |

Section 6.8's arithmetic (`14 − 3 − 1 − 1 = 9`) was **checked and is correct** — recounted against the live
registry: 9 retained CHANGE tools. An earlier review pass called this a miscount; that review was
wrong, and the line stands unchanged.

Owner actions outstanding, not agent tasks: rename this repo to `noa` and the old one to `noa-old`
(section 5); NTP on the Kubernetes node and the database VM (section 17, where the 38-second gap
that makes it worth doing is measured).

---

## 1. Why this repo exists

`~/noa/noa` (the old repo) works and is deployed to staging, but two problems drove a rewrite
rather than a refactor:

1. **Context bloat.** All 39 registered tools are exposed to the model every turn.
2. **Chat maintenance.** Maintaining a chat UI is not the product. The owner already decided
   (2026-07-27) to move conversation to self-hosted LibreChat and expose NOA as an MCP server.

The old repo carries a full chat stack, Assistant Transport, and an agent loop that all die under
that decision. Rewriting the exposed surface is cheaper than unwinding it in place.

**Old repo status:** reference and source of truth for patterns, not a base to fork.

---

## 2. Measurements (verified 2026-08-04, not estimates)

Tool schema cost, measured by serializing the actual OpenAI tool schemas from the live registry:

| Metric | Value |
|---|---|
| Registered tools | 39 |
| Tool schema JSON | 46,234 chars |
| Approx tokens per turn | **~11,558** |
| READ subtotal | 24,595 ch (~6,148 tok) |
| CHANGE subtotal | 21,561 ch (~5,390 tok) |

Heaviest offenders:

```
1,995 ch  CHANGE  proxmox_move_vms_between_pools
1,925 ch  READ    proxmox_preflight_move_vms_between_pools
1,820 ch  READ    whm_mail_log_failed_auth_suspects
1,632 ch  CHANGE  proxmox_enable_vm_nic
1,626 ch  CHANGE  proxmox_disable_vm_nic
1,615 ch  CHANGE  whm_change_primary_domain
```

Note the top two: one workflow (pool move) plus its preflight burns ~4k chars of schema.

Codebase split in the old repo (LOC, non-test).

> **Branch caveat (added 2026-08-04, verified).** The figures below were measured on branch
> **`staging`**. The port instructions in section 8.5 target branch **`MCP`**, where several of these
> directories differ. Read LOC from the column matching the branch you are actually copying from.
> `MCP` and `main` agree on every figure.

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

`core/secrets/` is the load-bearing row. On `staging` it holds only `crypto.py` + `redaction.py`.
`yopass.py`, `password.py`, and `docs/integrations/yopass.md` — the exact files section 8.5 instructs the
agent to port — **exist only on `MCP`/`main`**. An agent porting from the branch this table was
measured on would find nothing to copy.

Shared between admin and MCP (the number that decided section 4):

| Component | `staging` | `MCP` |
|---|---|---|
| `storage/` | 1,745 | 1,921 |
| `core/auth/` | 1,357 | 1,357 |
| `whm/integrations/` | 1,083 | 1,179 |
| `proxmox/integrations/` | 442 | 442 |
| `core/remote_exec/` | 335 | 335 |
| `pmg/integrations/` | 250 | 250 |
| `core/secrets/` | 136 | 290 |
| **Total** | **~5,348** | **~5,538** |

Either total supports section 4's conclusion — the argument never turned on the exact figure.

---

## 3. DECIDED — Tool granularity: one workflow, one tool

The core design rule of this repo.

**One major workflow = one exposed tool.** Preflight, check A, check B, check C, do D become
*internal functions*, not MCP tools. The model does not need to know preflight exists.

Example — today vs target:

```
today:   whm_preflight_account  +  whm_suspend_account     2 tools, 2 LLM rounds
target:  whm_suspend_account(server, username, reason)     1 tool, preflight runs inside
```

### 3.1 Why this is the only lever available

The old repo solved context bloat with dynamic relevance gating — `tool_selection.py`, 535 LOC.
That approach is **dead under MCP**, and the old SPEC already recorded why:
`select_tools_for_turn(messages, registry)` needs the latest user message per turn, but MCP
`tools/list` is per-connection and never sees user messages. Unimplementable.

So under MCP the only remaining lever is **exposing fewer tools by design**. There is no third
option.

### 3.2 Why this also shrinks the security work

This is the non-obvious payoff. Worth not losing.

In the old design preflight is a separate tool call, so its result must be persisted, and the
following CHANGE call must find matching evidence. That requirement generates: an evidence store,
`conversation_ref` scoping, a freshness window, fail-closed logic, and a `require_preflight`
protocol change across 8 sites. In the old SPEC that is tasks T143 / T144 / T145 / T146 / T155 /
T160 — the largest single block of P2.

If preflight runs **inside** the same CHANGE call: evidence is born in-process, lives for
milliseconds, belongs to the same user, and never enters a transcript. The old rule
("evidence must never come from LLM args or transcript") is then satisfied *structurally* rather
than by a gate. `conversation_ref` demotes to an audit/grouping label instead of a security
control. The old fail-closed rule stops being necessary.

Result: cheaper in context, safer, and materially less code.

### 3.3 Constraint that must survive the merge

Discovery READ tools stay separate. "Which accounts exist on server X" cannot be folded into
suspend.

**Requirement:** when an identifier is ambiguous, an atomic workflow tool must return a
structured "ambiguous, here are the candidates" result and **must not guess**. Today the model
handles this by calling `whm_search_accounts`, seeing 3 hits, and asking the user. That behavior
has to be designed in from the start, not patched on later.

### 3.4 Target size

Superseded by section 6, which is owner-confirmed rather than estimated. Kept only for the shape of the
reasoning: preflight tools go internal, `validate_server` moves to admin, `update_workflow_todo`
goes internal (todos were already plain text in the tool result), and CHANGE tools
collapse into workflow families.

The pre-list estimate was `39 → ~14`. The measured figure is **39 → 16 exposed** (section 6.8), or
11 if enum collapsing (section 9) is adopted. Use section 6.8, not this number.

---

## 4. DECIDED — Single repo, three deployables

Not two repos.

```
noa-mcp/                (repo name not final)
  apps/api/             one FastAPI, two router sets: /mcp + /admin
  apps/admin-web/       Next — admin panel (servers, users, roles, audit)
  apps/web-embed/       Next — iframed approval card
```

One Alembic. One shared `core/`. Three separate deploy artifacts — the same pattern the old repo
already proved (`apps/api` + `apps/web`, one repo, two images, shipped to staging).

### 4.1 Reasoning

1. **~5,348 LOC is genuinely shared** (section 2) with no clean cut line. Admin is not merely DB CRUD:
   `api/whm_admin/service.py` imports `whm.integrations.ssh` and `core.remote_exec.ssh` because
   validating a server means SSH connect, capture fingerprint, TOFU refresh.
   `api/pmg_admin/service.py` imports `pmg.integrations.pmgsh_cli`. The integration layer is
   inherently joint property. Two repos means duplicating or internally packaging ~1,775 LOC of
   integration code.

2. **Tables are written by one side and read by the other, in both directions.**
   - MCP writes `tool_runs`; the admin audit UI reads it
   - Admin writes `role_tool_permissions`; MCP reads it on every `tools/list`
   - Admin CRUDs `whm_servers` / `proxmox_servers` / `pmg_servers`; MCP reads them at execution
   - Admin disabling a user must **cascade-revoke `mcp_tokens`** — admin has to touch a table
     that, in a two-repo world, is not its own

   That last one is decisive: split repos means either admin calls an MCP API to revoke, or it
   writes another service's table. Both break the property that `is_active=False` takes effect
   immediately, which the old design guarded per request.

3. **Repo structure is not the source of the pain.** The complaint — 39 tools, ~11.5k tokens per
   turn — is about tool granularity and the chat UI, not about repo count. Splitting repos cures
   neither. Greenfield plus one-workflow-one-tool cures both, and that is fully available inside
   one repo.

### 4.2 Consequence

`apps/web-embed` lives here too. No third home needed.

If MCP and admin should later run as separate processes (plausible — MCP faces LibreChat, admin
is internal-only), that is a **deployment** decision, not a repo decision: one codebase, run
twice, different router sets. Exposure isolation without paying the duplication cost.

### 4.3 When two repos would be right

Left open deliberately, since the reason would be non-technical: separate teams with different
access rights, or a compliance rule that MCP code must not sit beside admin code. If either
applies, revisit with a single schema owner plus an internal package for the ~5.3k shared LOC,
and accept the versioning cost knowingly.

---

## 5. DECIDED — Paths and roles

| Thing | Path | Role |
|---|---|---|
| New repo | `~/noa/noa-mcp` → **renamed to `noa`** | Active work. Owner performs the rename. |
| Old repo | `~/noa/noa` → **renamed to `noa-old`** | Reference / model. Abandoned after the port. Owner performs the rename. |
| `noa-admin` | folded into this repo per section 4 | No longer a separate repo. |

**Repo name DECIDED (owner, 2026-08-04): `noa`.** The old repo becomes `noa-old`. This repo is the
real NOA going forward, which also settles section 4's naming concern — `noa` covers all three apps.

> Rename is the owner's task, not an agent action. Until it happens, paths in this file still read
> `noa-mcp` (new) and `noa` (old). Re-read this table after the rename.

**Landmine to remember:** the old repo's checked-out branch is `staging`, and its `SPEC.md`
contains **no MCP/LibreChat material**. That work lives on branch **`MCP`** (top commits
`6912ae4` → `6cc2cff` → `6e2fc52`), which carries the old repo's constraints C17 and C18, its
invariants V138–V180, and its tasks T98–T171. Branch `MCP` is spec-only: no MCP dependency in `pyproject.toml`, no `apps/web-embed`.
Nothing was ever implemented. Read `git show MCP:SPEC.md`, not the working tree.

Research backing it, all untracked in `~/noa/noa/tmp/`:

- `librechat-embed-approval-design.md` — the design behind embed-on-NOA-origin
- `lobechat-vs-noa-research.md` — comparison, twice revised
- `lobechat.md` — superseded, proven wrong (LobeChat's plugin renderer no longer exists)

---

## 6. DECIDED — Tool surface (owner-confirmed 2026-08-04)

Owner supplied the in-use list; every one of the 39 registry tools is now classified.

### 6.1 Workflows in use — these get built

| # | Need | Old tools folded in |
|---|---|---|
| 1 | Suspend / unsuspend WHM account | `whm_suspend_account`, `whm_unsuspend_account`, `whm_preflight_account` |
| 2 | List WHM accounts | `whm_list_accounts` — needs the large-output fix, section 6.7 |
| 3 | Search WHM accounts | `whm_search_accounts` — stays exposed |
| 4 | Check IP firewall status (CSF **and** Imunify) | `whm_preflight_firewall_entries` — **exposed** (owner-confirmed), step 1 of the real flow (section 6.5) |
| 5 | Release IP from deny **and** add to allow, one action | `whm_firewall_unblock` + `whm_firewall_allowlist_add_ttl` → **merged into one tool** (section 6.5) |
| 6 | Remove IP from allowlist (undo) | `whm_firewall_allowlist_remove` |
| 7 | Reset Proxmox VM password | `proxmox_reset_vm_cloudinit_password` + its preflight |
| 8 | Enable / disable Proxmox VM NIC | `proxmox_enable_vm_nic`, `proxmox_disable_vm_nic` + preflight |
| 9 | PMG whitelist add / remove | `pmg_whitelist_add`, `pmg_whitelist_remove`, `pmg_whitelist_search`, `pmg_whitelist_list` |
| 10 | Server discovery | `whm_list_servers` — **exposed** (section 6.6) |

Dual-backend CSF+Imunify already works today — verified in
`whm/tools/firewall_tools/__init__.py`: each tool checks `available["csf"]` and
`available["imunify"]` and acts on both, in parallel via `asyncio.gather`. **Copy, do not
rebuild.**

Naming asymmetry worth fixing in the rewrite: allowlist has `add_ttl` + `remove`, but denylist
has `add_ttl` + `unblock`. `unblock` *is* denylist-remove.

### 6.2 Dropped — never permitted, do not implement

Owner: **not approved by management**, so these are a boundary to preserve, not dead weight.
Re-adding any of them is a policy decision, not a technical one.

- `whm_change_contact_email`
- `whm_change_primary_domain` — note the sunk cost: earlier work removed DNS-zone verification and
  dropped the `cpanel-api` ACL dependency for security. That work dies with the tool.
- `proxmox_move_vms_between_pools` ("Change Email PIC") — heaviest schema (1,995 ch), most-fixed
  workflow. Its preflight and `proxmox_get_user_by_email` die with it.
- `whm_check_binary_exists` — build-time test tool, never real usage.
- `whm_firewall_denylist_add_ttl` — **owner reversed course 2026-08-04**: manually adding an IP to
  the denylist is not part of the workflow. CSF and Imunify already auto-deny spam sources. The
  real job is the *opposite* direction — releasing a wrongly-blocked IP. See section 6.5.

### 6.3 Demoted to internal functions — not exposed as tools

Still needed in-process; simply not in `tools/list`.

- `whm_list_servers`, `proxmox_list_servers`, `pmg_list_servers` — server resolution stays
  internal. **Exception: `whm_list_servers` stays exposed — see section 6.6.**
- `proxmox_get_vm_status_current`, `proxmox_get_vm_config`, `proxmox_get_vm_pending` — a separate
  Telegram bot already covers operator-facing VM status. Keep the code as internal preflight input.
- All `*_preflight_*` tools — per section 3, they run inside their workflow.
- `update_workflow_todo` — internal (todos are plain text in the tool result).
- `get_current_time`, `get_current_date` — the model and LibreChat already know the time.
- `whm_validate_server`, `proxmox_validate_server`, `pmg_validate_server` — admin-panel concern,
  not MCP.

### 6.4 Mail-log forensics — internal only, already chained
`whm_mail_log_failed_auth_suspects` is **not exposed** (owner-decided 2026-08-04). No need for a
manual check outside the unblock flow.

It does not disappear, though — it is already wired as an internal follow-up.
`whm_firewall_unblock` calls it directly (`firewall_tools/__init__.py:339-364`): after a successful
unblock it inspects the CSF preflight via `_extract_lfd_auth_line`, and if the block reason was an
LFD auth block (smtpauth / imapd / pop3d) it runs the mail-log search and attaches
`failed_auth_suspects` to the result. Guarded by
`status == "changed" and target_ok and not still_blocked`.

So the capability ships as an internal function with exactly one caller. This is why the owner
did not notice it as a separate tool — it has been running inside unblock all along.

Saves 1,820 ch of schema (it was the third-heaviest).

### 6.5 FINDING — the real firewall flow is one direction, not four

Owner's actual working pattern, stated 2026-08-04:

```
1. check whether an IP is denied      (usually auto-denied by CSF/Imunify for spam)
2. release it from the denylist
3. add it to the allowlist
```

That is why `whm_firewall_denylist_add_ttl` is dropped: nothing in the flow manually *adds* a deny.
The four-tool symmetry in the old repo modelled a workflow that does not exist in practice.

Consequences:

- **`whm_preflight_firewall_entries` is exposed**, not internal (owner-confirmed 2026-08-04). It is
  step 1 — an operator decision point, not a gate. The operator reads the verdict and decides
  whether to release. This is the one preflight that is genuinely operator-facing, so section 3's
  "preflight goes internal" rule does not apply to it.
- **DECIDED: steps 2 and 3 merge into one CHANGE tool** — `whm_firewall_release_and_allow(target,
  reason)` (owner-confirmed 2026-08-04). They are almost always run together, so one approval
  matches the real unit of work and halves approval friction.

  **Requirement carried from the owner's instruction:** the evidence/receipt must still tell the
  whole story in the description — *why the IP was blocked* (the CSF/Imunify block reason and the
  relevant log line), and then the resulting state set to allowlisted. So one approval, but a
  two-part receipt: before-state = block reason + log evidence, after-state = released **and**
  allowlisted, each verified separately. Do not collapse the two outcomes into a single "done".

  The old presentation rule still applies to the before-state: show only the `csf.deny`/`csf.allow`
  log line, never a raw iptables table dump.
- Keep `whm_firewall_allowlist_remove` separate — it is the undo path, run on its own.

### 6.6 RESOLVED — `whm_list_servers` stays exposed
Owner-decided 2026-08-04, correcting the earlier plan to hide it: the model needs to know which
servers exist. Option (b) from the previous draft.

Only `whm_list_servers` is exposed. `proxmox_list_servers` and `pmg_list_servers` stay internal —
Proxmox and PMG servers are few and named directly by the operator, and a Telegram bot already
covers Proxmox status (section 6.3). Revisit if that proves wrong in use.

`resolve_*_server_ref` still handles UUID / name / hostname and returns a `choices` list on
ambiguity (verified in `whm/server_ref.py`), so section 3.3's structured-ambiguity rule still applies
underneath.

### 6.7 DECIDED — large account lists render as a UI resource (option A)

`whm_list_accounts` today takes only `server_ref` — no limit, no pagination — and returns **every**
account into LLM context. On a dense server that is thousands of rows.

**Decision: UI resource.** A NOA-origin page renders the full table in the iframe; the tool result
carries a short summary plus the resource. Zero token cost for the table body. The old MCP server
contract already anticipated this ("READ → text (+ optional UI resource for large tables)"), and the iframe
infrastructure is mandatory anyway for the approval card, so this adds no new component.

CSV-to-S3 was **considered and set aside**, not rejected on merit: it answers "save for offline
work", not "look at it now". If a real export need appears later, two costs must be faced then —
account inventory leaving for object storage is a data-governance decision of the same class as
old T123 (Mongo retention), and a presigned URL must not land in tool-result text, because anyone
who can read the LibreChat transcript could fetch it (same reasoning as the id-only embed URL). It would have
to travel through the iframe.

`whm_search_accounts` already has `limit` 1–100, so filtered lookup is covered today.

### 6.8 Projected surface (measured 2026-08-04, not estimated)

Computed by serializing the actual retained schemas from the live registry:

| | Tools | Schema chars | ~Tokens |
|---|---|---|---|
| Today | 39 | 46,653 | 11,663 |
| **Retained** | **15** (9 CHANGE + 6 READ) | **19,045** | **4,761** |
| Reduction | | | **60%** |

CHANGE (9): suspend, unsuspend, **release-and-allow** (merged), allowlist remove, password reset,
NIC enable, NIC disable, PMG whitelist add, PMG whitelist remove.
READ (6): list accounts, search accounts, firewall status check, PMG whitelist list, PMG whitelist
search, list WHM servers.

Arithmetic: 14 CHANGE today − 3 unpermitted (section 6.2) − 1 denylist-add (section 6.5) − 1 merged
(section 6.5) = 9.

With the remaining section 9 enum merges (NIC and PMG only): **13 tools, ~16,035 ch (~4,008 tok), 66%.**

> Earlier drafts said "~13 exposed" (miscount), then 16. This figure reflects the final
> owner-confirmed set including the section 6.5 merge.

### 6.9 #3 "Get account details" — deferred by owner

No such tool exists today; the closest is `whm_preflight_account`, which is an internal gate for
suspend, not an operator read. Owner decided: **do not add it now**, avoid extra complexity. Build
it later if the need is real.

---

## 7. Upstream re-verification (2026-08-04) — two research assumptions now stale

The research in `~/noa/noa/tmp/` is dated 2026-07-27. Re-checked today. **Two of its load-bearing
claims no longer hold.** Neither breaks the design; both make it cheaper. Verified against the PRs
and the spec release post, not from memory.

### 7.1 CHANGED — LibreChat now has server-side tool approval (HITL). It shipped.

Old claim (research section 8 matrix, old SPEC): *"LibreChat has no approval gate — PR #13304 open,
#12152 closed not merged."*

Reality: **#12938 merged 2026-06-24** ("Slice A", policy + types + job state) and **#13942 merged
late June 2026** ("Slice B", runtime interrupt/resume/SSE). Shipped in v0.8.7 or immediately after.
Latest release line is now v0.8.8-rc1.

What it actually is:

- Config lives at `endpoints.agents.toolApproval` in `librechat.yaml` — **admin/instance config,
  not a user preference.** `enabled` is described upstream as the admin kill switch.
- Shape: `mode: 'default' | 'dontAsk' | 'bypass'`, plus `allow` / `deny` / `ask` glob lists.

> **Correction (2026-08-04, re-verified).** An earlier draft of this section attributed the
> precedence chain `deny → bypass → allow → ask → dontAsk → fallthrough(ask)` and "unmatched tools
> default to `ask`" to `packages/api/src/agents/hitl/policy.ts`. **That file contains neither.** It
> has no rule-matching chain and the string `dontAsk` does not appear in it. What it does define is
> **layer** precedence — which policy *source* wins: `endpoint` is the baseline and sole owner of
> the `enabled` kill switch; `agent` may override `mode`/`allow`/`deny`/`ask`/`reason`; `skills`
> may only tighten. Even that is aspirational — `resolveToolApprovalPolicy` currently
> `return layers.endpoint;` and nothing else. Per-rule evaluation and the unmatched-tool default
> live in `@librechat/agents` (`ToolPolicyConfig`), which `policy.ts` only imports as a type, and
> were **not** inspected. Treat the unmatched-default claim as unverified.
>
> This does not change the section 7.1 decision — see the resolved spike note at the end of section 7.3, which
> settles the question that actually mattered.
- Globs match qualified names, so `mcp:noa:*`-style patterns can target NOA's tools specifically.
- Runtime: `PreToolUse` hook + `humanInTheLoop` + a durable checkpointer; paused jobs get status
  `requires_action`; resume is guarded by an atomic compare-and-set on both status and
  `expectedActionId`, so two concurrent approvals cannot both drive the run.

This is materially better than LobeChat's `approvalMode`, which the research correctly rejected as
user-flippable. Here the operator being gated cannot switch it off — only an admin editing
`librechat.yaml` can.

**What has NOT changed, and this is the important part:** an MCP server still **cannot declare**
that its own tool requires approval. Verified in `packages/api/src/agents/hitl/policy.ts` — no
reference to `destructiveHint`, `readOnlyHint`, or any annotation. Decisions come only from `mode`
plus name-glob matching. So NOA cannot force the gate from its side.

**Consequence for NOA — nothing is relaxed, and we will not use it.** **DECIDED (owner,
2026-08-04): LibreChat's `toolApproval` is not used.** Reason: it gates by tool-name globs at the
instance level, while NOA's access model is per-user RBAC — which tools a given operator may use at
all. A glob list in `librechat.yaml` cannot express that, and configuring it would create a second,
weaker copy of an authorization decision NOA already owns.

So the split stays clean:

- **Who may call a tool** → NOA RBAC, enforced in `tools/list` and again at execution.
- **Whether this specific change may run** → NOA's own approval gate, read from the DB.
- **LibreChat** → transport and rendering only. `toolApproval` left disabled/untouched.

This also avoids the double-friction trap: with HITL on, an operator would confirm in LibreChat and
then approve again in NOA's iframe for one action.

Worth noting the shape convergence: #13304 (the still-open community PR) proposed a
`{"confirmationRequired": true}` envelope returned *by the MCP server*, intercepted before the LLM
sees it. That is closer to what NOA wants than the merged glob-based approach. Watch it, but do not
plan on it.

### 7.2 CHANGED — MCP Apps is now an official extension, and LibreChat's PR moved to the official SDK

MCP spec **`2026-07-28` is released** (no longer a release candidate). MCP Apps is named as an
official extension in the new extensions framework.

> **Correction (2026-08-04, re-verified).** An earlier draft of this section recorded, as an honest
> caveat, that the release post gives MCP Apps "**no stability label** and no namespaced ID" and
> that "SEP-1865 is not cited by number". **All three are wrong.** The `ext-apps` repository
> publishes a version table reading `| 2026-01-26 | Stable |`, the specification header states
> `**Status:** Stable (2026-01-26)`, and the extension identifier is
> **`io.modelcontextprotocol/ui`** — advertised in client capabilities as
> `capabilities.extensions["io.modelcontextprotocol/ui"]`. SDK v1.0.0 shipped alongside it on
> 2026-01-26. SEP-1865 is cited by number in the ext-apps repo and in LibreChat's own commit
> messages. The caveat also contradicted this section's own body, which references LibreChat
> negotiating `io.modelcontextprotocol/ui` per session. So MCP Apps is both official **and**
> stable — which strengthens this section's warning rather than softening it: the spec NOA's render path
> depends on is settled, and `text/uri-list` is not in it.

LibreChat **PR #13831 is still open** — but it has evolved in a way that matters:

- It **dropped the community `@mcp-ui/client` for the official Anthropic SDK**, driving
  `AppBridge` + `PostMessageTransport` directly.
- It **removed the old `handleUIAction` intent/tool/prompt code** — the exact mechanism the
  never-through-the-LLM rule forbids relying on.
- It negotiates `io.modelcontextprotocol/ui` per session, propagates `_meta.ui.csp` and
  `_meta.ui.permissions`, and gates `allow-same-origin` on the sandbox running on a dedicated
  origin.

**Consequence for section 11's origin rule — flag as a real risk.** Our whole cookie-POST decision path
rests on the iframe document sitting on NOA's origin (`text/uri-list` → `src` mode →
`allow-same-origin`). #13831 introduces a **same-origin sandbox proxy** (`/api/mcp/sandbox`) and
grants `allow-same-origin` to the inner frame **only when the sandbox runs on a dedicated origin**.
If that lands and becomes the default render path, the old embed-on-NOA-origin assumption needs
re-verification from scratch — it is precisely the "proxy mode" silent-break condition old T98
warned about. It
also classifies only `text/html;profile=mcp-app` as app-backed, leaving plain `text/html` `ui://`
as inert `sandbox=""` srcDoc.

### 7.3 Net effect on plan

- Nothing in sections 3–6 changes. Tool granularity, single repo, and the tool surface all stand.
- The P1 spike list from the old SPEC is **still required**, and T98's upgrade checklist
  is now urgent rather than hypothetical: pin an exact LibreChat commit, and on every bump
  re-verify (a) no mcp-ui proxy at any render site, (b) `text/uri-list` still maps to `src` mode.
- ~~Add a spike: confirm LibreChat's merged HITL gate stays **out of the path** for NOA tools.~~
  **RESOLVED 2026-08-04 — spike closed, do not carry it forward.** Verified directly in
  `packages/api/src/agents/hitl/policy.ts`:

  ```ts
  export function isHITLEnabled(policy: TToolApprovalPolicy | undefined): boolean {
    return policy?.enabled === true;
  }
  ```

  A strict `=== true` on an optional-chained field means **both** an absent `toolApproval` block **and**
  `enabled: false` return `false`, so the HITL machinery never engages. The file's own comment
  confirms the intent: *"HITL remains default-off for the rollout; `enabled: true` is the explicit
  opt-in."* The `allow`/`deny`/`ask` glob lists and any unmatched-tool default are only ever
  consulted **after** that gate opens — so the section 7.1 correction above (precedence chain unverified)
  is harmless: NOA never reaches the matcher.

  The feared trap — "not configuring it" silently defaulting every NOA CHANGE tool into a LibreChat
  confirmation dialog, producing double friction on top of NOA's own iframe — **does not exist**.
  Section 7.1's decision to leave `toolApproval` untouched is safe as written. Carried forward as a
  rule: **absent `toolApproval` config bypasses entirely, so it is not relied on.**

---

## 8. RESOLVED — owner answers (2026-08-04)

All nine answered. Nothing in section 8 is open.

**8.1 Repo name → `noa`.** See section 5.

**8.2 Enum collapsing → adopted.** See section 9. Final surface 13 tools.

**8.3 Admin frontend → PORT, keep BIGSU.** Copy the finished admin surface from the old repo's `MCP`
branch (`apps/web-bigsu/src/app/(protected)/admin/`: users, roles, audit, audit/tool-runs, whm,
proxmox, pmg) into this repo. Do not rebuild.

Consequences to carry:

- Internal `@gio/bigsu-ui` / `@gio/bigsu-icons` from `bigsu.biznetgio.pt/registry/` must resolve
  here — registry access and `.npmrc` are part of the port, not an afterthought.
- The old BIGSU governance docs come along: `apps/web-bigsu/AGENTS.md`, `CLAUDE.md`,
  `.claude/skills/bigsu/`, `docs/web-bigsu/topology.md`.
- **Only the admin surface ports.** The `web-bigsu` chat surface does not — LibreChat replaces it.
  That was the old P3 cleanup; starting a new repo achieves it for free.
- The old two-frontend coexistence rules do not apply: one admin app, no legacy `apps/web` to
  coexist with.

**8.4 Invariant carry-over → delegated to me.** See section 10.

**8.5 yopass → bring it, as a shared internal helper.** Owner's framing: make it shared/internal so
future tools can consume it, not welded to the password-reset tool.

- Port `core/secrets/yopass.py` + `core/secrets/password.py` into the shared `core/secrets/` layer
  with a stable internal API (`generate_password()`, `store_secret_and_get_url()`).
- Keep the old secrets rule intact: password generated **server-side**, never an LLM argument; plaintext
  lives only inside `execute()` scope; never persisted; never crosses the LLM boundary in either
  direction; the tool returns only `yopass_url`. These helpers are **not** MCP tools.
- **Locks Python `<3.13`.** `pgpy` 0.6.0 imports the removed stdlib `imghdr`, and the import fires
  at tool-registry import time, not only when the reset tool runs. Decide at scaffold. Revisit if
  `pgpy` ships a 3.13-compatible release.
- Port `docs/integrations/yopass.md` with it.

**8.6 LibreChat Mongo retention / access list → not needed now.** Old T123 remains a go-live gate:
answer before this carries real ops data, not before it is built.

**8.7 Phasing → deferred, owner-owned.** Owner will research phases in a later
session. **Do not design phases here.** Consequence: whether a READ-only-first phase
(`mcp_change_tools_enabled`) exists at all is undecided, so READ-only-first is parked (section 10.5).

**8.8 `whm_list_accounts` UI-resource page → yes, together with the approval card.** Same app, same
origin rules, same framing headers, one surface to secure.

**8.9 `pmg_whitelist_list` → needs it too.** A long PMG mynetworks list hits the same problem.
Generalize: the UI-resource path is a **shared capability for any large READ result**, not a
`whm_list_accounts` special case. Build it once, use it for both.

---

## 9. DECIDED — enum collapsing adopted (owner-confirmed 2026-08-04)

Firewall is already settled by section 6.5's merge. The two remaining pairs **are** merged:

| Merge | Before | After |
|---|---|---|
| `proxmox_vm_nic(action: enable\|disable)` | 2 tools, 3,258 ch | ~1,700 ch |
| `pmg_whitelist(action: add\|remove)` | 2 tools, 3,052 ch | ~1,600 ch |

**Final surface: 13 tools, ~16,035 ch (~4,008 tok) — 66% below today's 39 tools / 11,663 tok.**

**Not merged: suspend / unsuspend** — opposite risk directions, clearer as two names.

**Accepted cost, recorded so it is not rediscovered later as a bug:** RBAC gets coarser. Granting
"PMG whitelist add" without "remove" is no longer possible, same for NIC enable without disable, and
release-and-allow bundles two mutations under one grant. This matters more than it would have
before, because section 7.1 makes NOA RBAC the *only* authorization layer — there is no LibreChat glob
fallback. If a role ever needs one direction but not the other, the merge must be undone for that
pair.

---

## 10. DECIDED — invariant carry-over from the old repo's spec (section 8.4, my call)

My pass, as delegated. This is **triage input**, not a spec.
Numbering is the old repo's, kept so every line stays traceable to `git show MCP:SPEC.md`.
Per section 8.7, nothing here implies a phase.

### 10.1 Carry over unchanged — the load-bearing set

These survive because they encode security properties independent of how NOA's chat used to work.

| Old | Property |
|---|---|
| V138 | MCP = Streamable HTTP, one `/mcp`, thin adapter over the tool registry. Resolve `users.id` + re-check `is_active` every request. `tools/list` RBAC-filtered per user. |
| V139 | Per-user bearer token minted by NOA. Not shared, not a LibreChat-asserted identity. TOFU binding to `{{LIBRECHAT_USER_ID}}`: mint NULL → bind on first use → thereafter must match, absent counts as mismatch once bound. Residual (a pasted token) stated, not hidden. |
| V140 | READ executes; CHANGE unapproved creates a pending row and does **not** execute; CHANGE approved executes once. "May this run?" always read from the DB. |
| V141 | Approve/deny never travels through the LLM. Only path = cookie POST from the embed iframe. Reinforced by section 7.2: #13831 deletes the `handleUIAction` intent/tool/prompt code outright. |
| V142 | One `build_change_gate_response()` shapes every CHANGE tool result; three branches (link-out / UI resource / elicitation). |
| V143 | Text content always carries the approval URL, so link-out is a free fallback. Missing `\ui{}` marker = accepted degradation, not failure. |
| V144 | Embed URL carries `action_request_id` only. Assume a LibreChat admin can read tool-result artifacts in Mongo. |
| V146 | Same registrable domain; `noa_session` scoped `Domain=.noa.internal`, SameSite=Lax. |
| V147 | CSRF token server-minted, signed, session-bound. Double-submit alone insufficient — any `*.noa.internal` sibling can plant the cookie. |
| V148 | Embed 401 renders an explicit "cannot authenticate here" state — never a blank card, never a live Approve button. The one place a silent origin failure becomes visible. |
| V149 | Embed access control: requester-match primary (`requested_by_user_id == caller`, caller = cookie identity). Mismatch → 404, not 403, so existence does not leak. |
| V150 | Exactly one `pending → decided` transition per request, under a row lock. Concurrent double-click → one wins, loser gets 409. |
| V151 | Post-decision notification to the LLM is notification only. Ignored means change still executed and recorded. |
| V152 | Embed app = own project, own build/deploy, compact and self-scrolling. |
| V155 | MCP identity resolved in exactly one function, called for every MCP request. Auth-mechanism swap = one file. |
| V156 | Tokens **hashed** at rest (not Fernet — NOA only verifies). Plaintext shown once at mint. Revoke = delete row. Never logged. |
| V157 | LDAP is source of truth for employment, not just login. Long-lived tokens revalidate on a staleness interval; LDAP down means fail closed. Cascade revoke fires on admin disable too, not only the LDAP path. |
| V158 | LibreChat auth = same LDAP directory, local registration disabled. A confidentiality requirement: its Mongo holds ops transcripts. |
| V159 | Embed app has no login page, no LDAP form, no credential handling. 401 → top-level "Sign in to NOA" + retry. |
| V160 | Two reasons, never conflated: LLM-written `proposed_reason` (evidence only, insufficient to approve) vs operator-typed authoritative `reason` (never through the LLM). |
| V161 | Approve → async execution, 202 + `tool_run_id`, embed polls to terminal. State in the DB, not in a connection. |
| V162 | `noa_get_action_result(id)` enforces the V149 predicate against the MCP-token identity; identical not-found shape for foreign and unknown ids, so it is not an enumeration oracle. |
| V167 | Async host = in-process asyncio task with its **own** session, plus a reaper for runs stuck in STARTED. |
| V168 | Explicit per-user cap on in-flight CHANGE executions (default 1). Over limit → 409, never a silent queue. |
| V169 | Workflow todos in the MCP path = plain text in the tool result. No route, no UI resource. |
| V172 | Approval context built at gate time and **persisted on the row**, never rebuilt from a transcript at render time. |
| V173 | One URL per request; `/approvals/[id]` owns the whole lifecycle through receipt. |
| V174 | Pending requests expire on a TTL → terminal `expired`; approve/deny on expired → 409. |
| V175 | Approval card shows provenance: created-at, conversation ref, origin, requesting identity. Because a token holder *can* create a pending request — the bound is that it cannot execute. |
| V176 | Embed never triggers a decision from an inbound `postMessage`. Any listener validates `event.origin` and is read-only in effect. |
| V179 | Every MCP READ writes a `tool_runs` row: requester, truncated summary, redacted args, queryable in admin audit. |

### 10.2 Carry over, but re-verify before building — embed on NOA's own origin

The embed iframe document must sit on NOA's own origin. **Still holds as a requirement**, but its
justification is now stale. Per section 7.2, #13831 moved to the official Anthropic SDK, added a
same-origin sandbox proxy (`/api/mcp/sandbox`), grants `allow-same-origin` only when the sandbox
runs on a dedicated origin, and treats only `text/html;profile=mcp-app` as app-backed.

So the chain "`text/uri-list` means `src` mode means `allow-same-origin` means cookie rides" must be
**re-verified from scratch against the pinned LibreChat version**, not inherited. This is exactly
the proxy-mode silent-break condition old T98 warned about, and the explicit 401 card is the only
place it surfaces.

### 10.3 Amended — property kept, mechanism changed

| Old | What changes |
|---|---|
| V145 | Framing headers. Half (a) targeted `apps/web` + `web-bigsu`; here it becomes **the ported admin app sends `frame-ancestors 'none'`**. Half (b) unchanged: only the embed app allowlists the LibreChat origin. The token-display route keeps `'none'`. |
| V164 | "Evidence never from LLM args or transcript" is **kept as a rule** but becomes structurally true instead of gate-enforced — section 3.2, preflight runs in-process inside the CHANGE call. |
| V166 | "NOA does not own the conversation" holds, but there is **no migration**: a greenfield schema simply has no `threads` table and no nullable-`thread_id` retrofit. Old T111/T162/T167 collapse into initial schema design. |
| V178 | Model-facing safety policy (preflight-first, approval gates, no fabrication, argument discipline, the `\ui{}` instruction) must live in the LibreChat agent config. Carries as **new work**, not a port — there is no `core/prompts/loader.py` here to lose. |

### 10.4 Dissolved — do not implement

| Old | Why it goes |
|---|---|
| V163 | The NOA-owned evidence store exists so a *separate* preflight call can feed a *later* CHANGE call. Section 3.2 removes that gap: evidence is born in-process, lives milliseconds, same user, never in a transcript. Largest single deletion — old T143/T144/T155 go with it. |
| V165 | `conversation_ref` as a **security** scope (fail-closed when absent) is unnecessary once evidence never crosses calls. It **demotes to an audit/grouping label**: keep recording it, stop gating on it. Old T145/T160 go. |
| V170 | "Delete `core/agent/`" — nothing to delete; greenfield never has an agent loop. Its accepted consequence still stands and belongs in the new SPEC: **no NOA-initiated automation**, no cron path, without rebuilding a loop. |
| V177 | Retirement of relevance-based tool gating — moot; `tool_selection.py` is never ported. Section 3 is the replacement lever. |
| V153, V154 | Copy-don't-extract, the freeze rule, `COPIED-FROM.md`. These managed dual maintenance between two live frontends. Here the old repo becomes `noa-old` and is abandoned, so freeze is satisfied trivially, no ledger to keep. |

### 10.5 Parked — depends on section 8.7

| Old | Blocked on |
|---|---|
| V171 | READ-only first (`mcp_change_tools_enabled` default false, CHANGE absent from `tools/list`). Purely a phasing device, so owner decides the phasing. |

### 10.6 Not in the MCP range, but must not be dropped

Greenfield cannot inherit these by accident, so they need restating in the new SPEC:

- **V1–V14** auth + RBAC — including "admin bypasses known tools but still rejects unregistered
  ones" and "disabled user has zero permissions regardless of roles".
- **V31–V37** tool discipline — CHANGE requires `reason`, argument validation, error sanitization
  (raw exceptions never reach the LLM), machine-stable lifecycle enums.
- **V45 / V53** Fernet-encrypted server secrets at rest. Distinct from hashed tokens: these
  must stay **decryptable** because they are outbound credentials.
- **V68 / V100 / V101** IPv4-only firewall targets; `sudo -n` when the SSH user is not root;
  SSH banner stripping at the boundary with raw output retained for audit.
- **V83–V89** PMG mynetworks semantics, argv-only `pmgsh`, `/32` normalization.
- **V94–V99** tool-run audit shape and sensitive-arg redaction.
- **V72–V79** approval-card presentation — especially each fact exactly once, no raw
  iptables dump in before-state, activity label ≤ 60 chars, summary plain text with no
  markdown tables.

---

## 11. Carry-over rules (from old repo, still binding)

Preserved because they were paid for in production incidents, not theory.

- **An approve/deny decision never travels through the LLM.** MCP-UI action types
  (`intent` / `tool` / `prompt`) all merely compose a chat message and submit it, so any of them
  is reachable by prompt injection. Approve is a POST from the embed iframe straight to NOA with
  the `noa_session` cookie.
- **"May this run?" is read from the database**, never from an LLM claim or a tool argument.
- **The embed iframe document must sit on NOA's own origin.** MIME type `text/uri-list` is
  load-bearing, not a formatting choice: it selects mcp-ui's `src` render mode, which yields
  `allow-same-origin`, which is what lets the cookie ride. `text/html` renders `srcDoc` → opaque
  origin → cookie never sent → the whole decision path dies **silently**. The only place that
  failure surfaces is a 401 on the initial fetch, so that state must render explicitly.
- Copy the mature integration layers (WHM / Proxmox / PMG, `remote_exec`, `secrets`) rather than
  rewriting: SSH banner stripping, `sudo -n` escalation, host-key pinning, TOFU refresh are all
  already hardened there.
- `core/workflows/` (7,913 LOC) is the biggest simplification candidate — much of its weight
  serves chat presentation that is being dropped.
- Hygiene limits from the old repo still apply: `.ts` ≤ 300, `.tsx` ≤ 450, `.py` ≤ 900.

---

## 12. DECIDED — a change made outside NOA between the gate and the run is not detectable (2026-09-11)

**Not built. Recorded so the next reader does not rediscover it as a bug and then build the one
thing this design forbids.**

The case: an operator opens an approval card to suspend an account, and before that approval is
executed somebody suspends the same account another way — the WHM UI, another tool, a script. NOA
then runs its own `suspendacct`, WHM accepts it, and the confirming read finds the account
suspended. The receipt reports an ordinary confirmed suspension, and there is no honest way for it
to report anything else.

**There is no signal.** What the runner holds at the moment it states the outcome is three
readings:

- the before-value, `suspended: false`, taken at gate time off the evidence the operator
  authorised against;
- the post-write read, `suspended: true`;
- WHM's own answer to the mutation, which said it acted.

A NOA suspension of a live account yields `false, true, accepted`. An out-of-band suspension
followed by a NOA suspension yields `false, true, accepted`. Identical. Nor is the disagreement
hiding in the diff: the field change is computed from the gate-time value against the value the
change was asking for, so the post-write read never enters it — that read decides only whether the
outcome is reported as verified.

Getting a signal needs one of two things, and neither is available:

- **A read taken immediately before the write.** Refused, and refused for a reason older than this
  case: the before-state means *the state the operator authorised*, read from the gate-time
  evidence and never re-derived from a second read. A card saying the account was live and a
  receipt whose before-state says it was already suspended describe two different decisions, and
  the one the operator actually made is the first. Adding a pre-write read to catch this would
  silently redefine the before-state for every CHANGE tool in order to address a case none of them
  can observe.
- **Something in WHM's own response separating "suspended" from "re-suspended".** Nothing has
  measured one, and `suspendacct` answers a bare success carrying no data of its own.

The one variant that *is* detectable is already handled, at both ends: a preflight that finds the
account already in the state the change would produce answers instead of opening a card at all,
and where the gate-time value already equals the value the change asks for, the delta states no
field change rather than a fabricated one. Neither reaches into the window between the decision and
the run, which is where this case lives.

### 12.1 Why this does not contradict the rule beside it

The rule beside it, decided the same day: **when the remote explicitly refuses a write — WHM
answering `result: 0`, an HTTP 4xx, a named integration error — and the confirming read nonetheless
finds the state matching the target, the outcome is reported as the refusal, with the reading named
beside it.** Read together the two look like a contradiction. One says a matching state after a
successful write is an ordinary success; the other says a matching state after a failed write is
worth reporting as something else.

They are two different branches, and what separates them is what the remote said about itself:

- **The write succeeded.** The remote acted, so a state matching the target is exactly what its own
  act predicts. There is nothing to attribute elsewhere, and as above no reading that could.
- **The write was refused.** The remote stated what it did, which was nothing. A state matching the
  target therefore came from somewhere other than this change — and that is the signal. It is not
  inferred from a comparison; it is stated by the remote. So the refusal is what gets reported, and
  the reading is named in the message rather than dropped, because NOA holds no measurement that
  *its own* change took.

The dividing line is the same one that decides what a timeout means: a timeout says NOA does not
know what the remote did, so the state is the better witness and NOA believes it; an explicit
refusal says the remote knows what it did, so the refusal is the better witness. A successful write
is the case where both witnesses agree, and neither is being weighed against the other.

---

## 13. DECIDED — the WHM read deadline is 120 seconds, and it is a split budget (2026-09-11)

**This is the first decision the value has carried.** What it replaced was 20 seconds, and that
number was inherited, not chosen: `docs/integrations/whm.md` named `timeout` only in its error-code
tables and never as a value, this file had no timeout entry at all,
`git log -S "timeout_seconds" -- core/integrations/whm/` yields exactly one commit — the port —
whose message lists six deliberate deviations from the reference repo with timeouts not among them,
and the reference repo on its port branch carries the identical `20.0`. Boilerplate, copied twice.

### 13.1 The measurement that settles it

Taken against the production WHM server on 2026-09-11, with the credential NOA itself uses:

| Call | Wall time | WHM's answer |
|---|---|---|
| `suspendacct` | **12.09 s** | `http=200`, `result: 1` |
| `unsuspendacct` | **52.91 s** | `http=200`, `result: 1` |

**Unsuspend was broken, not at risk.** 52.91 s against a 20 s deadline means every
`whm_unsuspend_account` through NOA timed out while WHM went on to complete the change — NOA
reporting a failure for a change that had landed, on every call, by construction. The suspend side
passed at 12.09 s, and yet the run that opened this whole question exceeded 20 s on that same call,
so same-operation variance on this host is at least 1.65x. A deadline that only the faster of two
operations clears, and only sometimes, is not a deadline anybody picked.

120 seconds is 2.3x headroom over the measured maximum. 60 was rejected as too close to it on a
server the owner reports as spiky.

### 13.2 Why a split budget and not a bigger number

A scalar timeout sets connect, read, write and pool alike, so raising the budget to cover a slow
call also means an **unreachable** host hangs for the full two minutes before failing. The four
deadlines answer different questions and only one of them is about how long WHM may take to think:

- connect 10 s — a host that is not answering is not answering, and waiting longer never changes
  that;
- read 120 s — the one the measurement is about, and the only one a slow `suspendacct` binds. It is
  socket silence, not wall clock: the deadline fires when WHM sends nothing, not when the call has
  simply been running a while;
- write 30 s, pool 10 s — unchanged in character, bounded independently.

Only the read deadline is overridable per request, and that is deliberate: a caller that reads
state back to find out whether a failed write landed wants a shorter budget for that read than the
write got, and nothing about that caller's situation argues for a different connect deadline. The
shape was ported from the Proxmox client rather than invented, that being the place this repo had
already solved a per-request override.

Operator-facing name: `whm_read_timeout_seconds`.

### 13.3 Two consequences, both accepted and both instrumented

**Raising a deadline hides a slow server.** A call that would have failed at 20 s now succeeds at
80 and says nothing about it. So the log event that closes every approved change carries the
duration of the run beside its status and error code — measured with a monotonic clock rather than
the wall clock, because an NTP step backwards would otherwise report a change that finished before
it started. "How long did it hang" becomes a question the logs answer for every CHANGE tool at
once, which is what makes it safe to stop asking it of the deadline.

**A slow change now pins a database connection for longer.** The executor opens a session and holds
it across the whole remote call, WHM round trips included, and the pool is 5 with 10 overflow — so
at a two-minute read deadline, fifteen concurrent slow changes exhaust it. Realistic concurrency
for a human-approval ops tool is one to three, so this is a stated ceiling rather than a defect,
marked at the site that holds the session. The upgrade path is to close the session before
dispatching the runner and reopen it to write the terminal run and the receipt; the cost of that
path is that those two writes stop sharing one transaction with the run, which makes the stranded-run
reaper's already-written-receipt case the ordinary one instead of the rare one.

---

## 14. DECIDED — a tool's log lines carry the call's request id, rebound at the call boundary (2026-09-11)

**The rule: an id bound once per HTTP request names the request that opened the MCP session, never
the tool call being served. Anything logged from inside a `tools/call` therefore rebinds it from the
live request at the boundary, and no log site reads the ambient binding.**

A Streamable HTTP tool call does not run in the task that served it. The session manager runs the
session in a task created during `initialize`, and a task copies contextvars at creation — so the
`request_id` the HTTP middleware binds per request is, read from inside a tool, the id of the
request that opened the session. One value for every call in that session, for the life of the
session, and never the one the call's own response returned in its `x-request-id` header.

That is worse than logging no id at all. An operator debugging "the model says it cannot do this"
greps the id the client was handed, and an id that reads like a correlation while resolving to a
different request sends them to the handshake instead of to the call.

### 14.1 The measurement

Asserted over the real mount, where the value is decided by middleware rather than planted by a
test: the id on the logged event against the `x-request-id` of the response that same call
returned. It failed with two different UUIDs on the two sides — the logged value being the
`initialize` request's, the header value the call's own.

The second claim is the one the first cannot make, so both are asserted together: two calls in one
session must log two *different* ids. A single call's line matching its own header is also
consistent with coincidence, whereas the defect stated directly is that every call in a session
shared one value. Pinning each id to its own response header additionally rules out a per-call
*minted* value, which would differ across calls and correlate with nothing.

### 14.2 A rebind at the seam, not a helper each log site must remember to call

The fix is one rebind wrapping the whole of the middleware hook every `tools/call` routes through,
so it covers the tool body, the error sanitizer's failure line inside the tool's own decorator, the
CHANGE branch that writes no audit row, the middleware's own events — and every event nobody has
written yet. A helper called from each known log site would have been the same number of characters
and a worse mechanism: it leaves the next site wrong by default, and the failure it produces is
silent. Two mechanisms for one fact also teach the next reader the wrong one, which is why the one
explicit per-site read that existed in the tree was collapsed into the seam rather than kept beside
it.

It is two wraps, not one, because the RBAC gate is registered first and is therefore the *outermost*
middleware: a refused call is logged before the audit middleware's rebind runs at all. Both of the
gate's hooks are wrapped, since the denial event has two emitters — the refused call, and the caller
whose `users` row disappeared between authentication and dispatch, which the `tools/list` path
reaches as well. Same helper at both, not a second mechanism.

**Off-request, nothing is bound and nothing is minted.** A direct call, a non-HTTP transport, a test
driving a tool alone: there is no request to name. A fresh uuid on a line nobody can correlate is
the silence this exists to end, wearing a better costume, so the rebind is a no-op instead.

### 14.3 The approval runners are deliberately not on this path

A CHANGE approved in the card does not run during a `tools/call`. The executor creates its task when
the approval lands, so a runner's log lines inherit the contextvars of **the request that approved
the change** — which is the request that actually caused the run. That is correct and must stay
correct.

Stamping the tool call's id there would be this same error pointing the other way. The call that
opened the card and the request that decided it are two different requests, often minutes apart and
often from a different document; the run was caused by the decision, and the decision is what its
log lines should name. A reader who finds the runners outside the rebind and "fixes" them by
extending it breaks a correlation that currently works.

### 14.4 Rejected

- **A fourth middleware registered ahead of the RBAC gate, so one wrap covers everything.** It is a
  new class plus a registration to reach one log line that an existing hook can be taught to carry.
  Reordering the two middlewares instead was rejected for a stronger reason: the gate sitting
  outside the audit middleware is what makes a refused call write no audit row, and that is
  asserted.
- **Minting an id when there is no HTTP request in scope.** Every line gets a plausible-looking
  field and none of them joins to anything. The absence of the key is the honest answer, and it is
  asserted through the behaviour — the capture sees no `request_id` — rather than against the
  returned object's type, so the claim survives changing how the no-op is spelled.
- **Rebinding the plain `ContextVar` the request-context module also exposes.** No production code
  reads it from inside a tool; its only reader in the repo is a test. If a production reader ever
  appears on the tool path it needs the same treatment, and this sentence is the note saying so.

**Known ceiling, same failure mode one level down.** The rebind works because the middleware and the
tool share a task: `call_next` is awaited directly, so the value set in the middleware is visible in
the tool. Anything under a tool that moves its work into a task of its own stops carrying the id,
silently — which is exactly the defect above, one level deeper.

---

## 15. DECIDED — the copied summary carries one identifier, not four (2026-09-12)

**The rule: the block an operator pastes out of the frame carries exactly one identifier an
administrator can look the run up by — `tool_runs.id` — beside the requester's email. The
action-request id, the conversation reference and the LibreChat account id are not in it.**

**Amended 2026-09-13, and the amendment moves the boundary in both directions at once: the raw tool
name left, and the run id gained a second surface.** See section 15.2.

This reverses the argument that used to sit in `apps/web-embed/src/lib/approvals/summary.ts`: carry
all four, because a ticket is searched by them. What settles it is who reads the block. It exists
for the operator who cannot open `/admin` — that is the entire reason a summary is copyable at all
— and what that reader needs is one string to hand to somebody who can. The other three are already
in front of whoever can: the action-request id, the conversation reference and the LibreChat account
all render in the admin drawer
(`apps/admin-web/src/components/admin/audit/action-request-detail-drawer.tsx`), and the audit list
and that drawer are both keyed on `tool_runs.id`. So the run id is the one identifier that crosses
the gap between the two readers; the other three cross it only in the direction where nobody needed
them carried.

Four identifiers also cost more than three lines of a paste. A block is read a year later by
somebody who then has to decide which of them to quote, and a reader made to choose has been handed
a lookup problem in place of a lookup. One identifier that works beats four of which one works.

### 15.1 The card's own reasoning retires with the change

`apps/web-embed/src/app/approvals/[id]/card-view.tsx` had already dropped the LibreChat account id
and the conversation reference from the card, and **one of its two stated reasons was that the
copied block carried them** — they were one copy away. After this change neither surface carries
them, so that half of the justification is gone, and the comment at that site now says so rather
than leaving it standing.

The surviving half is the one that was always doing the work: those two identify the request to NOA
and to `/admin` rather than to the person deciding it, and a card that has to fit one screen spends
its rows on what a decision turns on. The admin drawer is where they live.

This is recorded because the failure mode is specific and quiet. A reader who notices the card is
missing them, checks why, and finds a reason that is no longer true will put them back on the card
— and will be right to, given what the comment said. The same correction was carried at the
before-state renderer, whose admin-only rows leaned on the same "one copy away" clause; that module
no longer exists, for the reason section 15.2 gives.

### 15.2 AMENDED 2026-09-13 — the tool name left, and the run id gained the card

**The rule now: the run id renders on the completed card *and* in the copied block, for every tool,
one label and one spelling in the same position on both. The raw tool name renders on neither.**

The new evidence is the approval surfaces being rewritten so that every clause on them is measured,
documented or owner-stated, with everything an engineer or an administrator needs reached from
`/admin` instead. Read against that rule the pair comes apart, and it comes apart cleanly, because
section 15's own reasoning is what separates them — *who reads the block*:

- The **run id** is the one technical value that is the user's business as well. It is the string a
  person pastes into a ticket and the string an administrator looks a run up by directly, so it
  crosses the gap between the two readers. That argument was made about the copied block and it
  never had anything to do with copying: the card has the same reader. So it gains the card rather
  than losing the block.
- The **raw tool name** is an administrator's string with no such crossing. `whm_firewall_release_and_allow`
  is what somebody greps for; the reader of these surfaces wants the label, and the surfaces now
  carry a headline written for them in the operator's own words. It renders in the audit list's
  `Tool` column and in the action-request drawer, which is where its reader already is.

**A PENDING card carries no run id and nothing is substituted.** A run id exists only once a run has
started; a placeholder reading `none` states an identifier that does not exist, and the
action-request id is explicitly not put in its place. The line appears from the moment there is a
run — which is the same absence-renders-as-absence rule the receipt facets obey, applied to an
identifier.

---

## 16. DECIDED — the zone is named once, in a heading, and the stamps under it are bare (2026-09-12)

**The rule: a bare wall-clock stamp may leave the frame only under a heading that names the zone.
In the copied block that heading is `When — all times Jakarta (WIB)`, and the stamps under it carry
no offset.**

The offset moved; it was not dropped. Every stamp in the block used to end `+07:00`, on its own
line, and the trade is between two ways of stating one fact: repeated per line it becomes furniture
a reader stops seeing, stated once directly above the values it governs it is read before the first
of them. What is not on the table is a stamp with neither. A wall-clock time alone in a ticket is
read in whatever zone the reader sits in, and pinning the zone at all (`Asia/Jakarta`, never the
machine's) exists so that two operators quoting the same approval quote the same instant.

**What makes the rule enforceable is that there is exactly one stamp function.** `formatJakarta` in
`apps/web-embed/src/lib/format/jakarta-time.ts` is the only thing in the package that renders an
absolute instant, so there is nowhere for a bare stamp to escape from. The tempting call, once the
offset is off the line, is a second shorter variant beside it for some caller that does not want a
heading — and that variant is precisely how a bare wall-clock time reaches a ticket with nothing
above it. There is no second one, and that absence is the control.

**The rule is written at both of the two sites that can break it**, because a surviving copy of the
old one is what restores the old shape: the `formatJakarta` docstring in that module, and the header
docstring of `apps/web-embed/src/lib/approvals/summary.ts`. Restating one and leaving the other
would hand the next reader a per-line offset to put back with a comment agreeing that it belongs.

Heading above it or zone on the value — never neither. The exception this section used to name was
the before-state row rendering `account.suspendtime`, which sat in a block with no heading to
satisfy and so put `(WIB)` on the value. That block and that module are both gone (section 15.2),
so the exception is gone with them and is recorded here only because a reader who finds the rule and
not the case it was written against will wonder which one moved.

### 16.1 AMENDED 2026-09-13 — there is a second stamp function, and it is in Python

**The TypeScript control is intact and was never spent.** `formatJakarta` in
`apps/web-embed/src/lib/format/jakarta-time.ts` is still the only thing in that package that renders
an absolute instant. No second variant was written there, so the argument above — that a shorter
sibling beside it is precisely how a bare wall-clock time reaches a ticket with nothing over it —
still holds with nothing replacing it. What changed is that a stamp now also exists on the **Python**
side, and a rule that is enforceable in one language and unstated in the other is a rule with a door
in it.

**What exists: `jakarta_stamp` in `apps/api/src/noa_api/mcp_tools/whm_firewall_release_outcome.py`**,
answering `12 Sep 2026, 8:26 PM (WIB)` for the one sentence a firewall runner composes around an
expiry. **It appends `(WIB)` to the value unconditionally and takes no parameter that can suppress
it.** That shape is what replaces the one-function control rather than weakening it: written this
way the stamp has no bare form to escape in, so there is no call site that could produce one and no
flag a later caller could reach for. Written with an optional suffix it would defeat the rule
outright, which is the trade this is recording.

**Why the stamp is composed in Python at all**, rather than the card formatting an instant the
runner hands it: the sentence around it — `The allow entry expires <stamp>.` — is the firewall
family's own vocabulary, and the branch that drops that sentence altogether is a Python branch. A
renderer composing it would be a table mapping each tool to a phrasing, which is a second copy of
six runners' vocabularies with nothing reading it against them: blank the day a runner is added,
stale the day one is reworded. The approval card renders the runner's bytes with no transformation,
so the stamp has to be born where the sentence is.

**A card has no zone-naming heading the way the copied block does.** The copied block states
`When — all times Jakarta (WIB)` once above bare stamps; a card has one sentence and nowhere to put
a heading, so on that surface the zone rides on the value or it is not stated at all. That is the
same trade section 16 settled, taken in the only direction the surface leaves open.

---

## 17. DECIDED — every timestamp NOA writes comes from the application clock (2026-09-12)

**The rule: `created_at` and `updated_at` are stamped by `core.clock.now_utc` through the column
helpers in `core/db/columns.py` — the same clock `decided_at`, `expires_at` and `completed_at`
already came from. `server_default=func.now()` stays on both columns and only as the fallback for
rows inserted outside the ORM (Alembic, `psql`). No DDL changed, so there is no migration.**

### 17.1 The measurement that forced it

Taken on 2026-09-12 against the live deployment: **the API pod's host and the database host are 38
seconds apart.** Four readings agree, one of them a direct probe from inside the API pod, so the gap
belongs to the deployment rather than to the vantage point it was read from.

38 seconds is enough to break every pair of stamps that spanned the two clocks, and it broke them in
the direction that reads as a bug somewhere else entirely:

- `completed_at - created_at` was the true duration **minus 38 s**. A run shorter than 38 seconds
  therefore yields a negative, so a duration rendered at all only for runs longer than 38 seconds,
  and every faster run printed a completion stamped earlier than its own start.
- A decision taken within 38 seconds of the request it decides printed as decided before it was
  asked for.

Neither is a rounding error a reader shrugs past. Both are an audit trail stating an impossible
order, which is the one thing an audit trail cannot do and remain worth reading.

### 17.2 Why the application clock rather than the database's

The other direction was available and is worse arithmetic: putting everything on the database clock
means changing four writers instead of one helper, and two of those four are values the code
reasons about before any row exists — the expiry the gate computes and the approval window the card
counts down are decided in Python, not returned by an INSERT. Two functions in one helper, against
four call sites and a migration.

Spelled `default=lambda: now_utc()` rather than `default=now_utc`, deliberately, with the reason at
the site: the lambda resolves the name at call time, which is what lets a test pin the clock and
watch its assertion fail against unmodified production code. The tidier bare reference captures the
function at import, which would make the pin a no-op — and an assertion that cannot be made to fail
is not evidence of anything. `apps/api/tests/test_tool_runs_schema.py` holds both halves: a pinned
sentinel no Postgres would ever return, read back with a column-level SELECT so the assertion is
against what was stored rather than against the flushed instance; and beside it the plain
start-before-finish check, stated as only biting on a lane whose database clock runs ahead rather
than left to read as broader coverage than it is.

### 17.3 Accepted cost, and the part of this that is not code

**The audit list's ordering now rests on pod clocks agreeing across nodes** rather than on one
database clock. Two API pods on drifting hosts can write rows whose `created_at` order is not their
real order. The list sorts `created_at DESC, id DESC` (`core/audit/tool_run_reads.py`) and that
tiebreaker is already there for a different reason — two calls in the same millisecond are ordinary
on the MCP path — so the page boundary stays reproducible under drift even where the order inside a
tied group is not wall-clock truth. Accepted knowingly: it trades a defect that fired on every run
shorter than 38 seconds for a risk that needs two nodes to drift.

What remains after the fix is one host stepping its own clock backwards mid-run, which is what an
NTP correction is. `_duration_ms` in `core/audit/tool_run_reads.py` clamps at zero rather than
rendering a negative, which is where the hazard is now guarded and named. The embed had the second
guard — a duration formatter answering `null` rather than averaging a backwards step away — and it
went with the card's execution block when the approval surfaces were rewritten to carry only what an
operator reads (section 15.2). No elapsed time renders on those surfaces at all now, so there is
nothing left there to render wrongly; `/admin` is the one surface that states a duration and it is
the one still holding the clamp.

**NTP on the Kubernetes node and on the database VM is a separate task and the owner's.** It is not
blocked by this change and it is not replaced by it. The code fix makes NOA's own stamps comparable
**to each other**, which is what a duration and a decision order are made of. NTP is what makes them
comparable to anything NOA did not write — the clock in a screenshot, a target system's log line, a
certificate's validity window. Both are wanted; neither substitutes for the other.

---

## 18. DECIDED — every clause on an approval surface is measured, documented or owner-stated (2026-09-13)

**The rule: a clause that reaches the PENDING card, the completed card or the copied block carries
one of exactly three provenances, and a clause with none of them does not get written — not
softened, not hedged, not deferred.**

- **MEASURED** — it restates a value NOA read on this run. The reading exists in the evidence the
  gate wrote or in the delta the runner published, and the clause can be pointed at the field it
  came from.
- **DOCUMENTED** — it states behaviour written down in this repo, in `docs/` or in the module that
  implements it. `<server> has no deny entry and no allow entry for <address>` is documented: that
  is what the `not_found` verdict means here, and the sentence states the documented meaning rather
  than softening the token into a friendlier word.
- **OWNER-STATED** — it is one of the facts the owner supplied about how these systems behave. Two
  examples, both shipping: *the whole account is suspended, nothing on it is reachable*, and *an
  address in `mynetworks` may relay mail through the gateway, one that is not may not, and that is
  all it means*.

**Translating a measured value into plain words is welcome. Adding a consequence nobody read is
not.** That single line is the whole distinction and it is the one an author crosses without
noticing, because the sentence that crosses it is always the helpful one. `103.94.170.25 is no
longer blocked on web08cpnpool03` is a measured verdict said in words. *You can tell the customer to
try again now* is a consequence nobody measured, and it is wrong on precisely the branch where it
matters — the one where a second backend went silent and NOA cannot say the address is fully
unblocked.

**An owner-stated fact must be recorded in `docs/integrations/*` as part of shipping**, or it stops
being citable the day someone checks. A sentence on an operator's card whose only record is a
conversation is a sentence the next reader cannot verify, cannot correct, and will eventually delete
as unsourced — or worse, will keep and extend.

**Why this is a decision and not a style note.** These surfaces are read to decide whether to touch a
production machine, and the failure they are exposed to is not a wrong fact but a *confident* one: a
clause with no provenance reads exactly like a clause with one, and nothing in a test suite can tell
them apart. The nearest mechanical guard the repo has is the rule that no text on these surfaces is
authored at runtime — the runner composes it in Python beside the family that holds the vocabulary,
and the card renders those bytes with no transformation — which narrows *where* an unsourced clause
can be written to one file per family, but does not stop one being written there. The rest is this
rule, stated at the sites that can break it.

**The corollary, and it is the half that gets lost:** a fact whose row is deleted from these surfaces
has to land somewhere or be declared gone. Four honesty properties survived the rewrite by moving
rather than by staying — the names of sources that could not answer moved into the runner's sentence
(named, never counted); the cap on a capped reading moved onto the evidence block's closing line;
the split between "nothing was measured" and "the runner compared and nothing moved" moved into the
before-clause the runner composes; and the four verification states moved into the corner word, all
four still distinguishable. Deleting the assertion that guarded one of those because it went red is
how an honesty property leaves without anyone deciding it should.
