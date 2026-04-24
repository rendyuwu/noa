# SPEC.md — Project NOA

## §G Goal

NOA: operational assistant for hosting infrastructure. Monorepo (FastAPI backend + Next.js frontend). Authenticates operators via LDAP, enforces RBAC tool permissions, runs multi-round LLM agent with approval-gated CHANGE tools against WHM and Proxmox servers. Persists threads, messages, runs, action requests, receipts, workflow checklists. Admin panel manages users, roles, servers, audit log.

## §C Constraints

- C1. Python 3.11+, `uv` package manager, hatchling build.
- C2. Node.js 20+, Next.js 16, React 19, assistant-ui 0.12.24, Tailwind 4.
- C3. Postgres 16 via SQLAlchemy async + asyncpg. Alembic migrations.
- C4. LDAP authentication (dev bypass mode for local dev). JWT in httpOnly cookie.
- C5. OpenAI-compatible LLM endpoint (configurable model/key/base URL). No demo fallback.
- C6. Browser never calls FastAPI directly; same-origin `/api/*` proxy via Next.js route handlers.
- C7. Secrets (API tokens, SSH creds) encrypted at rest via Fernet (`NOA_DB_SECRET_KEY`).
- C8. All CHANGE tools require user-supplied `reason` and matching preflight evidence.
- C9. Env vars for list/set configs use JSON arrays.
- C10. No secrets in git (`.env*` gitignored except `.env.example`).
- C11. Caveman communication style per AGENTS.md.

## §I Interfaces

### I.api — FastAPI HTTP (apps/api)

| Route | Method | Purpose |
|---|---|---|
| `/health` | GET | Health check → `{"status":"ok"}` |
| `/auth/login` | POST | LDAP auth → JWT in httpOnly cookie |
| `/auth/logout` | POST | Clear session cookie |
| `/auth/me` | GET | Current user (cookie or Bearer) |
| `/threads` | GET/POST | List/create threads (owner-scoped) |
| `/threads/{id}` | GET/PATCH/DELETE | Get/rename/delete thread |
| `/threads/{id}/archive` | POST | Archive thread |
| `/threads/{id}/unarchive` | POST | Unarchive thread |
| `/threads/{id}/title` | POST | LLM-generated title |
| `/assistant` | POST | Transport endpoint (commands + state) |
| `/assistant/threads/{id}/state` | GET | Canonical thread state |
| `/assistant/runs/{id}/live` | GET(SSE) | Live run event stream |
| `/admin/users` | GET | List users (admin) |
| `/admin/users/{id}` | PATCH/DELETE | Enable/disable/delete user (admin) |
| `/admin/users/{id}/roles` | PUT | Replace user roles (admin) |
| `/admin/roles` | GET/POST | List/create roles (admin) |
| `/admin/roles/{id}` | DELETE | Delete role (admin) |
| `/admin/roles/{id}/tools` | PUT | Set tool permissions (admin) |
| `/admin/audit` | GET | Query audit log (admin) |
| `/admin/whm/servers` | GET/POST | List/create WHM servers (admin) |
| `/admin/whm/servers/{id}` | PATCH/DELETE | Update/delete WHM server (admin) |
| `/admin/whm/servers/{id}/validate` | POST | Validate WHM server (admin) |
| `/admin/proxmox/servers` | GET/POST | List/create Proxmox servers (admin) |
| `/admin/proxmox/servers/{id}` | PATCH/DELETE | Update/delete Proxmox server (admin) |
| `/admin/proxmox/servers/{id}/validate` | POST | Validate Proxmox server (admin) |

### I.web — Next.js Frontend (apps/web)

| Surface | Purpose |
|---|---|
| `/login` | Login form (email/password) |
| `/assistant/[[...threadId]]` | Chat workspace (thread list + thread + tool UIs) |
| `/admin/users` | User management |
| `/admin/roles` | Role management |
| `/admin/audit` | Audit log viewer |
| `/admin/audit/receipts/[id]` | Action receipt detail |
| `/admin/whm/servers` | WHM server management |
| `/admin/proxmox/servers` | Proxmox server management |
| `/api/[...path]` | Catch-all proxy → `NOA_API_URL` |
| `/api/assistant` | Assistant proxy (ack + SSE composition) |

### I.tools — Tool Registry

