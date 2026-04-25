# Backend Observability Baseline

Date: 2026-03-16

## Scope

- Backend telemetry/exporter wiring is already complete.
- This baseline defines what to watch operationally without changing backend event names or choosing a dashboard vendor.
- Event names and metric series in this doc must stay aligned with the existing backend telemetry vocabulary.

## Dashboard Groups

### API health

- Metrics: `api.requests.total`, `api.request.duration_ms`
- Trace/report events: `api_request_completed`, `api_unhandled_exception`
- Break down by bounded fields already emitted: `request_method`, `status_code` (note: trace events use raw `request_path`; only metrics normalize the route path)
- Watch for: traffic drops, 5xx share, latency regression, unhandled exception bursts

### Auth

- Metric: `auth.outcomes.total`
- Trace/report events: `auth_login_succeeded`, `auth_login_rejected`, `auth_current_user_resolved`, `auth_current_user_rejected`, `auth_me_succeeded`
- Break down by bounded fields already emitted: `event_name`, `status_code`, `error_code`, `failure_stage`
- Watch for: login rejection mix, bearer-token rejection spikes, `authentication_service_unavailable`

### Assistant

- Metric: `assistant.failures.total`
- Trace/report events: `assistant_run_failed_pre_agent`, `assistant_run_failed_agent`, `assistant_error_message_persist_failed`, `assistant_state_refresh_failed`
- Break down by bounded fields already emitted: `assistant_command_types`, `status_code`, `error_code`, `error_type`
- Watch for: unexpected failure spikes, degraded fallback persistence/state-refresh failures, concentration by command type

### Admin and management

- Operational grouping only: this groups existing admin, threads, WHM, and Proxmox telemetry without inventing new event names.
- Metrics: `admin.outcomes.total`, `threads.outcomes.total`, `whm.outcomes.total`, `proxmox.outcomes.total`
- Trace events to watch as examples:
  - Admin: `admin_access_denied`, `admin_users_list_succeeded`, `admin_user_status_updated`, `admin_direct_tool_grants_disabled`, `admin_role_tools_updated`, `admin_last_active_admin_conflict`, `admin_self_deactivate_conflict`, `admin_user_not_found`, `admin_unknown_tools`
  - Threads: `threads_list_succeeded`, `thread_created`, `thread_reused`, `thread_retrieved`, `thread_title_updated`, `thread_archived`, `thread_unarchived`, `thread_deleted`, `thread_not_found`, `thread_title_generated`, `thread_title_returned_existing`, `thread_title_returned_refreshed` (inactive-user denial recorded as `auth_current_user_rejected` via auth telemetry)
  - WHM: `whm_servers_list_succeeded`, `whm_server_created`, `whm_server_updated`, `whm_server_deleted`, `whm_server_validated`, `whm_server_not_found`, `whm_server_name_conflict` (admin access denial recorded as `admin_access_denied` via shared admin guard)
  - Proxmox: `proxmox_servers_list_succeeded`, `proxmox_server_created`, `proxmox_server_updated`, `proxmox_server_deleted`, `proxmox_server_validated`, `proxmox_server_not_found`, `proxmox_server_name_conflict`
- Watch for: unexpected changes in outcome mix, conflict/not-found spikes after deploys, WHM validation failure concentration by `validation_ok`

## Alert Baseline

Keep the first alert set intentionally small and high-signal.

The canonical initial alert baseline is exactly the alerts listed in the table below.

| Alert | Primary signal | Why it matters |
| --- | --- | --- |
| Sustained API 5xx increase | `api.requests.total` filtered to 5xx | Signals broad backend instability |
| Sustained API latency regression | `api.request.duration_ms` on core routes | Catches degraded user experience before total failure |
| Unhandled exception burst | `api_unhandled_exception` | Signals backend instability even when 5xx volume is still emerging or route-localized |
| Auth service availability failures | `auth.outcomes.total` with `error_code=authentication_service_unavailable` | High-signal dependency issue that blocks login |
| Unexpected/degraded assistant failure spike | `assistant.failures.total`, especially with `error_type` present or repeated `assistant_error_message_persist_failed` / `assistant_state_refresh_failed` | Captures assistant incidents that are not expected product-state failures |

## Dashboard-Only vs Alert-Worthy

Dashboard-only at first:

- `request_validation_error`
- `invalid_credentials`, `missing_bearer_token`, `invalid_token`
- `user_pending_approval`
- expected 404/409/400 admin, threads, and WHM outcomes
- normal thread/admin/WHM success counters and low-volume single-event anomalies
- isolated assistant HTTP rejections with known `error_code` and no sustained spike

Alert-worthy at first:

- repeated or sustained 5xx-backed API degradation
- unhandled exception bursts
- repeated auth dependency availability failures
- repeated assistant degraded or unexpected failures

The rule is simple: expected product-state outcomes stay on dashboards; system-health regressions page.

## Rollout and Validation Notes

- Realize this baseline in any OTLP-compatible dashboard tool, but keep repo docs as the source of truth.
- Reuse existing event names and metric series exactly; do not rename events in dashboard queries.
- Keep identifiers such as `request_id`, `user_id`, `thread_id`, `server_id`, and `server_name` out of alert dimensions even if they remain useful in logs and traces.
- Validate each query against the current event inventory before rollout:
  - API: `api_request_completed`, `api_unhandled_exception`, `api.requests.total`, `api.request.duration_ms`
  - Auth: `auth_login_succeeded`, `auth_login_rejected`, `auth_current_user_resolved`, `auth_current_user_rejected`, `auth_me_succeeded`, `auth.outcomes.total`
  - Assistant: `assistant_run_failed_pre_agent`, `assistant_run_failed_agent`, `assistant_error_message_persist_failed`, `assistant_state_refresh_failed`, `assistant.failures.total`
  - Admin/management: `admin.outcomes.total`, `threads.outcomes.total`, `whm.outcomes.total`, `proxmox.outcomes.total` plus the route trace events listed above
- Roll out dashboards before paging alerts, then tune thresholds from observed baseline behavior instead of guessing from docs alone.
