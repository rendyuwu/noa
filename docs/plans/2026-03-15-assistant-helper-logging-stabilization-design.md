# Assistant Helper Logging Stabilization Design

Date: 2026-03-15

## Context

The backend logging and error-handling audit in `docs/reports/2026-03-14-error-handling-and-logging-audit.md` now records the route-level continuation work as complete across assistant, auth, admin, threads, and WHM admin flows.

That leaves backend telemetry as the main deferred follow-up, but the audit also says telemetry should wait until the current structured log and event field set stabilizes.

The remaining instability is no longer in route-slice coverage. It is in a narrow assistant helper seam: `apps/api/src/noa_api/api/routes/assistant_operations.py` still owns several important failure-path events that are emitted in production code but are not yet pinned with the same structured-log expectations used for auth and non-assistant route events.

## Goal

- Stabilize the remaining assistant helper failure-event vocabulary that is most likely to feed any later backend telemetry design.
- Keep this pass intentionally narrow and helper-level.
- Reassess telemetry readiness after the helper failure-event vocabulary is test-pinned.

## Non-goals

- Reopening the completed route-level logging slice in `admin.py`, `threads.py`, `whm_admin.py`, `auth.py`, or the top-level assistant transport route.
- Adding a telemetry vendor or tracing/metrics dependency.
- Broad helper-level logging expansion outside the assistant failure seam.
- Renaming established route event names unless a test proves the current helper vocabulary is unsafe or misleading.

## Readiness Decision

Telemetry revisit is not ready yet.

Why:

1. The route-level field set is mostly stable and already pinned in focused tests.
2. The assistant helper failure seam is still only partially pinned, especially for rendered structured fields.
3. Starting traces, metrics, or external reporting now would risk encoding helper event names and fields that are still implicitly defined.

## Approaches Considered

### 1) Pin the assistant helper failure vocabulary first (chosen)

Add focused tests for the remaining assistant helper failure events in `assistant_operations.py`, keep implementation changes minimal, and use the result as the final readiness gate before a telemetry design pass.

Pros:

- smallest change that directly addresses the remaining instability
- matches the audit's helper-level follow-up framing
- avoids reopening route logging that is already complete
- creates a concrete field inventory for the next telemetry decision

Cons:

- does not itself add telemetry
- may require tiny field-shape adjustments if tests expose drift

### 2) Do a docs-only telemetry inventory now

Write a telemetry design or field-catalog doc immediately, based on the current code and tests, without first tightening helper-level assertions.

Pros:

- fastest way to move discussion forward
- no code changes required

Cons:

- risks documenting helper events that are not actually stable yet
- weakens the audit's explicit "wait for stabilization" guidance

### 3) Start telemetry instrumentation now and refine later

Begin adding tracing or metrics hooks now, assuming the current helper event vocabulary is good enough.

Pros:

- immediate telemetry progress

Cons:

- highest churn risk
- directly conflicts with the audit's defer-until-stable recommendation
- likely to couple telemetry shape to still-evolving helper details

## Proposed Design

### 1) Stabilize the assistant helper failure events

Focus on these events in `apps/api/src/noa_api/api/routes/assistant_operations.py`:

- `assistant_run_failed_agent`
- `assistant_error_message_persist_failed`
- `assistant_state_refresh_failed`

The pass should verify that these events continue to use the current helper context model and safe failure metadata without introducing new telemetry abstractions.

### 2) Prefer tests first, then the smallest code change

Add focused tests that capture the helper failure paths and assert the event names plus the stable fields expected to matter later:

- `assistant_command_types` where available
- `thread_id`
- `user_id`
- `status_code` and `error_code` for translated HTTP failures
- `error_type` for unexpected exceptions when that is the existing behavior

If the current implementation already matches the desired contract, the production code change should be zero or near-zero.

### 3) Keep the field vocabulary narrow

This pass should not expand the assistant helper vocabulary beyond what the code is already emitting. The goal is to freeze the currently useful fields, not to design a larger taxonomy.

Expected stable helper failure vocabulary after this pass:

- `request_id` where the top-level assistant route already includes it
- `assistant_command_types`
- `thread_id`
- `user_id`
- `status_code`
- `error_code`
- `error_type`

### 4) Reassess telemetry after the helper seam is pinned

Once these helper failure events are covered by focused tests, do a fresh readiness check against the audit recommendation before any telemetry design or vendor work starts.

## Module Shape

Target files for this pass:

- `apps/api/src/noa_api/api/routes/assistant_operations.py`
  - keep helper failure logging behavior explicit and stable
- `apps/api/tests/test_assistant.py`
  - extend route-facing helper failure coverage where the top-level route log contract matters
- `apps/api/tests/test_assistant_operations.py`
  - add direct helper-level structured log assertions for agent failure and fallback failure paths

## Error Handling

- Preserve existing HTTP response contracts and user-visible fallback behavior.
- Do not surface raw internal exception text to users.
- Keep internal logging safe and structured.
- Preserve `asyncio.CancelledError` handling exactly as-is.

## Testing Strategy

- Add direct helper tests for assistant helper failure logs in `apps/api/tests/test_assistant_operations.py`.
- Reuse existing structured-log capture patterns instead of introducing a new logging test helper unless the reuse becomes noisy.
- Run the focused assistant helper and route suites first.
- Run the full backend test suite and Ruff after the focused pass is green.

## Acceptance Criteria

- The remaining assistant helper failure events are explicitly pinned by tests.
- Any needed implementation changes stay local to the assistant helper seam.
- Route-level logging and `error_code` behavior remain unchanged.
- The repo is in a better position to make a backend telemetry decision without guessing at helper event shape.
