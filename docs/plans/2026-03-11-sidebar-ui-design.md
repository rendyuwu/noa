# Sidebar UI: Global Dark Theme Tokens + Sidebar Polish (Design)

## Problem

The web UI currently mixes two styling strategies:

- global Tailwind tokens (eg, `bg-bg`, `text-text`) backed by CSS variables in `apps/web/app/globals.css`, and
- many hard-coded hex colors (and `dark:` variants) scattered across the Claude-style assistant shell and adjacent UI.

This makes it difficult to implement cohesive theme changes (like a new global background) and leads to inconsistent hover/active states and spacing in the sidebar.

## Goal

- Change the app background to `hsl(30 3.3% 11.8%)` (`#1F1E1D`) across the entire web UI.
- Improve sidebar UX:
  - set desktop sidebar width to `18rem`,
  - add a clear active style for the selected thread,
  - add consistent hover styles for sidebar items,
  - reduce the left padding to the icon rail from ~28px to ~16px.
- Reduce future maintenance cost by removing hard-coded colors where practical and standardizing on reusable, semantic tokens/classes.

## Constraints

- Keep assistant-ui thread list primitives and rely on built-in active state (`data-active` / `aria-current`) rather than custom selection state.
- Keep the existing desktop sidebar + mobile drawer layout.
- Maintain accessibility: sufficient contrast, keyboard focus-visible rings, and clear hover/active affordances.
- Avoid introducing a full theme system or toggle in this change; implement a single, cohesive dark theme.

## Chosen Approach

Adopt a token-first refactor:

1) Set a coherent dark palette once in `apps/web/app/globals.css` (CSS variables).
2) Replace hard-coded assistant/sidebar colors with semantic Tailwind tokens (`bg-bg`, `bg-surface`, `text-text`, `border-border`, etc.).
3) Implement sidebar spacing + interaction states (hover/active) using shared utility classes (either via Tailwind token classes in JSX or small reusable component-class helpers).
4) Verify with:
   - existing unit tests (`npm test`), and
   - `noa-playwright-smoke` run before + after (capture artifacts for comparison).

## Detailed Design

### 1) Global theme tokens

File: `apps/web/app/globals.css`

- Update `:root` CSS variables to a warm dark theme anchored by:
  - `--bg: 30 3.3% 11.8%;` (must match `#1F1E1D`)
- Provide matching dark values for the semantic surfaces:
  - `--surface`, `--surface-2`, `--border`, `--text`, `--muted`
- Keep `--accent` as the existing orange accent (or adjust slightly only if contrast requires it).

Background rendering:

- Replace the current light radial-gradient `background:` on `body` with a flat `background-color: hsl(var(--bg));`.
- If we want atmosphere later, reintroduce subtle dark glows as `background-image` (low alpha) without changing the base color.

Rationale: a single token source of truth prevents repeated hex edits and keeps the new background exact.

### 2) Remove hard-coded background usage

Update assistant surfaces to rely on tokens instead of `bg-[#F5F5F0]` / `dark:bg[...]`.

Primary targets:

- `apps/web/app/(app)/assistant/page.tsx`
- `apps/web/components/claude/claude-workspace.tsx`
- `apps/web/components/claude/claude-thread.tsx`
- `apps/web/components/claude/claude-thread-list.tsx`

Secondary targets (to keep the rest of the app readable under the new dark tokens):

- `apps/web/app/login/page.tsx`
- `apps/web/components/assistant-ui/markdown-text.tsx`
- `apps/web/components/claude/request-approval-tool-ui.tsx`

Implementation principle: replace hard-coded hex colors with semantic tokens wherever they map cleanly.

### 3) Sidebar width

File: `apps/web/components/claude/claude-workspace.tsx`

- Change the desktop grid column from `md:grid-cols-[320px_minmax(0,1fr)]` to `md:grid-cols-[18rem_minmax(0,1fr)]`.
- Adjust the mobile drawer width so it feels consistent with `18rem`, while retaining a viewport-based max width for small screens.

### 4) Active + hover states in the thread list

File: `apps/web/components/claude/claude-thread-list.tsx`

- Use `ThreadListItemPrimitive.Root` built-in active state:
  - apply styles using `data-[active]:...` and/or `aria-[current=page]:...` selectors.
- Restructure the thread list row so the root element owns:
  - padding,
  - hover background,
  - active background,
  - focus ring offset based on `bg-bg`.

Expected behavior:

- Hover: thread row background subtly lifts (`bg-surface-2` with appropriate alpha).
- Active: selected thread is visibly selected even without hover.

### 5) Sidebar item hover + icon rail padding

File: `apps/web/components/claude/claude-thread-list.tsx`

- Standardize sidebar horizontal padding to `px-4` (16px) for header, nav rows, section label, and thread rows.
- Remove nested `px-1` + outer `px-3` combinations that push icons to ~24-28px.
- Ensure nav rows (including disabled items) share the same hover fill language as threads.

### 6) Reusable sidebar styles

To reduce future copy/paste of long `className` strings, introduce a small, explicit set of reusable styles:

- Option A (preferred): add a few component classes under `@layer components` in `apps/web/app/globals.css` (eg, `.noa-sidebar-row`, `.noa-sidebar-icon`, `.noa-sidebar-row-active`).
- Option B: define shared className constants local to `apps/web/components/claude/claude-thread-list.tsx`.

Either way, the rule is: colors come from tokens, not hex.

### 7) Tests + verification

- Update unit tests that assert hard-coded background classes (eg, `apps/web/components/claude/claude-workspace.test.tsx`).
- Run before/after verification:
  - Before: run `noa-playwright-smoke` and capture a success screenshot on `/assistant`.
  - After: run `noa-playwright-smoke` again and capture the same screenshot.
  - Keep both artifact directories to visually confirm the new background + sidebar states.

## Non-Goals

- No theme toggle UI.
- No redesign of assistant layout beyond the requested sidebar width/spacing/states and token cleanup.
- No changes to backend behavior.

## Success Criteria

- The entire web UI uses a coherent dark theme with background base `#1F1E1D`.
- Desktop sidebar width is `18rem` and mobile drawer feels consistent.
- Selected thread is clearly active; thread and nav rows have consistent hover feedback.
- Sidebar icon rail aligns closer to 16px left padding.
- Hard-coded hex colors in the assistant/sidebar are substantially reduced in favor of tokens.

## Verification

- Web: `npm test` in `apps/web`.
- API (baseline): `uv run pytest -q` in `apps/api`.
- E2E: `noa-playwright-smoke` before + after with artifact paths recorded.
