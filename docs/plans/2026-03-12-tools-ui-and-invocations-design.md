# Tools: Invocation Stability + Claude Tool Activity UI Design

## Problem

Typing a prompt that triggers a tool call (ex: "What time now?") crashes the web UI with:

`Uncaught TypeError: Cannot read properties of undefined (reading 'startsWith')` in
`useToolInvocations.ts`.

Root cause:

- The backend emits tool calls as message parts with `args` but without `argsText`.
- The web app (`apps/web/components/lib/runtime-provider.tsx`) converts persisted state into
  assistant-ui `ThreadMessage` objects, but drops `argsText` entirely when it is missing.
- `@assistant-ui/react`'s Assistant Transport tool invocation logic assumes `content.argsText` is a
  string and calls `content.argsText.startsWith(...)`.

Separately, tool results are persisted as standalone messages (role `tool`). Our current adapter
turns those into standalone assistant messages with synthetic tool-call parts, causing duplicated
tool UI and leaking raw JSON (args/result) in the Claude-style UI fallback.

## Goals

- No crashes when tool-call parts are present in the transport state.
- Claude-style tool activity: compact blocks that explain what tool is doing.
- Do not show raw tool `argsText` or raw `result` JSON in the chat UI (except the existing
  approval card).
- Keep approval flow (`request_approval`) working and visually consistent.

## Non-goals

- Changing `@assistant-ui/react` implementation in `node_modules`.
- Reworking the backend persistence format for tools (beyond what is required for stability).
- Building a full per-tool rich UI; we start with a generic "tool activity" renderer.

## Proposed Approach (Recommended)

### 1) Normalize tool-call parts in the web adapter

Location: `apps/web/components/lib/runtime-provider.tsx`

Changes:

- Ensure every converted `tool-call` part always includes:
  - `toolCallId: string`
  - `toolName: string`
  - `args: object` (at least `{}`)
  - `argsText: string` (stable JSON string, default `{}`)

Rationale:

- This matches assistant-ui expectations and prevents `.startsWith` on `undefined`.
- A stable `argsText` also avoids the "argsText can only be appended" invariant in the tool
  invocation stream when the state updates.

### 2) Merge standalone tool results onto their matching tool-call

Location: `apps/web/components/lib/runtime-provider.tsx`

Changes:

- When the persisted state contains a standalone `tool` role message with a `tool-result` part,
  attach `{ result, isError, artifact }` onto the existing tool-call part with the same
  `toolCallId`.
- Do not emit a separate assistant message for that tool-result message.

Rationale:

- UI shows a single tool activity item per invocation.
- We avoid duplicated tool blocks and reduce noisy tool output in the transcript.

### 3) Hide internal proposal tool calls

Location: `apps/web/components/lib/runtime-provider.tsx`

Changes:

- Drop tool-call parts with `toolCallId` starting with `proposal-`.

Rationale:

- The user-visible action is the approval request card (`request_approval`). Proposal tool calls are
  internal plumbing and clutter the UI.

### 4) Claude-style compact tool activity UI (no raw JSON)

Location: `apps/web/components/claude/request-approval-tool-ui.tsx`

Changes:

- Update `ClaudeToolFallback` to render a compact, collapsible tool activity block.
- Do not render `argsText` or `result` bodies.
- Show:
  - tool name (humanized)
  - status badge
  - a plain-English line describing what the tool is doing/did
- Auto-expand on error or when the tool requires action (optional).

Copy strategy:

- Map known tools to clear present-tense / past-tense activity strings:
  - `get_current_time`: "Checking the current time" / "Checked the current time"
  - `get_current_date`: "Checking today's date" / "Checked today's date"
  - `set_demo_flag`: "Requesting a change" / "Change request completed" (details are handled by
    approval UI)
- Unknown tools fall back to "Using <toolName>".

Notes:

- `request_approval` keeps its dedicated card UI (`RequestApprovalToolUI`) and remains the only
  place where we show structured details (tool name + buttons).

## Testing

Web (Vitest):

- Add unit tests for the state-to-ThreadMessage conversion to ensure:
  - tool-call parts always include `argsText: string`.
  - tool-result messages merge into their matching tool-call and do not emit extra messages.
  - proposal tool calls are removed.
- Add a lightweight render test for `ClaudeToolFallback` asserting:
  - it renders a human-friendly activity line.
  - it does not render raw JSON args/result.

Manual smoke:

- In `/assistant`, ask "What time now?" and verify:
  - no console crash
  - a compact tool activity block appears
  - assistant response includes the time

## Risks / Mitigations

- Risk: unstable `JSON.stringify(args)` ordering could violate the append-only invariant.
  - Mitigation: produce `argsText` once from the persisted `args` object (stable), and avoid
    re-stringifying from reshaped objects; default to `{}` when missing.

- Risk: merging tool results could mis-associate results if IDs collide.
  - Mitigation: merge only when `toolCallId` matches exactly and appears earlier in the message
    stream.