**Common (3):** `get_current_time` (READ), `get_current_date` (READ), `update_workflow_todo` (READ).

**WHM (15):** `whm_list_servers` (R), `whm_validate_server` (R), `whm_check_binary_exists` (R), `whm_mail_log_failed_auth_suspects` (R), `whm_list_accounts` (R), `whm_search_accounts` (R), `whm_preflight_account` (R), `whm_preflight_primary_domain_change` (R), `whm_preflight_firewall_entries` (R), `whm_suspend_account` (C), `whm_unsuspend_account` (C), `whm_change_contact_email` (C), `whm_change_primary_domain` (C), `whm_firewall_unblock` (C), `whm_firewall_allowlist_add_ttl` (C), `whm_firewall_allowlist_remove` (C), `whm_firewall_denylist_add_ttl` (C).

**Proxmox (13):** `proxmox_list_servers` (R), `proxmox_validate_server` (R), `proxmox_get_vm_status_current` (R), `proxmox_get_vm_config` (R), `proxmox_get_vm_pending` (R), `proxmox_get_user_by_email` (R), `proxmox_preflight_vm_cloudinit_password_reset` (R), `proxmox_preflight_move_vms_between_pools` (R), `proxmox_preflight_vm_nic_toggle` (R), `proxmox_reset_vm_cloudinit_password` (C), `proxmox_move_vms_between_pools` (C), `proxmox_disable_vm_nic` (C), `proxmox_enable_vm_nic` (C).

### I.db — Postgres Schema (14 tables)

`users`, `roles`, `user_roles`, `role_tool_permissions`, `audit_log`, `threads`, `messages`, `assistant_runs`, `workflow_todos`, `action_requests`, `tool_runs`, `action_receipts`, `whm_servers`, `proxmox_servers`, `login_rate_limits`.

### I.ext — External Systems

| System | Protocol | Auth |
|---|---|---|
| LDAP server | LDAP(S) bind+search | Service account + user bind |
| OpenAI-compatible LLM | HTTPS | API key |
| WHM servers | HTTPS (cPanel API) + SSH | API token + SSH key (encrypted) |
| Proxmox servers | HTTPS (Proxmox API) | API token ID + secret (encrypted) |
| OpenTelemetry collector | OTLP (optional) | — |
| Sentry (frontend) | HTTPS (optional) | DSN |

## §V Invariants

### Auth

- V1. New LDAP users auto-provisioned `is_active=False` (pending approval). Bootstrap admin emails auto-activated with `admin` role.
- V2. Login sets httpOnly `noa_session` cookie (SameSite=Lax, Path=/, max-age=3600). Logout clears with max-age=0. Logout idempotent without auth.
- V3. `/auth/me` accepts Bearer OR cookie. Cookie takes precedence when both present. Missing both → 401 `missing_authentication`. Invalid token → 401 `invalid_token`. Inactive user → 403 `user_pending_approval`.
- V4. Passwords and access tokens never appear in structured logs.
- V5. JWT secret required in production (auto-generated ≥32 chars in dev). Insecure LDAP transport (`ldap://`) rejected in production. `auth_dev_bypass_ldap=True` rejected in production.
- V6. Rate limiter blocks after configured max failures within window. Resets after window expires. Successful login clears buckets. Rate-limited → 429 with `Retry-After` header.
- V7. `get_auth_service` commits on pending-approval exception (user was created), rolls back on all other exceptions.
- V8. All error responses include `request_id` in body and `x-request-id` header.

### RBAC

- V9. Admin role bypasses tool permission checks for known tools but still rejects unknown/unregistered tools.
- V10. Disabled users (`is_active=False`) have zero permissions regardless of roles.
- V11. Cannot disable last active admin. Admin cannot self-deactivate. Admin self-delete → 409 `self_delete_admin`.
- V12. Non-admin users → 403 on admin endpoints. Role names with spaces rejected. `admin` role reserved (cannot edit tools or delete). Internal roles (`user:*`) cannot be assigned via API.
- V13. Admin changes produce audit events. Permission updates take effect immediately.
- V14. Direct tool grants disabled (410 `direct_tool_grants_disabled`). Role replacement preserves internal roles.

### Threads

