# Claude UI A11y + Mobile Drawer Fixes

## Goals

- Restore keyboard accessibility parity with mouse interactions.
- Make the mobile thread list drawer a proper modal dialog (semantics, focus trap, escape-to-close, inert background, scroll lock).
- Keep styling aligned with the upstream Claude example; keep changes scoped to Claude UI components plus `apps/web` deps.

## Non-goals

- Visual redesign of the Claude UI.
- New features beyond the code-quality review items.

## Proposed Changes

### 1) Thread list focus visibility + hover-only controls

File: `apps/web/components/claude/claude-thread-list.tsx`

- Add `focus-visible` styling to `ThreadListItemPrimitive.Trigger` (ring/background) so keyboard focus is clearly visible.
- Make the delete control visible not only on hover but also when any descendant has focus (e.g. `group-focus-within:opacity-100`).

### 2) Mobile drawer accessibility via Radix Dialog

File: `apps/web/components/claude/claude-workspace.tsx`

- Replace the custom overlay/drawer with `@radix-ui/react-dialog`:
  - `Dialog.Root` controlled by local `open` state.
  - `Dialog.Overlay` for the dimmed backdrop.
  - `Dialog.Content` for the sliding drawer panel.
  - `Dialog.Close` for the close button.
- Preserve existing Tailwind classes so appearance and motion match current behavior.

Dependency:

- Add `@radix-ui/react-dialog` to `apps/web/package.json` and update `apps/web/package-lock.json` via `npm install`.

### 3) Mobile drawer UX: close on new chat / select thread

Files: `apps/web/components/claude/claude-thread-list.tsx`, `apps/web/components/claude/claude-workspace.tsx`

- Ensure `ThreadListPrimitive.New` invokes `onSelectThread` so the drawer closes after starting a new chat.
- Ensure selecting a thread item invokes `onSelectThread` (existing pattern), so the drawer closes after navigation.

### 4) Assistant message action bar visibility

File: `apps/web/components/claude/claude-thread.tsx`

- Adjust action bar classes so it is not permanently translated out of view.
- Reveal on hover and on keyboard focus within the message (`group-hover` + `group-focus-within`) while keeping the current layout.

### 5) Replace `AssistantIf` with `AuiIf`

File: `apps/web/components/claude/claude-thread.tsx`

- Replace `AssistantIf` usage with `AuiIf` (expected upstream component name) to align with current assistant-ui API.

### 6) Add aria-labels to icon-only controls

File: `apps/web/components/claude/claude-thread.tsx`

- Add `aria-label` to:
  - `ComposerPrimitive.Input`
  - `ComposerPrimitive.Send`
  - `ActionBarPrimitive.Copy`
  - Any other icon-only buttons (including disabled ones) that are missing a label.

## Verification

- Keyboard:
  - Tab through thread list items; focus ring visible.
  - Delete icon becomes visible when the item is focused.
  - Assistant message action bar reachable via keyboard focus.
- Mobile drawer:
  - Escape closes.
  - Focus stays trapped inside while open.
  - Background does not scroll while open.
  - Starting a new chat or selecting a thread closes the drawer.
- Build:
  - `npm run build` in `apps/web`.
