# WHM Smoke Fixes Design

Date: 2026-03-14

## Context

Playwright smoke testing (local notes in `SMOKE_TEST_WHM_REVIEW.md`, not tracked) showed that some approved WHM CHANGE tools can fail in a way that breaks the `/assistant` SSE response.

Symptoms observed in the browser/backend:

- Browser: `ERR_INCOMPLETE_CHUNKED_ENCODING`
- Backend: `RuntimeError: Caught handled exception, but response already started.`

## Problem

`approve-action` commands are processed inside the `/assistant` streaming run callback. In `apps/api/src/noa_api/api/routes/assistant.py`, `AssistantService.approve_action()` currently:

- Persists a tool-call message and tool-result/audit records
- Then raises an `HTTPException` on failure (eg `detail="Approved action execution failed"`)

If the SSE response has already started, raising an HTTP exception aborts the stream and the frontend sees a transport error instead of a normal in-thread tool failure.

Separately, the LLM sometimes asks for textual confirmation instead of calling a WHM CHANGE tool (which would automatically produce an approval card via `request_approval`).

## Goals

- Approved CHANGE tool failures never break SSE transport; failures are represented as normal `tool-result` events (`isError: true`) plus a readable assistant follow-up.
- Add a best-effort safety net so unexpected exceptions during streaming do not terminate the SSE response.
- Nudge LLM behavior so CHANGE actions rely on the approval UI rather than textual confirmation.

## Non-goals

- Changing WHM tool semantics or adding new WHM tools.
- Changing the approval UI itself.

## Proposed changes

### 1) Make approved-action failures non-throwing

In `apps/api/src/noa_api/api/routes/assistant.py` `AssistantService.approve_action()`:

- Keep raising `HTTPException` for precondition failures before tool-run creation (missing/invalid ids, not found, denied, already decided).
- After starting a tool run, never raise on:
  - unknown tool definition
  - tool risk mismatch
  - tool execution exceptions

Instead, for these cases:

- Mark the tool run as failed in storage (`fail_tool_run`)
- Persist a `role="tool"` message containing a `tool-result` with `isError: true`
- Persist a `tool_failed` audit entry
- Return cleanly so the stream can continue and the agent can produce a follow-up turn

### 2) Catch-all safety net in the streaming callback

In `/assistant` `run_callback` (same file), wrap the agent-running portion in `try/except Exception` and on any exception:

- Set `controller.state["isRunning"] = False`
- Append a single assistant error message to the in-memory `controller.state["messages"]` (best effort)
- Flush state if the controller has a state manager

This prevents transport-level failures even if an unexpected error escapes tool execution.

### 3) Prompt guidance to prefer approval UI

In `apps/api/src/noa_api/core/config.py` `llm_system_prompt`, add explicit guidance:

- For WHM CHANGE tools: after preflight + gathering required args, call the CHANGE tool directly and rely on the approval card (`request_approval`) rather than asking the user to type "yes/confirm".

## Testing

- Add a unit test for `AssistantService.approve_action()` that simulates a failing approved CHANGE tool and asserts:
  - no exception is raised
  - a `tool-result` message with `isError: true` is persisted
  - a `tool_failed` audit is persisted
- Run: `uv run pytest -q` in `apps/api`
