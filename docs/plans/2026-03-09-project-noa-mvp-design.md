# Project NOA (MVP) - Design

Date: 2026-03-09
Status: Approved

## 1) Vision

Project NOA is an AI operations workspace: a ChatGPT-like interface where the real value is a controlled tool layer that can read from and safely change infrastructure systems via natural language.

The model is the interpreter and planner. The platform is the controller of actions.

## 2) MVP Goals / Non-Goals

Goals (MVP):
- Chat UI with streaming assistant responses.
- Multi-user support with LDAP auth (no registration).
- RBAC with an allowlist of tools per user (admin-managed).
- Tool execution with explicit approval gates for any modifying action.
- Clear action history and audit log.
- Thread list (multiple conversations) with persistence.
- A small demo toolset (read-only date/time; plus one safe "change" demo tool to validate approvals).

Non-goals (MVP):
- Deep production integrations (WHM/Proxmox/etc.).
- Fully resumable runs via Assistant Cloud.
- Complex multi-agent orchestration.

## 3) Architecture (Monorepo)

Monorepo layout:
- apps/web: Next.js + assistant-ui components.
- apps/api: Python FastAPI "agent server" implementing assistant-ui Assistant Transport streaming.

Key choices:
- Frontend uses assistant-ui primitives/components (Thread, ThreadList, ToolGroup/ToolFallback).
- Frontend runtime uses Assistant Transport (`useAssistantTransportRuntime`) to talk to the backend.
- Backend uses `assistant-stream` protocol to stream state updates.
- Backend is authoritative for thread state; the client-provided `state` field is treated as a hint only.

## 4) Authentication & Session

Auth source: LDAP only.
- Login flow: bind service account -> search user DN -> bind as user. (Mirrors `noa-old` LDAP patterns.)
- First successful login creates a local user record.
- New users default to `is_active=false` (pending admin approval), except bootstrap admins.

Bootstrap admin:
- `BOOTSTRAP_ADMIN_EMAILS` marks initial admin(s) as active and assigns the `admin` role.

Session:
- JWT access token (sent via Authorization header or httpOnly cookie).
- Token contains identity (user_id/email). Effective permissions are resolved server-side per request so RBAC changes apply immediately.

## 5) RBAC Model

Core entities:
- User: LDAP identity + local flags (active/disabled).
- Role: named role.
- RoleToolPermission: allowlist of tool names per role.

Admin capabilities:
- Enable/disable users.
- Assign tool permissions.

Per-user tool allowlist:
- Implemented by creating/maintaining a dedicated role for a user (e.g. `user:<uuid>`) and assigning that role, keeping a single permission model.

## 6) Tools & Safety

Tool registry:
- Each tool has metadata: `tool_name`, `description`, `integration`, `risk`.

Risk levels:
- READ: safe info requests. Executes immediately when authorized.
- CHANGE: creates/updates/deletes something. Never executes without explicit per-action approval.

Hard rule:
- For any CHANGE tool call, the system must stop and request explicit approval for that specific action instance.

## 7) Approval Gate (Recommended)

Two-phase execution:
1) Proposal phase: assistant proposes a CHANGE action; backend creates an `action_request` (status=pending) and logs `action_requested`.
2) Execution phase: user explicitly approves or denies in the UI.
   - Approve sends a custom Assistant Transport command `approve-action` with `action_request_id`.
   - Backend re-validates user active status + RBAC + tool risk, then executes tool.
   - Logs `action_approved`/`action_denied` and tool run events.

No implicit approvals.
No approval reuse across actions.

## 8) Persistence (Postgres)

Tables (MVP):
- auth/rbac/audit: `users`, `roles`, `user_roles`, `role_tool_permissions`, `audit_log`.
- threads: `threads` (owner_user_id, title, archived flag, timestamps).
- messages: `messages` (thread_id, role, parts JSON, timestamps).
- approvals: `action_requests` (thread_id, tool_name, args JSON, risk, status, requested_by, decided_by, timestamps).
- tool execution: `tool_runs` (thread_id, tool_name, args, status, result/error, action_request_id nullable).

Ownership:
- Threads are private to their owner in MVP.
- Admin sees user management + audit logs (thread visibility can be extended later).

## 9) API Surface (apps/api)

Auth:
- POST /auth/login
- POST /auth/logout
- GET  /auth/me

Threads (used by assistant-ui Custom Thread List adapter):
- GET    /threads
- POST   /threads
- PATCH  /threads/{id}            (rename)
- POST   /threads/{id}/archive
- POST   /threads/{id}/unarchive
- DELETE /threads/{id}
- POST   /threads/{id}/title      (optional title generation)

Admin:
- GET   /admin/users
- PATCH /admin/users/{id}         (enable/disable)
- GET   /admin/tools
- PUT   /admin/users/{id}/tools   (set tool allowlist; implemented via a per-user role)

Assistant Transport:
- POST /assistant
  - Accepts `threadId`, `commands`, and a `state` snapshot.
  - Streams state updates using assistant-stream operations.

## 10) Web UI (apps/web)

Core screens:
- Login: LDAP email/password.
- Assistant: Thread list sidebar + Thread view.
- Admin: manage users (active/disabled) and tool permissions.

assistant-ui integration:
- Use `unstable_useRemoteThreadListRuntime` for thread persistence with our backend.
- Each thread uses `useAssistantTransportRuntime` with `threadId=remoteId`.
- Use `ToolGroup` and `ToolFallback` to render tool calls and results.
- Custom tool UI for approval cards (Approve/Deny) that dispatch custom transport commands.

## 11) Demo Tools (MVP)

READ:
- get_current_time
- get_current_date

CHANGE (safe demo):
- set_demo_flag (writes a row in DB) to validate approval + audit + RBAC end-to-end.

## 12) Observability & Audit

Audit events:
- login_success/login_failed/login_denied_pending
- tool_denied
- action_requested/action_approved/action_denied
- tool_started/tool_completed/tool_failed

All events include request/thread ids where available.

## 13) Testing & Verification

Backend:
- Unit tests for LDAP auth wrapper (mock LDAP), RBAC checks, and approval gate logic.
- Integration tests for assistant endpoint command handling.

Frontend:
- Basic smoke tests for login + thread list + thread rendering.

## 14) Future Extensions

- Real integrations (WHM/Proxmox/DNS/Monitoring/Billing).
- More granular risk levels and reversible actions.
- Team/multi-tenant org model.
- Assistant Cloud resumability or equivalent sync server.
