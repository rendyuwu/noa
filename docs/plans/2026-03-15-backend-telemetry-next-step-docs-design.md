# Backend Telemetry Next-Step Docs Design

Date: 2026-03-15

## Context

The audit lineage in `docs/reports/2026-03-14-error-handling-and-logging-audit.md` now records multiple completed backend follow-up passes for request IDs, centralized error shaping, route-level structured logging, request-validation `error_code` coverage, and assistant/auth/non-assistant backend seam extraction.

That progress changes the follow-up priority. The audit should now state more clearly that the main remaining backend recommendation is to revisit telemetry after the current structured log and event field set stabilizes.

## Goal

- Refresh the audit wording so backend telemetry reconsideration is the main next step.
- Keep the recommendation explicitly deferred until the current structured log/event field vocabulary is stable enough to evaluate traces, metrics, or external reporting.
- Preserve the framing that any later backend logging or `error_code` work is a separate deeper helper-level or shared-catalog follow-up, not a return to the completed route-slice work.

## Non-goals

- Changing any implementation code, tests, or verification commands.
- Rewriting the broader audit lineage or historical handoff sections.
- Updating unrelated handoff docs.

## Proposed Doc Change

Update only the active recommendation language in `docs/reports/2026-03-14-error-handling-and-logging-audit.md`:

1. Strengthen the recommendation sentence near the completed-status bullets so it says backend telemetry revisit is now the main next step.
2. Reword the three-item active next-step list so item 1 is the primary telemetry revisit, while items 2 and 3 stay clearly secondary helper/shared follow-up work.

## Rationale

- The current route-slice logging and `error_code` follow-up called out by the audit is already complete.
- A telemetry decision is more useful after the field set settles, because it avoids designing traces or metrics around event names and fields that are still moving.
- Any additional backend logging or `error_code` expansion should happen later as deeper helper/service or shared-catalog work, which is a different scope from the completed route-level continuation.
