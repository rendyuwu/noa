# Tools Permissions + Admin Users UX Design

Date: 2026-03-13

## Context

NOA currently supports a small tool catalog (MVP tools) with a risk model:

- READ tools run immediately.
- CHANGE tools require an explicit approval step (`request_approval`).

Admins can manage per-user tool allowlists in the Admin Users page.
However:

- Admins currently bypass tool permissions (admin = all tools).
- The Admin Users page sidebar is collapsed by default on desktop.
- The Users table lacks operational fields (e.g., last login) and status nuance.
- When a tool is denied/unavailable, the user feedback is too generic.

## Goals

- Admin pages: sidebar is open by default on desktop, so the Users nav is visible immediately.
- Admin Users table: show `Created` and `Last login` and provide clearer user status labels.
- Tools: default disabled for everyone (including admin) until explicitly allowlisted.
- Tool permission denial: provide explicit, user-facing guidance to contact "SimondayCE Team" for enablement.

## Non-Goals

- A self-service "request tool access" workflow.
- A role-permissions management UI (only per-user allowlist remains in scope).
- Changing tool approval semantics (CHANGE tools still require approval).
- Broad UI redesign outside the admin sidebar default state and users table details.

## Current State (Relevant)

- Admin pages use `AdminSidebarShell`, which defaults to desktop sidebar closed.
- Admin Users UI is `apps/web/components/admin/users-admin-page.tsx`.
- Tool allowlisting persists via role `user:{userId}` tool permissions.
- Authorization currently allows any user with role `admin` to use any tool.
- User DB model includes `users.last_login_at`, but it is not currently updated on login.

## Proposed Changes

### 1) Admin Sidebar Default Open

Change `AdminSidebarShell` desktop default from closed to open.

- Desktop (`>= md`): sidebar starts open.
- Mobile: behavior stays overlay/closed-by-default (unchanged).

Implementation target:

- `apps/web/components/admin/admin-sidebar-shell.tsx`

### 2) Admin Users Table: More Detail

Add two columns and improve status semantics.

New columns:

- `Created`: derived from `users.created_at`
- `Last login`: derived from `users.last_login_at` (or "Never")

Status labels:

- `Active`: `is_active == true`
- `Pending approval`: `is_active == false` AND `last_login_at is null`
- `Disabled`: `is_active == false` AND `last_login_at is not null`

Backend work:

- Update the login flow to set `last_login_at = now()` when authentication succeeds.
- Extend the `/admin/users` response to include `created_at` and `last_login_at`.

Frontend work:

- Render the two new columns.
- Keep existing columns (Email / Status / Roles / Tools).

Implementation targets:

- `apps/api/src/noa_api/core/auth/auth_service.py`
- `apps/api/src/noa_api/api/routes/admin.py`
- `apps/api/src/noa_api/core/auth/authorization.py`
- `apps/web/components/admin/users-admin-page.tsx`

### 3) Tools Default Disabled (Including Admin)

Change tool authorization so that admins do not implicitly have access to all tools.

Rules:

- If user is inactive: deny all tools.
- If user is active: allow tools only if granted via role tool permissions (including per-user role `user:{id}`).

This makes the initial state "no tools" unless an allowlist is set.

Implementation target:

- `apps/api/src/noa_api/core/auth/authorization.py` (`authorize_tool_access`)

### 4) Explicit Permission Feedback When Tool Is Denied

When the assistant attempts to call a tool that is not available for the user, return an explicit message that:

- States the tool is not permitted.
- Tells the user to contact "SimondayCE Team" to enable access.

Example message (exact wording can be tuned, but must include the team name):

"You don't have permission to use tool '<tool>'. Please ask SimondayCE Team to enable tool access for your account."

Implementation target:

- `apps/api/src/noa_api/core/agent/runner.py` (tool-call deny/unavailable branch)

## Data Model / Migrations

- No schema changes are intended (the `users.last_login_at` column already exists in the SQLAlchemy model).
- Verify Alembic migrations already include this column; if not, add a migration to create it.

## Security & Compliance Notes

- Removing the admin bypass ensures tool access is explicitly granted and auditable per-user.
- CHANGE tools continue to require approval via the existing approval gate.
- The denial message should avoid exposing sensitive internal details beyond the tool name.

## Testing & Verification

- Web tests:
  - Update `AdminSidebarShell` tests to reflect default-open desktop behavior.
  - Update Users Admin page tests to validate new columns and status labels.

- API tests:
  - Add/adjust tests to ensure tool access for admins is denied unless allowlisted.
  - Add a test that a successful login sets `last_login_at`.

## Rollout Notes

- After this change, existing admins will not have tool access until they are allowlisted.
- Ensure at least one admin can still access the Admin Users page to configure tool allowlists.