- V15. Thread list owner-scoped; other users' threads → 404. Inactive users → 403 `user_pending_approval`.
- V16. Thread create with `localId` idempotent per user (same localId → same thread; 200 reuse vs 201 create).
- V17. Title > 255 chars → 422 `request_validation_error`. Title generation does not overwrite manually-set title. Race: stored title wins.
- V18. Delete → 204; subsequent GET → 404. Archive/unarchive toggles `is_archived` and `status`.

### Assistant Transport

- V19. Missing `threadId` → 422. Missing thread → 404 `thread_not_found`. No-op request → JSON ack with `runStatus=null`, `activeRunId=null`.
- V20. `add-message` with non-null `sourceId` → 400 `message_edit_not_supported`. Only `role=user` allowed. Client `system`/`tools` overrides ignored (logged as warning).
- V21. User message during active run → 409. Assistant messages allowed during active run (error persistence).
- V22. Thread state includes: `messages`, `workflow`, `pendingApprovals`, `actionRequests`, `isRunning`, `runStatus`, `activeRunId`, `waitingForApproval`, `lastErrorReason`. Active run metadata projected without `live_snapshot` leaking.

### Assistant Runs

- V23. `AssistantRunStatus` enum values stable: `STARTING`, `RUNNING`, `WAITING_APPROVAL`, `COMPLETED`, `FAILED`. DB enforces at most one active run per thread (partial unique index).
- V24. Terminal runs (`COMPLETED`/`FAILED`) cannot be reopened. Stale sessions cannot rewrite terminal runs.
- V25. Late SSE subscribers receive latest snapshot first, then deltas. Stored snapshots isolated from external mutation (deep copy). Queued events isolated from mutation.
- V26. `remove_run` prevents stale publish from reviving a run. Stale handle cannot publish into reused run ID. Wait timeout does not cancel detached run.
- V27. Live SSE route requires owner match (404 for non-owner). Streams `event: snapshot` / `event: delta`. Closes after terminal run or `WAITING_APPROVAL` transition.

### Approval Flow

- V28. `approve-action` reuses existing `WAITING_APPROVAL` run (no new run). `deny-action` completes waiting run and unblocks thread. `add-tool-result` keeps waiting run blocked.
- V29. Agent failure persists safe error message "Assistant run failed. Please try again." Streaming placeholder (`id=assistant-streaming`) removed from final state.
- V30. Failed run persists `FAILED` status with `last_error_reason`. When both error persistence and state refresh fail, local fallback appends error to controller state.

### Tools

- V31. All CHANGE tools require a `reason` parameter (shared schema). CHANGE action without `reason` → 409 `change_reason_required`.
- V32. READ tools execute immediately. CHANGE tools go through approval gate: create `action_request` (PENDING) → `request_approval` → user approves/denies → execute if approved.
- V33. Whitespace-only required strings rejected. Duplicate firewall targets rejected. Invalid server_ref (URL-like) rejected. Invalid WHM username (email-like) rejected. Non-positive vmids rejected. Duplicate vmids rejected.
- V34. Tool error sanitization: `RuntimeError` → `tool_execution_failed` (raw exception redacted from LM). `TimeoutError` → `timeout`. Original exception logged at ERROR but not exposed to LM.
- V35. Missing tool definition → tool run `FAILED` "Requested tool is unavailable". Risk mismatch → `FAILED` "Approved tool risk mismatch".
- V36. Tool result foreign thread → 404 `tool_call_not_found`. Stale (completed) tool run → 409 `tool_call_not_awaiting_result`.
- V37. Lifecycle enum values machine-stable: `READ`, `CHANGE`, `PENDING`, `APPROVED`, `DENIED`, `STARTED`, `COMPLETED`, `FAILED`.

### Preflight

- V38. WHM CHANGE tools without matching preflight evidence → `FAILED` with `preflight_required`. Matching preflight allows execution. Mismatched account in preflight → blocked.
- V39. All Proxmox CHANGE tools expose preflight guidance in description. Proxmox CHANGE tools have `workflow_family` metadata.

### Workflows

