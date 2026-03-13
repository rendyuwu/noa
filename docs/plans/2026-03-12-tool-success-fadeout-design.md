# Tool Success Linger + Fade-Out Design

Date: 2026-03-12

## Problem

Successful tool activity rows disappear immediately in the Claude UI.

- Current behavior: `ClaudeToolFallback` returns `null` as soon as a tool is `complete` and not an error.
- Desired behavior: keep successful rows visible briefly, then fade/collapse smoothly before unmounting.

## Goals

- Successful tool activity rows linger for 1 second so users can notice completion.
- Successful rows fade/collapse smoothly (no abrupt layout jump).
- After fade completes, the row unmounts so the transcript remains clean.
- Non-success rows remain visible:
  - `running`
  - `requires-action`
  - `incomplete` / errors

## Non-goals

- Adding an animation library.
- Changing tool semantics or tool-call persistence.
- Changing approval card UI.

## Proposed Approach

Implement a small client-side state machine inside `ClaudeToolFallback`:

States

- `visible` (default)
- `exiting` (during fade/collapse)
- `hidden` (unmounted)

Timing

- Linger: 1000ms after the tool becomes `complete`.
- Transition: 200ms fade/collapse.

Behavior

- When `statusType === "complete" && !isError`:
  - Render the row.
  - Start timer for 1000ms.
  - Set `exiting` and apply transition classes.
  - After 200ms, set `hidden` and return `null`.
- If tool status changes away from `complete`, cancel timers and return to `visible`.
- If tool is `request_approval` (custom UI) do nothing here; it already uses a dedicated UI.

## UI / CSS

Use Tailwind transitions on an outer wrapper to avoid jumpy layout:

- `overflow-hidden`
- animate `opacity` and `max-height`

Example classes

- Visible: `opacity-100 max-h-20`
- Exiting: `opacity-0 max-h-0`
- Common: `transition-all duration-200 ease-out`

## Testing

- Update `apps/web/components/claude/request-approval-tool-ui.test.tsx`:
  - Use Vitest fake timers.
  - Assert a successful tool row is present at `t=0`.
  - After `t=1000ms` it should still be in DOM but have exiting classes (optional).
  - After `t=1200ms` (linger + transition) it should be unmounted.
