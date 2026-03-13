# Tool Activity: Errors Only Design

Date: 2026-03-13

## Decision

Do not render tool activity in the transcript for normal operation.

- Show tool UI only when a tool call fails/errors (`incomplete` / `isError`).
- Do not show tool UI while tools are running.
- Do not show tool UI on success.
- Keep the approval UI (`request_approval`) visible (otherwise users cannot approve).

## Motivation

Tool rows add noise and compete with the Claude-style typography and palette.
Users only need tool visibility when something went wrong or requires human action.

## Scope

Web-only presentation change.

- Backend tool loop and persistence are unchanged.
- Approval card UI is unchanged.

## Implementation Overview

File: `apps/web/components/claude/request-approval-tool-ui.tsx`

- `ClaudeToolFallback`:
  - Compute `statusType` and ensure `isError === true` forces `statusType = "incomplete"`.
  - Return `null` unless `statusType === "incomplete"`.
  - Render the existing compact one-line row for failures.
- Remove success-linger/fade timers (no longer needed).

Tests

File: `apps/web/components/claude/request-approval-tool-ui.test.tsx`

- Assert successful complete renders nothing.
- Assert running/unknown status renders nothing.
- Assert incomplete/error renders the one-line row.