- V40. Workflow templates produce todos, reply templates, evidence templates, approval context. Approval context includes `activity`, `beforeState`, `evidenceSections`, `argumentSummary`, `replyTemplate`.
- V41. `update_workflow_todo`: only one item `in_progress` at a time. Invalid status rejected with valid status list. `waiting_on_user` and `waiting_on_approval` are valid blocked statuses. Empty list clears thread state.
- V42. 8 registered workflow families: 5 WHM (`whm-account-lifecycle`, `whm-account-contact-email`, `whm-account-primary-domain`, `whm-firewall-batch-change`) + 3 Proxmox (`proxmox-vm-cloudinit-password-reset`, `proxmox-pool-membership-move`, `proxmox-vm-nic-connectivity`).

### Agent

- V43. Multi-round tool-calling loop: max 6 rounds, max 8 tool calls per turn. Temperature 0, tool_choice "auto".
- V44. System prompt contains required policy lines: preflight-first, approval gates, no fabrication, argument discipline.

### Secrets & Crypto

- V45. `SecretCipher` round-trips encrypt/decrypt. Encrypted format `enc:v1:fernet:...`. `maybe_decrypt_text` passes plaintext unchanged.

### Infrastructure

- V46. `/health` → 200 `{"status":"ok"}`. CORS allows configured origins. `llm_api_key` required (None/whitespace → ValueError).
- V47. `json_safe` converts datetime, date, UUID, enum, sets to JSON-serializable types.
- V48. Auth metrics never include `user_email` or `user_id` (cardinality control). 503 auth failures reported as reporting candidates.

### Code Quality (added 2026-04-24 review)

- V49. Telemetry helpers (`_safe_trace`/`_safe_metric`/`_safe_report`) single shared implementation in `api/route_telemetry.py` or similar. No per-module copy-paste.
- V50. Admin guard (`_require_admin`) single shared implementation in `api/admin/guards.py`. WHM/Proxmox admin routes reuse it; no local copies.
- V51. `AssistantService` repository and runner typed via Protocol (not `Any`). No `getattr(self._repository, "method_name", None)` dispatch pattern.
- V52. `AssistantRunCoordinator` internals accessed only via public methods. No `getattr` on private `_tasks`/`_sequences` from outside the class.
- V53. WHM/Proxmox server secrets (api_token, ssh_password, ssh_private_key, api_token_secret) encrypted at rest via Fernet, same as tool args in `action_requests`/`tool_runs`.
- V54. `_require_active_user` dependency single shared implementation with consistent telemetry. Thread and assistant routes reuse `get_active_current_auth_user` from `auth_dependencies.py`.
- V55. `_decrypt_sensitive_args` only decrypts values whose key matches `is_sensitive_key()`, symmetric with `_encrypt_sensitive_args`. Non-sensitive strings never passed through `maybe_decrypt_text`.
- V56. `AuthPendingApprovalError` carries `error_code = "user_pending_approval"` attribute so `deps.py` commit-on-pending-approval path works correctly.
- V57. CORS origins validator handles JSON array strings (e.g. `["http://localhost:3000"]`) per C9, same as `llm_system_prompt_extra_paths`.
- V58. `delete_user` self-delete guard only fires when target user is admin. Non-admin self-delete → generic error, not "Admins cannot delete their own account".

## §T Tasks

