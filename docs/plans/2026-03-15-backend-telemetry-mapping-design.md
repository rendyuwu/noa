# Backend Telemetry Mapping Design

Date: 2026-03-15

## Context

The audit lineage in `docs/reports/2026-03-14-error-handling-and-logging-audit.md` now records the route-level backend logging and `error_code` continuation work as complete across request handling, auth, assistant transport, admin, threads, and WHM admin flows.

The remaining backend observability gap is no longer field creation. The current structured log/event vocabulary is stable enough to start a backend telemetry design pass that maps those existing events and fields to traces, metrics, and any later external reporting without choosing a vendor yet.

This pass is intentionally docs-first. It defines how the current backend event vocabulary should be interpreted by future instrumentation so the next implementation step can be tracked cleanly and resumed without rediscovering event shape.

## Goal

- Map the now-stable backend event and field set to trace, metric, and external-reporting candidates.
- Keep the mapping anchored to existing structured logs rather than inventing a parallel telemetry taxonomy.
- Produce a resume-friendly handoff so the next session can move directly into implementation planning and later instrumentation work.

## Non-goals

- Picking or installing a telemetry vendor such as `OpenTelemetry`, `Sentry`, or another reporting platform.
- Changing backend implementation code, log event names, or structured field names in this pass.
- Reopening the completed route-slice logging and `error_code` work.
- Defining frontend telemetry in this pass.

## Approaches Considered

### 1) Map the current stable event vocabulary first (chosen)

Treat structured logs as the source inventory, group them into a few telemetry domains, and define how each domain should later feed traces, metrics, and external reporting.

Pros:

- keeps the next step easy to track
- reuses the field vocabulary already stabilized in tests and docs
- avoids coupling vendor choice to the design pass
- creates a clean handoff for an implementation plan

Cons:

- does not add runtime telemetry yet
- still requires a later instrumentation pass

### 2) Refresh the audit only

Update the audit to say telemetry revisit is active, but do not write a dedicated mapping design.

Pros:

- smallest docs change

Cons:

- leaves the next step underspecified
- makes implementation planning slower because the mapping still has to be invented later

### 3) Choose a vendor and design instrumentation immediately

Decide on a concrete backend telemetry stack now and define spans, metrics, exporters, and reporting behavior around that tool.

Pros:

- most concrete implementation direction

Cons:

- forces a platform choice before the team has reviewed the mapping itself
- adds avoidable churn if the vendor decision changes
- mixes design and implementation concerns too early

## Proposed Design

### 1) Treat structured logs as the canonical source

The current backend structured logs remain the source of truth for telemetry design. Future tracing, metrics, and external reporting should reuse the existing event names and stable fields instead of introducing a second naming model.

This keeps incident diagnosis aligned across logs and telemetry and avoids duplicating semantics in two different places.

### 2) Organize telemetry into four backend domains

The current event set is easiest to reason about when grouped into a small number of telemetry domains:

- `request lifecycle`
- `auth`
- `assistant orchestration`
- `admin and management`

These domains are broad enough to cover the current stable backend surface, but small enough that the next implementation plan can track instrumentation work by domain.

### 3) Stable field inventory

The current telemetry design should treat these fields as stable reuse candidates where they already exist:

- `request_id`
- `request_method`
- `request_path`
- `status_code`
- `duration_ms`
- `error_code`
- `error_type`
- `user_id`
- `user_email` for auth/admin observability where already logged
- `thread_id`
- `assistant_command_types`
- event-specific counts such as `thread_count`, `tool_count`, and `server_count`
- event-specific routing fields such as `target_user_id`, `server_id`, and `server_name`

Future instrumentation should reuse only the fields that are already emitted by the mapped event. It should not backfill missing values by guessing or by creating event-specific side channels.

### 4) Trace mapping

Traces should focus on request and operation flow rather than every individual business event.

Recommended trace mapping:

- `api_request_completed` defines the baseline HTTP span attributes for all API requests using `request_id`, `request_method`, `request_path`, `status_code`, and `duration_ms`.
- Auth flows should enrich request spans or child auth spans with outcome attributes from `auth_login_succeeded`, `auth_login_rejected`, `auth_current_user_resolved`, `auth_current_user_rejected`, and `auth_me_succeeded`.
- Assistant transport should produce the richest operation traces because it crosses validation, authorization, streaming, persistence, and fallback behavior. The current assistant failure events should map to child span events or span status updates keyed by `assistant_command_types`, `thread_id`, `user_id`, `status_code`, `error_code`, and `error_type`.
- Admin, threads, and WHM admin success/rejection events should usually decorate the active request span rather than creating a large set of separate spans.

