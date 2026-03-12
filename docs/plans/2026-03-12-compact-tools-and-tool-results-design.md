# Compact Tools UI + Tool-Result Loop Design

Date: 2026-03-12

## Problem

1) Tool UI is too heavy.

- The Claude-style renderer currently wraps tool invocations in a visible "Tool activity" card and each tool renders as a bordered, collapsible block.
- Desired: a clean, compact presentation (single-line rows) and, for successful tools, the UI should disappear after the assistant finishes responding.

2) Tool execution does not feed results back to the LLM.

- Today, when a user asks for date/time, the backend runs the LLM once, executes the tool, persists a tool result, and stops.
- The LLM never receives the tool output as input for a follow-up turn, so it cannot take the next action (ex: answer with the date, chain another tool call, or explain the result).

Reference behavior from `noa-old`:

- Tool lifecycle events and operator-facing output redact raw tool-call args/results.
- Internally, the agent still receives tool outputs to continue reasoning.

## Goals

- Tool results are provided back to the LLM so it can produce the next assistant message.
- The web UI shows a minimal tool activity affordance:
  - While running: show a single-line activity row (no card).
  - Requires approval: show the existing approval card.
  - Error: keep a visible single-line row (optionally expandable later).
  - Success: hide after completion (do not clutter transcript).
- Do not expose raw tool args/results JSON in the transcript UI (except the structured approval card UI).
- Keep the persisted tool-call / tool-result message model compatible with assistant transport.

## Non-goals

- Changing `@assistant-ui/react` internals.
- Building per-tool rich UI beyond the existing approval card.
- Reworking persistence/encryption/redaction policies for tool result payloads (MVP keeps current storage behavior).

## Current System (What Exists)

Backend

- `apps/api/src/noa_api/core/agent/runner.py`:
  - Calls the LLM once.
  - Executes READ tools and persists:
    - assistant `tool-call` message
    - tool `tool-result` message
  - For CHANGE tools, persists:
    - internal proposal `tool-call` (`toolCallId` starts with `proposal-`)
    - approval `tool-call` (`request_approval`)
  - Does not call the LLM again after tool execution.

- `apps/api/src/noa_api/api/routes/assistant.py`:
  - Runs the agent only when there is a user `add-message` command.
  - Does not run the agent after `approve-action` or `add-tool-result` commands.

- `OpenAICompatibleLLMClient` only serializes text parts; it currently ignores tool-call and tool-result parts when building OpenAI messages.

Web

- `apps/web/components/lib/assistant-transport-converter.ts`:
  - Normalizes tool calls (`argsText` always present).
  - Merges persisted tool results onto the matching tool-call part by `toolCallId`.
  - Drops proposal tool calls (`proposal-*`).

- `apps/web/components/claude/request-approval-tool-ui.tsx`:
  - Renders a visible tool group card (`ClaudeToolGroup`).
  - Renders tool fallback as a bordered `<details>` block (`ClaudeToolFallback`).

## Proposed Approach (Recommended)

### A) Proper tool loop in the backend

Implement an LLM/tool loop:

1) Call LLM with tools.
2) If tool calls are returned:
   - Execute each READ tool.
   - Persist tool-call + tool-result messages.
   - Append those messages to the conversation context.
   - Call LLM again, now including tool results.
3) If a CHANGE tool is requested:
   - Persist approval request tool-call(s).
   - Stop the loop until user approval/denial.
4) Stop when the LLM returns no tool calls (final assistant text) or safety limits are hit.

Key requirement: the OpenAI client must serialize tool calls/results into OpenAI-compatible message shapes so the model actually receives tool outputs.

Safety:

- Add a hard cap on tool rounds (ex: 4) and total tool calls (ex: 8) to prevent infinite loops.

### B) Compact tool UI that hides on success

UI behavior:

- Tool activity renders as an inline list of one-line rows, not a card.
- Successful tool invocations render nothing after completion.
- Failures remain visible as a one-line row.
- Approval remains a dedicated card (`request_approval`).

## Design Details

### Backend: LLM + Tools Loop

Files

- `apps/api/src/noa_api/core/agent/runner.py`
- `apps/api/src/noa_api/api/routes/assistant.py`

#### 1) Conversation context passed to the LLM

For OpenAI-compatible models, include:

- Text parts as today.
- Assistant tool calls as OpenAI `tool_calls` entries (use `toolCallId` as the tool call id).
- Tool results as `role: "tool"` messages with `tool_call_id` and JSON-stringified result as content.

This ensures the model can condition its next action on tool output.

#### 2) Loop semantics

- A single user action can produce:
  - assistant text (optional)
  - one or more tool-call / tool-result message pairs
  - a final assistant text response that uses tool outputs

- CHANGE tools short-circuit:
  - Persist proposal + approval tool-call messages.
  - Do not execute the tool.
  - Do not continue LLM turns until user approves.

#### 3) Running agent after approvals / external tool results

In `apps/api/src/noa_api/api/routes/assistant.py`, update `should_run_agent` to run the agent when commands include:

- user `add-message`
- `approve-action`
- `add-tool-result`

Rationale: after approval completes (and the tool result is persisted) the agent should be invoked to interpret the result and continue.

### Web: Tool UI Compaction

Files

- `apps/web/components/claude/request-approval-tool-ui.tsx`

#### 1) Replace "Tool activity" card with minimal container

- `ClaudeToolGroup` becomes a lightweight wrapper (no border/header).
- If all children are `null` (because tools succeeded and are hidden), render nothing.

#### 2) Hide successful tools

In `ClaudeToolFallback`:

- Determine status from existing `status` + `result` + `isError`.
- If status is complete AND `isError` is false/undefined AND tool is not `request_approval`, return `null`.
- Otherwise render a single-line row:
  - left: humanized tool label ("Current time")
  - middle: short activity text ("Checking..." / "Waiting for approval" / "Failed")
  - right: small status badge

Keep existing `RequestApprovalToolUI` unchanged.

## Testing Strategy

Backend

- Add unit tests for `AgentRunner` loop:
  - When the first LLM turn requests a READ tool, the runner executes it and then calls the LLM again with the tool result included.
  - The final assistant message includes content derived from tool output.
  - Safety cap prevents infinite loops.

- Add route tests ensuring the agent runs after:
  - `approve-action`
  - `add-tool-result`

Web

- Add render tests for `ClaudeToolFallback`:
  - success renders nothing
  - running renders one-line row
  - requires-action renders one-line row (approval card is separate)
  - error renders one-line row

## Risks / Mitigations

- Risk: infinite tool loops when the model repeats calls.
  - Mitigation: max rounds + max tool calls; emit a final assistant error message when exceeded.

- Risk: tool args/results leak into UI.
  - Mitigation: UI never renders `argsText`/`result`; keep approval card as the only structured tool UI.

- Risk: approvals/tool-results do not trigger follow-up assistant output.
  - Mitigation: run the agent on `approve-action` and `add-tool-result` commands.
