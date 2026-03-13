# WHM Tools + Workflow TODO Design

Date: 2026-03-13

## Context

We want to bring WHM (cPanel/WHM + CSF) operational capabilities from `noa-old` into this NOA monorepo.

NOA already has:

- A tool registry (`noa_api.core.tools.registry`) that exposes tools to the LLM.
- A persisted approval gate for any `ToolRisk.CHANGE` tool (ActionRequest -> UI `request_approval` -> execute).
- Per-user tool allowlisting managed in the Admin UI.

We will not create a dedicated “WHM agent”. Instead, we will implement WHM capabilities as normal NOA tools and lean on the existing approval + persistence flow.

## Goals

- Support many WHM servers via Postgres-backed inventory (Option 1).
- Provide a focused WHM tool set (read + change) suitable for day-to-day operations.
- Make CHANGE actions safe-by-default:
  - Require approvals.
  - Do automatic preflight and postflight verification.
  - Be idempotent/no-op where possible.
- Support flexible CSF TTL durations by using minutes as the canonical input.
- Provide an in-chat, step-by-step workflow TODO that updates during a workflow so users don’t lose track.

## Non-Goals

- Re-implementing `noa-old`’s “operation” and “approval request” domain objects.
- Building a separate “WHM Agent” runtime.
- A pinned/toplevel TODO panel (we will show TODO updates as in-chat tool cards).
- Full parity with every `noa-old` WHM capability.

## Current State (Relevant)

- Tool registry currently exposes only MVP demo tools.
- Any `ToolRisk.CHANGE` tool call creates an ActionRequest, emits a `request_approval` tool-call, and ends the turn.
- The web UI already renders `request_approval` cards.
- There is no WHM integration code or WHM data model in this repo yet.

## Proposed Changes

### 1) WHM Server Inventory (Postgres)

Add a `whm_servers` table (credentials stored in DB by design choice):

- `id` UUID PK
- `name` (unique)
- `base_url` (e.g. `https://whm.example.com:2087`)
- `api_username`
- `api_token` (secret; never returned)
- `verify_ssl` bool
- `created_at`, `updated_at`

Security rule: `api_token` is write-only via admin APIs and must never appear in API responses or tool outputs.

### 2) Admin-Only WHM Server APIs

Add admin-only endpoints under the existing `/admin` surface (exact paths TBD, recommended):

- `GET /admin/whm/servers` -> list safe fields
- `POST /admin/whm/servers` -> create (accepts token, returns safe fields)
- `GET /admin/whm/servers/{id}` -> get safe fields
- `PUT /admin/whm/servers/{id}` -> update (accepts token, returns safe fields)
- `DELETE /admin/whm/servers/{id}` -> delete
- `POST /admin/whm/servers/{id}/validate` -> validate credentials/connectivity

Validation behavior:

- Call WHM API 1 `applist` to confirm connectivity and auth.
- Optionally validate CSF plugin reachability via a safe `csf_grep` call.

### 3) WHM Integration Layer

Port and adapt the `noa-old` approach into `apps/api`:

- `noa_api.integrations.whm.client.WHMClient`
  - Async HTTP client
  - Normalized error shapes (`success`, `error_code`, `message`, `details`)
  - Sanitized mutation responses (never return raw WHM payloads)
- `noa_api.integrations.whm.csf` parser utilities
  - Strict IP/CIDR parsing
  - Parse CSF grep HTML into operator-safe evidence + an “effective verdict”

Dependency: add `httpx` to API runtime dependencies (it is currently dev-only).

### 4) Server Ref Resolution

Most WHM tools take `server_ref: str`.

Resolution order:

1) UUID -> direct lookup by `id`.
2) Exact match by `name` (case-insensitive).
3) Exact match by hostname parsed from `base_url` (case-insensitive).

If resolution is ambiguous, tools return a structured error containing a bounded list of choices.

### 5) Tool Catalog

Tools will be registered in `noa_api.core.tools.registry` as `ToolDefinition` entries.

All tools will accept explicit targets (no implicit “default server”).

#### Read tools (ToolRisk.READ)

- `whm_list_servers`
  - List configured servers (safe fields only)
- `whm_validate_server`
  - Validate a server by `server_ref` (applist)
- `whm_list_accounts`
  - Paginated list via `listaccts`
