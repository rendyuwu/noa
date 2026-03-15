# Assistant Route Decomposition Continuation Design

Date: 2026-03-15

## Context

The backend error-handling and logging audit in `docs/reports/2026-03-14-error-handling-and-logging-audit.md` has already driven several backend-only passes:

- request-scoped IDs and centralized API error shaping
- shared Postgres engine and session accessors
- assistant helper extraction for commands, streaming, repository access, tool execution, and assistant-specific ID parsing
- wider backend `error_code` coverage and initial structured logging context binding

The remaining high-value gap is the continued decomposition of `apps/api/src/noa_api/api/routes/assistant.py`.

That route is materially smaller than before, but it still owns too much orchestration logic. In particular, it still mixes transport concerns with assistant-domain operation flow, service-level failure mapping, and some logging-context plumbing. The next pass should keep the external contract stable while further shrinking the route into a thinner transport boundary.

## Goals

- Reduce the remaining orchestration responsibility inside `apps/api/src/noa_api/api/routes/assistant.py`.
- Move assistant-domain operation flow and service-to-HTTP normalization behind assistant-focused helper modules.
- Preserve all current assistant transport contracts, including `detail`, `error_code`, request/response shape, and SSE resilience behavior.
- Improve maintainability so future assistant logging and error-code work has cleaner seams.

## Non-goals

- Replacing the assistant transport route with a completely new package architecture.
- Changing user-visible assistant error prose or stable `error_code` values.
- Adding a telemetry vendor or broad repo-wide logging changes in this pass.
- Reworking already-extracted helper modules unless needed to support the new orchestration seam.

## Approaches Considered

### 1) Route-only cleanup

Trim helpers and reorder logic within `apps/api/src/noa_api/api/routes/assistant.py` without introducing a new orchestration module.

Pros:

- lowest implementation risk
- minimal file churn

Cons:

- leaves the core route coupling in place
- does not create a clean seam for future assistant changes

### 2) Incremental assistant split (chosen)

Keep the route as the transport boundary, but extract the remaining orchestration and service-level error normalization into one or two assistant-focused helper modules.

Pros:

- best fit for the current branch state
- reduces route responsibility without a disruptive rewrite
- preserves current contracts while making tests more focused

Cons:

- still leaves some future decomposition work for later
- touches both route and helper-level tests

### 3) Larger assistant application-layer redesign

Introduce a fuller assistant application/service layer now and move most route logic behind it in one pass.

Pros:

- cleanest long-term structure

Cons:

- higher churn and review risk
- too large for the current incremental continuation goal

## Proposed Design

### 1) Keep the route as a thin transport boundary

`apps/api/src/noa_api/api/routes/assistant.py` should remain responsible for:

- request and response models
- dependency wiring
- top-level request validation that is inherently transport-specific
- deciding what happens before SSE startup versus after streaming begins
- the final route-facing exception boundary

The route should stop owning service-level orchestration details that can live behind assistant-specific helpers.

### 2) Extract the remaining orchestration seam

Add a new assistant-focused helper module, likely `apps/api/src/noa_api/api/routes/assistant_operations.py` or a similarly named file, to own the remaining pre-stream and operation-level coordination.

That helper layer should:

- load or verify assistant thread/run context when the behavior is assistant-domain rather than purely transport-specific
- coordinate command application and other assistant operations before the route starts SSE
- raise assistant-domain exceptions or return plain operation results instead of constructing `HTTPException` directly
- centralize service/repository failure normalization so `assistant.py` does not repeat route-shaped error mapping

The already-extracted modules (`assistant_commands.py`, `assistant_streaming.py`, `assistant_repository.py`, `assistant_tool_execution.py`, and `assistant_errors.py`) should remain the building blocks for this pass.

### 3) Preserve the pre-stream versus in-stream failure split

The current boundary is good and should stay intact:

- pre-stream failures should still return shaped HTTP JSON errors with stable `detail`, `error_code`, and `request_id`
- once SSE begins, agent-phase failures should still be contained within the streaming callback and converted into a safe fallback assistant message
- `asyncio.CancelledError` should continue to be re-raised rather than swallowed

This pass is structural. It should not broaden the set of failures that degrade in-stream, and it should not turn current pre-stream HTTP failures into streamed fallbacks.

### 4) Move assistant-domain errors farther away from FastAPI

The next seam should continue the pattern introduced by `assistant_errors.py`:

- helper and orchestration code raises assistant-domain exceptions or returns explicit assistant-domain results
- a single thin translation boundary near the route turns those into `ApiHTTPException`
- existing `detail` strings and `error_code` values remain unchanged

This keeps domain logic decoupled from FastAPI and reduces the chance that route-local HTTP shaping leaks back into internal assistant helpers.

### 5) Bind logging context at the orchestration boundary

Use `log_context(...)` in the new orchestration layer where assistant identifiers are naturally available so both success and failure logs inherit stable context with less per-branch duplication.

The most important fields remain:

- `request_id`
- `user_id`
- `thread_id`
- `tool_name`
- `tool_run_id`
- `action_request_id`

This pass should not add many new log lines. It should primarily improve where context is bound so existing or narrowly added logs are more useful.

## Data Flow

1. The route parses the transport request and performs transport-specific validation.
2. Pre-stream assistant operations run through the new orchestration helper layer.
3. Assistant-domain failures are normalized there and translated into `ApiHTTPException` at one thin route-facing boundary.
4. If pre-stream work succeeds, the route starts SSE and hands off to a smaller streaming callback.
5. The streaming callback coordinates controller state, agent execution, and fallback behavior using existing streaming helpers rather than inline route-owned branching.

## Error Handling

- Keep all current assistant `detail` text and `error_code` values stable.
- Centralize the remaining pre-stream assistant failure mapping outside the route body.
- Preserve current stream resilience for non-cancellation failures.
- Continue logging in-stream failures with structured context and surfacing only safe fallback assistant output.

## Testing Strategy

- Add helper-level tests around the new orchestration seam instead of only growing route-level transport tests.
- Keep targeted route tests to verify transport behavior remains unchanged.
- Reuse the existing focused assistant test pattern so the new module can be exercised cheaply without needing full streaming coverage for every branch.
- Run the assistant-focused backend suite after the extraction to verify contracts and logging behavior still hold.

## Acceptance Criteria

- `apps/api/src/noa_api/api/routes/assistant.py` is materially smaller and closer to a transport coordinator.
- Pre-stream assistant failure mapping is more centralized and less route-local.
- Existing assistant HTTP and SSE contracts remain unchanged.
- The extracted seam is directly testable with focused helper-level tests.
- The assistant/backend verification suite remains green after the refactor.

## Recommended Next Step

Create a continuation implementation plan that breaks the work into incremental tasks: pin the current behavior with tests, extract the orchestration helper layer, rewire the route through the new seam, and run focused backend verification before deciding on commit or PR handoff.