Trace design principle: keep spans centered on request and assistant operation boundaries, while lower-cardinality route outcome events become span attributes or span events.

### 5) Metric mapping

Metrics should answer the operational questions that are hard to answer from logs alone: request rate, latency, failure rate, and high-signal rejection patterns.

Recommended metric candidates:

- Request counters and latency histograms from `api_request_completed`, keyed by `request_method`, normalized route template if later available, and `status_code`.
- Auth outcome counters from `auth_login_succeeded`, `auth_login_rejected`, `auth_current_user_rejected`, and `auth_me_succeeded`, keyed by `error_code` or `failure_stage` where already present.
- Assistant failure counters from `assistant_run_failed_agent`, `assistant_error_message_persist_failed`, and `assistant_state_refresh_failed`, keyed by `error_code` or `error_type` plus a bounded assistant command classification.
- Admin/threads/WHM management counters from the existing success and conflict/not-found events, keyed by event name and status family rather than high-cardinality entity IDs.

Metric design principle: favor counters and latency histograms with bounded dimensions; do not use free-form IDs such as `request_id`, `thread_id`, `user_id`, `server_name`, or raw paths as metric labels.

### 6) External reporting mapping

External reporting should stay selective and focus on the events most likely to represent actionable failures, not routine user or admin activity.

Recommended external-reporting candidates:

- `api_unhandled_exception`
- unexpected `assistant_run_failed_agent` paths that emit `error_type`
- `assistant_error_message_persist_failed`
- `assistant_state_refresh_failed`
- authentication-service availability failures already represented by stable auth `error_code` values

Routine validation failures, known authorization denials, and expected business conflicts should remain in logs and metrics unless later operational experience proves they need issue-level reporting.

External-reporting design principle: report exceptions and degraded-system signals, not expected product-state outcomes.

### 7) Domain-to-telemetry matrix

| Domain | Current stable events | Trace use | Metric use | External reporting |
| --- | --- | --- | --- | --- |
| Request lifecycle | `api_request_completed`, `api_unhandled_exception` | request span baseline and final status | request rate and latency | unhandled exceptions only |
| Auth | `auth_login_succeeded`, `auth_login_rejected`, `auth_current_user_resolved`, `auth_current_user_rejected`, `auth_me_succeeded` | span attributes or child auth events | login/rejection outcome counters | only service-availability or unexpected failures |
| Assistant orchestration | `assistant_run_failed_agent`, `assistant_error_message_persist_failed`, `assistant_state_refresh_failed`, existing assistant success logs | child spans or span events inside assistant request flow | assistant failure counters and outcome metrics | yes, for unexpected and degraded assistant failures |
| Admin and management | admin, threads, and WHM success/conflict/not-found events | request span decoration | route outcome counters | generally no, unless later severity data changes |

### 8) Deferred until instrumentation

This design intentionally leaves these decisions for the next step:

- concrete telemetry SDK or exporter choice
- route-template normalization strategy for request metrics
- exact span hierarchy in the assistant path
- sampling policy
- alert thresholds and dashboard layout

Those decisions belong in the implementation plan and later instrumentation work, after this mapping is accepted.

## Error Handling and Safety

- Preserve the current rule that raw exception text stays out of user-visible contracts and persisted tool results.
- Keep `error_code` as the primary stable failure classifier where available.
- Treat `error_type` as internal diagnostic metadata for unexpected failures, not as a public contract.
- Avoid high-cardinality metrics by keeping identifiers in traces and logs, not metric labels.

## Tracking and Resume Point

This design is meant to be easy to continue from. The next step should not revisit event naming. It should execute the paired implementation plan so that it:

1. chooses a backend instrumentation seam without changing the mapped event vocabulary,
2. defines how request, auth, assistant, and admin/management domains are instrumented incrementally, and
3. leaves vendor-specific export or reporting decisions isolated behind a small integration boundary.

Primary resume point for the next session:

- review this mapping against the current backend event set
- execute `docs/plans/2026-03-15-backend-telemetry-mapping-implementation-plan.md`
- refresh the audit handoff after implementation so it records verification results and any remaining vendor/export follow-up