- `whm_search_accounts`
  - Exact domain/username match; best-effort email match
- `whm_preflight_account`
  - Fetch the current account state needed for a mutation (e.g., suspended + contact email)
- `whm_preflight_csf_entries`
  - For each entry: CSF grep parse + verdict (blocked/allowed/none) with bounded evidence

#### Change tools (ToolRisk.CHANGE, approval-gated)

Each CHANGE tool MUST:

- Require `reason`.
- Do internal preflight to support idempotency/no-op.
- Execute mutation.
- Postflight verify and return a compact verification summary.

Change tools:

- `whm_suspend_account` (WHM `suspendacct`)
- `whm_unsuspend_account` (WHM `unsuspendacct`)
- `whm_change_contact_email` (WHM `modifyacct` contactemail)
- `whm_csf_unblock` (CSF plugin `qkill` fallback `kill`)
- `whm_csf_allowlist_add_ttl`
- `whm_csf_denylist_add_ttl`
- `whm_csf_allowlist_remove`

CSF TTL tools contract:

- Inputs: `entries: list[str]`, `duration_minutes: int`, `reason: str`
- A single `duration_minutes` applies to all entries.
- LLM converts natural durations into minutes (e.g. “5 days” -> `7200`).
- Backend converts minutes into CSF plugin params (`timeout` + `dur` minutes/hours/days) for compatibility.
- Apply `IPv4-only` and `no CIDR` restriction for TTL tools (consistent with `noa-old`).

Return shape for multi-entry CSF tools:

- `success: bool`
- `partial_success: bool` when some entries succeed and some fail/no-op
- `results: [{ entry, status: success|no-op|error, message, expires_at? }]`

### 6) Auto Preflight (LLM Guidance + Tool Behavior)

We will implement preflight in two layers:

1) Conversation-level preflight (LLM behavior)
   - System prompt + tool descriptions encourage: preflight READ tool(s) first, then the CHANGE tool.
   - This gives users visible evidence before approval.

2) Execution-level preflight (tool behavior)
   - CHANGE tools preflight again right before applying mutations (after approval).
   - Ensures correct idempotency and avoids stale assumptions.

### 7) Workflow TODO (Opencode-style, In-Chat)

Add a lightweight tool that exists purely to track workflow steps:

- Tool: `update_workflow_todo` (ToolRisk.READ)
- Input schema: `todos: [{ content: str, status: pending|in_progress|waiting_approval|completed|cancelled|failed, priority: low|medium|high }]`
- Output: echoes the current todo list + `ok: true`

The assistant uses this tool to:

- Create a checklist at the start of operational requests.
- Update statuses after preflight, when requesting approval, and after completion/verification.

Permissions:

- `update_workflow_todo` is intended to be always available to active users (baseline safe tool), even if other tools remain allowlisted.

Web UI:

- Add `makeAssistantToolUI` card rendering for `update_workflow_todo` so each update is shown as a structured checklist in the chat history.

### 8) System Prompt Updates

Update `settings.llm_system_prompt` default guidance to include:

- When performing operational actions, create/update workflow TODOs.
- For WHM CHANGE actions, always preflight first.
- For CSF TTL tools, convert durations to `duration_minutes`.

## Data Model / Migrations

- Add Alembic migration to create `whm_servers`.
- No durable CSF TTL tracking table is planned in this phase (TTL relies on CSF expiry; verification is done immediately via postflight grep).

## Security & Compliance Notes

- WHM API tokens are secrets: never return them in any response; avoid logging them.
- Tool outputs should be operator-safe: bounded evidence, no raw HTML dumps.
- CHANGE tools must remain approval-gated via existing ActionRequest flow.

## Testing & Verification

- API tests:
  - Admin WHM server CRUD + validate.
  - Tool permission behavior (denied tools produce explicit guidance).
- Tool tests:
  - CSF parsing unit tests (HTML parsing + verdict logic).
  - WHM client normalization tests (timeouts/auth/http errors).
- Manual verification (dev):
  - Add a WHM server via admin endpoint.
  - Run a “release IP” workflow: TODO -> preflight -> approval -> unblock -> verify.

## Rollout Notes

- Operators must register WHM servers via admin endpoints.
- WHM tools remain allowlisted per-user; `update_workflow_todo` is baseline.
- Start with a small set of operators enabled, then expand.
