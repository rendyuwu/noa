# Admin Users Page: Reuse Existing Sidebar with Default Collapse (Design)

## Problem

`/admin/users` currently renders only the users management UI. The request is to add the same existing Claude-style sidebar used in the assistant area, while defaulting that sidebar to collapsed when the users page opens.

## Goals

- Reuse the existing sidebar component (`ClaudeThreadList`) rather than creating a duplicate sidebar UI.
- On `/admin/users`, default desktop sidebar to collapsed.
- Preserve desktop + mobile sidebar behavior (desktop collapsible rail, mobile drawer).
- Keep current `UsersAdminPage` data/edit behavior unchanged.
- Add final UI verification with Playwright screenshots before declaring implementation complete.

## Non-Goals

- No redesign of `UsersAdminPage` table or slide-over editor behavior.
- No backend API changes.
- No change to existing sidebar visual styling language.

## Constraints and Considerations

- `ClaudeThreadList` is built on `ThreadListPrimitive` from `@assistant-ui/react` and requires runtime context from `NoaAssistantRuntimeProvider`.
- `/admin/users` does not include `ClaudeThread`, so an explicit page-level open-sidebar trigger is needed when desktop sidebar is collapsed.
- Reusing `ClaudeThreadList` on `/admin/users` means thread list runtime initialization/fetches also happen on that route; this is accepted in favor of one shared sidebar source.

## Chosen Approach

Use a shared admin shell wrapper that reuses `ClaudeThreadList` directly and controls layout/open state around arbitrary page content.

### Component Additions

- Add `apps/web/components/admin/admin-sidebar-shell.tsx`:
  - Renders desktop collapsible sidebar + mobile drawer.
  - Reuses `ClaudeThreadList` for both desktop and mobile sidebar surfaces.
  - Accepts page content via `children`.
  - Desktop initial state is collapsed (`desktopSidebarOpen = false`).
  - Includes an open-sidebar button for collapsed state.

### Route Wiring

- Update `apps/web/app/(admin)/admin/users/page.tsx`:
  - Wrap `UsersAdminPage` with `NoaAssistantRuntimeProvider`.
  - Wrap page UI with `AdminSidebarShell`.

## Behavior and Data Flow

### Desktop

- Initial load on `/admin/users`: sidebar is collapsed.
- Open action expands sidebar column and shows existing `ClaudeThreadList`.
- Close action collapses sidebar and returns full width to main content.

### Mobile

- Open action displays sidebar in a Radix drawer.
- Selecting a sidebar item closes the drawer.

### Sidebar Actions on `/admin/users`

- `New chat` and recent-thread selection navigate to `/assistant`.
- Existing nav links (including `Users`) continue working normally.

## Testing Strategy

### Unit/Integration

- Add or update web tests to confirm:
  - `/admin/users` sidebar starts collapsed on desktop.
  - Open button appears and expands sidebar.
  - Existing users page behavior continues to render correctly within the new shell.

### Playwright Final Gate (Required)

Before reporting completion, run Playwright verification and capture these 5 screenshots:

1. `01-admin-users-desktop-collapsed.png` (initial desktop collapsed state)
2. `02-admin-users-desktop-sidebar-open.png` (desktop sidebar opened)
3. `03-admin-users-drawer-open.png` (user editor drawer visible)
4. `04-admin-users-mobile-sidebar-open.png` (mobile sidebar drawer open)
5. `05-admin-users-to-assistant-nav.png` (sidebar action navigates to `/assistant`)

No completion notification should be sent unless this verification run succeeds and artifacts are captured.

## Risks

- Slight extra runtime/network work on `/admin/users` due to sidebar thread primitives.
- New shell can affect responsive layout if grid/drawer behavior diverges from assistant conventions.

## Success Criteria

- `/admin/users` uses the existing sidebar UI, not a duplicate implementation.
- Desktop sidebar is collapsed by default and can be opened/closed.
- Mobile drawer behavior remains functional.
- Users management UI continues to work unchanged.
- Playwright run passes and required screenshots are produced.
