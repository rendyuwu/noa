# WHM Tools Stabilization (JSON-Safe Tool Results) Design

Date: 2026-03-13

## Context

During WHM tool smoke testing, calling `whm_list_servers` via the assistant transport caused the `/api/assistant` request to fail mid-stream.

Root cause observed in API logs:

- Tool results were persisted to Postgres JSONB (`tool_runs.result`) and also emitted in tool-result message parts.
- `whm_list_servers` returned WHM server objects containing `created_at` / `updated_at` as `datetime` instances.
- SQLAlchemy JSONB (and the runner JSON encoding path) cannot serialize `datetime` by default, triggering:
  - `TypeError: Object of type datetime is not JSON serializable`
  - followed by `sqlalchemy.exc.PendingRollbackError`, cascading into broken assistant streaming.

We want a fix that prevents this entire class of issues (not only WHM servers), since other tools can return non-JSON-native values (`UUID`, `Decimal`, `Enum`, sets, etc.).

## Goals

- Prevent assistant transport crashes when any tool returns non-JSON-native values.
- Ensure anything persisted to JSONB (`tool_runs.args`, `tool_runs.result`, action request args) is JSON-serializable.
- Keep tool output structure stable from a product perspective (only normalize types).

## Non-Goals

- Changing tool semantics or payload schemas beyond type normalization.
- Solving WHM CSF availability issues (e.g. CSF plugin returning HTTP 404).

## Proposed Approach: JSON-Safety Boundary (Defense-in-Depth)

### 1) Add a JSON-safety utility

Introduce a small helper that converts arbitrary Python values to JSON-serializable equivalents.

Behavior (recursive):

- `datetime` / `date` -> ISO 8601 string
- `UUID` -> string
- `Enum` / `StrEnum` -> `.value`
- `set` / `tuple` -> list
- `dict` / `list` -> recursively sanitize children
- Other objects -> `str(value)` (last-resort to avoid runtime crashes)

### 2) Apply sanitization at the tool execution boundary

In `apps/api/src/noa_api/core/agent/runner.py`:

- After a tool executes, sanitize the tool `result` exactly once.
- Use the sanitized result for:
  - `complete_tool_run(..., result=...)` (JSONB persistence)
  - emitting the `tool-result` message part (messages JSONB persistence)

This prevents the observed crash and keeps the system robust even if individual tool implementations return rich Python objects.

### 3) Apply sanitization at persistence boundaries

In `apps/api/src/noa_api/storage/postgres/action_tool_runs.py`:

- Sanitize args/results right before writing to the DB models:
  - `ActionRequest.args`
  - `ToolRun.args`
  - `ToolRun.result`

This is a second layer of protection in case any code path bypasses the agent runner.

### 4) (Optional) Sanitize tool-results before LLM JSON encoding

In `apps/api/src/noa_api/core/agent/runner.py` `_to_openai_chat_messages`:

- Tool result parts are encoded via `json.dumps(rendered_result)`.
- If we sanitize at execution + persistence boundaries, this should already be safe.
- We may still choose to defensively sanitize right before `json.dumps`.

## Testing & Verification

- Add a regression test that triggers the previously failing path:
  - Execute a tool that returns `datetime` values (or use `WHMServer.to_safe_dict` output) and ensure:
    - tool run completion persists successfully
    - no serialization exceptions are raised

- Manual verification:
  - Run the assistant UI flow that calls `whm_list_servers`.
  - Confirm no stream interruption, no 500 on `/api/assistant`.
  - Confirm `tool_runs.result` contains string timestamps.

## Rollout Notes

- This is a safe change with broad upside: it prevents crashes without widening tool permissions.
- After stabilization, we can separately evaluate CSF plugin availability and error handling.