| id | status | task | cites |
|---|---|---|---|
| T1 | x | Add ESLint config for apps/web | C2 |
| T2 | . | Add dedicated web test runner (Vitest configured but no lint scripts) | C2 |
| T3 | . | Migrate legacy `integrations/whm/` to `whm/integrations/` (refactoring-map.md) | I.tools |
| T4 | . | Implement OpenTelemetry backend observability (traces + metrics, currently NoOp default) | I.ext |
| T5 | . | ? Add Proxmox postflight verification for pool-move and NIC-toggle workflows | V39,V40 |
| T6 | . | ? Evaluate removing Bearer token auth path (cookie-only migration complete?) | V3,C4 |
| T7 | . | Extract shared telemetry helpers (`_safe_trace`/`_safe_metric`/`_safe_report`) to single module; remove 6+ copy-pasted versions across `error_handling.py`, `routes/auth.py`, `auth_dependencies.py`, `routes/whm_admin.py`, `routes/proxmox_admin.py`, `assistant/assistant_operations.py` | V49 |
| T8 | . | Consolidate `_require_admin` into `api/admin/guards.py`; remove duplicate implementations in `routes/whm_admin.py:288-316` and `routes/proxmox_admin.py:243-271` | V50 |
| T9 | . | Consolidate `_require_active_user` into `auth_dependencies.get_active_current_auth_user`; remove local copies in `routes/threads.py:89-108` and `routes/assistant.py:91-104` | V54 |
| T10 | . | Extract shared server name validation (`_validate_server_name`) and base URL normalization (`_normalize_*_base_url`) for WHM+Proxmox admin routes into shared module; also deduplicate `_status_family` | V49,V50 |
| T11 | . | Deduplicate `ensure_role`/`assign_role`/`get_role_names` between `SQLAuthRepository` (`auth_service.py:122-154`) and `SQLAuthorizationRepository` (`authorization_repository.py:146-178`); extract to shared base or mixin | V49 |
| T12 | . | Extract `AuthorizationUser` construction helper in `authorization_service.py`; replace ~8 repeated `AuthorizationUser(user_id=..., tools=[], direct_tools=[])` + `get_allowed_tool_names` + reconstruct patterns | V49 |
| T13 | . | Type `AssistantService` repository/runner via Protocol; remove all `getattr(self._repository, "method_name", None)` dispatch in `service.py:99-182` | V51,B5 |
| T14 | . | Add public query methods to `AssistantRunCoordinator` (`get_task_done`, `get_sequence`); remove `getattr` on `_tasks`/`_sequences` in `run_lifecycle.py:82-104` | V52 |
| T15 | . | Encrypt WHM server secrets (`api_token`, `ssh_password`, `ssh_private_key`, `ssh_private_key_passphrase`) and Proxmox server secrets (`api_token_secret`) at rest via Fernet | V53,C7 |
| T16 | . | Fix `_decrypt_sensitive_args` to only decrypt values where `is_sensitive_key(key)` is True; make symmetric with `_encrypt_sensitive_args` | V55,B2 |
| T17 | . | Audit and remove dead `api/routes/assistant_*.py` files (8 files) that duplicate `api/assistant/` modules after refactor | V49 |
| T18 | . | Remove god-module re-exports from `core/agent/runner.py` (~70 lines of `# noqa: F401`); update callers to import from submodules directly | V49 |
| T19 | . | Remove dead code branch in `runner.py:958` (`execute_kwargs is not args` always True since `dict(args)` creates new object) | V49 |
| T20 | . | Add DB connection pool size config (`pool_size`, `max_overflow`) to `Settings`; pass to `create_async_engine` | V46 |
| T21 | x | Fix `deps.py` pending-approval commit: add `error_code` attribute to `AuthPendingApprovalError`; or change check to `isinstance` | V56,B1 |
| T22 | . | Fix CORS origins validator `_normalize_cors_origins` to handle JSON array strings per C9 (same pattern as `_normalize_prompt_extra_paths`) | V57,B3 |
| T23 | . | Fix `delete_user` self-delete guard: move `SelfDeleteAdminError` check inside `if is_admin_user` block; use generic error for non-admin self-delete | V58,B4 |

## §B Bugs

| id | date | cause | fix |
|---|---|---|---|
| B1 | 2026-04-24 | `AuthPendingApprovalError` has no `error_code` attr; `deps.py:41` `getattr(exc, "error_code", None)` always `None`; pending-approval branch never commits → first-time user insert rolled back | V56,T21 |
| B2 | 2026-04-24 | `_decrypt_sensitive_args` calls `maybe_decrypt_text` on ALL string values regardless of key; `_encrypt_sensitive_args` only encrypts `is_sensitive_key()` keys → asymmetric; non-sensitive strings matching `enc:v1:fernet:` prefix would be corrupted | V55,T16 |
| B3 | 2026-04-24 | `_normalize_cors_origins` splits on comma but doesn't handle JSON array format; env `API_CORS_ALLOWED_ORIGINS=["http://localhost:3000"]` produces `['["http://localhost:3000"]']` with brackets in URL; violates C9 | V57,T22 |
| B4 | 2026-04-24 | `authorization_service.delete_user:187-188` raises `SelfDeleteAdminError("Admins cannot delete their own account")` before checking `is_admin_user`; non-admin self-delete gets misleading admin error | V58,T23 |
| B5 | 2026-04-24 | `AssistantService` methods use `getattr(self._repository, "method_name", None)` → typo in method name silently returns `None` instead of raising; no type safety on repository/runner | V51,T13 |
