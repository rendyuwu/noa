#+#+#+#+#+#+#+#+#+#+#+#+############################################################
# Users Admin: Sidebar + /admin/users + User Enablement/Tools (Design)
#+#+#+#+#+#+#+#+#+#+#+#+############################################################

## Problem

- The assistant sidebar currently exposes an "Admin" link and a disabled "Customize" item.
- Admin user management exists at `/admin`, but the UI is a card list with a comma-separated input.
- We want a future-proof admin route structure that can grow beyond user management.

## Goals

- Sidebar:
  - Replace the disabled "Customize" item with an enabled "Users" item.
  - Remove the "Admin" link/button in the sidebar footer.
  - Keep styling consistent by reusing existing token classes (e.g. `bg-bg`, `bg-surface`, `text-text`, `border-border`).
  - Update the icon to a user-appropriate icon (Radix icon, same visual weight/size as other sidebar icons).
- Admin UI:
  - Move the user management UI to `/admin/users`.
  - Keep `/admin` working by redirecting to `/admin/users`.
  - Users page shows a table of users.
  - Clicking a user opens a right slide-over panel (mobile: full-screen dialog) to:
    - enable/disable the user
    - set the user's authorized tools via selectable UI (not a comma-separated textbox)
- Account lifecycle:
  - On first successful login, create a DB user entry.
  - New users default to disabled; an admin must enable them manually.
- Error handling:
  - API returns stable, actionable error messages.
  - Frontend shows clear, non-technical messages and handles common states (401, 403, 404, 409, network failures).

## Constraints / Existing Behavior

- Backend already creates a user record on first login and defaults to disabled (`users.is_active` defaults to `false`).
- Bootstrap admins (configured by env var) are created/kept active and assigned the `admin` role.
- Admin APIs exist under `/admin/*` and are protected by:
  - active account
  - `admin` role
- Tool authorization is role-based with a per-user allowlist implemented via a synthetic role name `user:{user_id}`.

## Approaches Considered

- Option A: Keep UI at `/admin` and just redesign the page.
  - Pros: least routing churn.
  - Cons: less future-proof.
- Option B (chosen): Put user management at `/admin/users` and make `/admin` a redirect.
  - Pros: future-proof namespace for additional admin sections.
  - Cons: minor routing + link updates.

## Chosen Design

### Routes

- Web:
  - `/admin` -> redirect to `/admin/users`
  - `/admin/users` -> Users management UI

### Sidebar

- In the assistant sidebar nav:
  - Replace disabled "Customize" with an enabled "Users" link.
  - Link destination: `/admin/users`.
  - Visibility: admin-only.
- Remove the footer "Admin" link.
- Icon:
  - Replace the gear icon with a person/id-card style Radix icon at `16x16`.
- Styling:
  - Use existing token classes already in the sidebar (no new hard-coded colors).

### Users Page UX

#### Table

- Columns:
  - User (display name + email)
  - Status (Enabled/Disabled)
  - Roles
  - Tools (count or short list)

#### Slide-over editor

- Opens when clicking a user row.
- Shows:
  - user identity (name/email)
  - enabled toggle (with guardrails for last-active-admin handled by API)
  - tool allowlist multi-select:
    - tool list fetched from `/api/admin/tools`
    - selectable checkboxes with a small search filter
    - save button persists via `/api/admin/users/{id}/tools`
- Save states:
  - show loading/disabled states
  - show inline error banner in the panel when a save fails

### API Surface (no new endpoints required)

- `GET /admin/users` -> list users
- `PATCH /admin/users/{id}` -> enable/disable user
- `GET /admin/tools` -> list registered tools
- `PUT /admin/users/{id}/tools` -> set per-user tool allowlist

### Error Handling

#### API

- Keep `HTTPException.detail` strings stable and specific (existing messages reused):
  - `401`: "Missing bearer token" / "Invalid token" / "Invalid credentials"
  - `403`: "User pending approval" (auth) and "Admin access required" (admin)
  - `404`: "User not found"
  - `409`: last-admin/self-disable conflicts
  - `400`: unknown tools

#### Frontend

- Use `fetchWithAuth()` + `jsonOrThrow()` for all `/api/*` calls.
- Map common statuses to friendly text:
  - 403 (admin pages): "You don't have access to this page." + show Logout button
  - 409 (disable): show the API message (e.g. last admin) in an inline banner
  - network failures: "Unable to reach API"
- Avoid color drift: reuse existing alert styles already used in login/admin.

## Success Criteria

- Sidebar shows "Users" (admin-only) with a user-related icon; "Admin" footer link is removed.
- `/admin` redirects to `/admin/users`.
- `/admin/users` shows a user table; clicking a user opens a right slide-over to enable/disable and set authorized tools using selectable controls.
- New users default disabled; admins enable manually.
- Errors are handled cleanly in both API and UI.

## Verification

- Web: existing unit tests updated for sidebar changes.
- API: run existing pytest suite.
- E2E: run `noa-playwright-smoke` after implementation; do not report completion if the smoke run fails.
