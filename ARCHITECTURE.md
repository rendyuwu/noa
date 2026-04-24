# Project NOA - Architecture (MVP)

This document describes the implemented MVP architecture: a Next.js + assistant-ui frontend talking to a FastAPI backend via the assistant-ui **Assistant Transport** protocol, with a controlled tool layer (READ vs CHANGE) and explicit approvals for any modifying action.

## High-Level Components

- `apps/web` (Next.js)
  - UI built with `@assistant-ui/react` primitives
  - Multi-thread UX via `unstable_useRemoteThreadListRuntime`
  - Per-thread runtime via `useAssistantTransportRuntime`
  - Custom tool UI for `request_approval` (Approve/Deny)

- `apps/api` (FastAPI)
  - LDAP authentication (python-ldap) + JWT
  - RBAC: admin/user, per-user tool allowlists
  - Postgres persistence (SQLAlchemy async + Alembic)
  - Assistant Transport endpoint: `POST /assistant` (assistant-stream)
  - Tool registry + tool execution engine
  - Approval gate for CHANGE tools, with recorded reasons for each approved action

- Postgres 16
  - Local dev via `docker-compose.yml`

## Data Model (Postgres)

Core entities:

- Auth / RBAC / audit
  - `users` (email, ldap_dn, is_active, last_login_at)
  - `roles`, `user_roles`
  - `role_tool_permissions` (allowlist of tool names)
  - `audit_log` (append-only events)

- Conversations
  - `threads` (owner_user_id, title, archived)
  - `messages` (thread_id, role, `content` JSON holding `{type, ...}` parts)

- Approvals + tool runs
  - `action_requests` (thread_id, tool_name, args, risk, status)
  - `tool_runs` (thread_id, tool_name, args, status, result/error, optional `action_request_id`)

- WHM inventory
  - `whm_servers` (name, base_url, API auth, SSL verification)
  - WHM secrets at rest are application-encrypted in Postgres using `NOA_DB_SECRET_KEY`
  - Optional SSH settings are stored per WHM server (username, port, encrypted password/private key/passphrase, pinned host key fingerprint)

## Auth & RBAC

- Login: `POST /auth/login`
  - Bind with service account (`LDAP_BIND_DN`/`LDAP_BIND_PASSWORD`)
  - Search for user DN under `LDAP_BASE_DN` using `LDAP_USER_FILTER`
  - Bind as the user to validate password
  - Provision local user row on first successful login
  - New users default to `is_active=false` unless email is in `AUTH_BOOTSTRAP_ADMIN_EMAILS`

- Session
  - JWT is set by login as an httpOnly `noa_session` cookie; web app authenticates via cookie (no Bearer header)
  - User `is_active` is re-checked server-side per request (disable takes effect immediately)

- RBAC
  - Admin endpoints require `admin` role
  - Tool access is an allowlist; admin can assign tool allowlists per user (implemented via a dedicated role)

## Tool System & Safety

- Tool metadata is registered in a tool registry:
  - `name`, `description`, `risk: READ|CHANGE`, and `parameters_schema` (JSON Schema for LLM tool calling)

- Remote execution model:
  - SSH execution is implemented as a shared backend capability, not a generic end-user "run arbitrary command" tool
  - Purpose-built tools resolve an approved server record from the DB, decrypt its credentials server-side, and call the shared SSH executor with a normalized connection config
  - This keeps secrets out of tool arguments and lets future integrations (WHM, Proxmox, other server inventories) reuse one SSH layer with server-type-specific credential providers
  - Canonical per-integration operational references live in `docs/integrations/whm.md` and `docs/integrations/proxmox.md`

- Safety policy:
  - READ tools can execute immediately if the user is permitted.
  - CHANGE tools never execute directly from an LLM proposal.
    They only create an `action_request` after matching preflight evidence exists and the assistant has captured a clear user-provided reason for that specific change.
    If the reason is missing or ambiguous, the assistant stays in `waiting_on_user` and asks for an osTicket/reference number or a brief description before any approval request is created.

## Approval Gate (Two-Phase)

1) Proposal phase
   - LLM proposes a tool call
   - For `risk=CHANGE`, backend first requires matching preflight evidence plus a clear user-provided reason
   - If either is missing, the workflow remains `waiting_on_user` and the assistant asks for an osTicket/reference number or a brief description
   - Only then does the backend store `action_requests(status=pending)`
   - Backend emits a `request_approval` tool-call part, which renders an approval card in the UI

2) Decision + execution phase
  - User clicks Approve/Deny
  - Web sends a custom Assistant Transport command:
    - `approve-action { actionRequestId }`
    - `deny-action { actionRequestId }`
  - Backend validates:
    - user is active
    - request exists and is pending
    - request belongs to user + thread
    - user is authorized for the tool
  - If approved, backend executes the tool, records `tool_runs`, appends tool-call/tool-result parts, and writes audit events.

## Assistant Transport: Web <-> API

Endpoint:
- `POST /assistant`

Request body (simplified):
- `state`: previous client-visible state
- `commands`: e.g. `add-message`, `add-tool-result`, plus custom commands `approve-action` / `deny-action`
- `threadId`: the current thread remote id (required)

Streaming:
- Backend returns an `assistant-stream` data stream (state updates)
- Frontend uses assistant-ui’s `useAssistantTransportRuntime` to decode and update UI state.

Important implementation detail:
- A new thread may not have a `remoteId` yet.
  The web runtime ensures the thread is initialized before sending commands so `threadId` is always present.

## Streaming Behavior (MVP)

- Assistant narration is canonicalized server-side before persistence.
  - READ-oriented interactions usually persist one final assistant answer even if the model used multiple internal tool rounds.
  - Operational workflows persist milestone narration only, such as missing input, approval handoff, and terminal outcome.
  - Streamed placeholder text is provisional and may be replaced by a smaller canonical transcript after state refresh.

- The transport channel streams **state updates** to the UI.
- LLM token streaming is not implemented yet.
  The backend currently chunks the final assistant text into small deltas and streams those deltas.

## Thread List Persistence

The frontend uses `unstable_useRemoteThreadListRuntime` with a custom adapter (`apps/web/components/lib/thread-list-adapter.ts`) backed by API endpoints:

- `GET /threads`
- `POST /threads` (idempotent by `localId`)
- `PATCH /threads/{id}` (rename)
- `POST /threads/{id}/archive`
- `POST /threads/{id}/unarchive`
- `DELETE /threads/{id}`
- `GET /threads/{id}`
- `POST /threads/{id}/title`

## Auditability

The backend writes audit events for:
- auth events
- admin changes (user enable/disable, tool allowlist updates)
- action requested/approved/denied
- tool started/completed/failed

## WHM Validation + SSH Trust

- `POST /admin/whm/servers/{server_id}/validate` remains the single admin validation entry point
- Validation first checks the WHM API token using a lightweight WHM API call for WHM API-backed operations
- If SSH credentials are configured for that WHM server, validation then:
  - derives the SSH host from the WHM `base_url`
  - connects with the stored SSH credentials
  - captures the remote host key fingerprint
  - overwrites the pinned fingerprint on the WHM server record (TOFU refresh behavior)
  - runs a harmless SSH command to verify command execution
- CSF/firewall tools use the SSH execution path rather than the WHM API token path
- Normal SSH-backed tools require a pinned fingerprint to be present and fail closed if it is missing or does not match
- See `docs/integrations/whm.md` for the exact current admin endpoints, upstream WHM calls, and SSH commands used by the implementation

## What’s Next (Short List)

- Extend the shared SSH-backed server pattern to additional integrations (Proxmox/DNS/etc.) via new tool packages
- True LLM token streaming (server-side streaming completions)
- Org/tenant model, shared threads, and finer-grained RBAC
- Stronger UX around approval previews, diffs, and reversibility
- See `docs/integrations/proxmox.md` for the current implemented Proxmox surface area and backlog research
