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
- C12. GitHub community health files follow GitHub's community standards. Issue/PR templates use YAML front matter. Conventional Commits enforced in contributing guide.
- C13. CI runs on every push/PR for both apps. Web: lint + typecheck + test. API: lint + test. Matrix strategy where applicable.
- C14. Security contact: `github@rendy.dev`. Coordinated disclosure policy.

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
| `/admin/users/{id}/tools` | PUT | Direct tool grants (disabled, 410) (admin) |
| `/admin/tools` | GET | List all registered tools (admin) |
| `/admin/roles` | GET/POST | List/create roles (admin) |
| `/admin/roles/{name}` | DELETE | Delete role (admin) |
| `/admin/roles/{name}/tools` | GET/PUT | Get/set tool permissions (admin) |
| `/admin/migrations/direct-grants` | POST | Migrate legacy direct grants to roles (admin) |
| `/admin/audit/action-requests` | GET | Query audit action requests (admin) |
| `/admin/audit/action-requests/{id}` | GET | Action request detail (admin) |
| `/admin/audit/action-requests/{id}/receipt` | GET | Action receipt payload (admin) |
| `/admin/whm/servers` | GET/POST | List/create WHM servers (admin) |
| `/admin/whm/servers/{id}` | PATCH/DELETE | Update/delete WHM server (admin) |
| `/admin/whm/servers/{id}/validate` | POST | Validate WHM server (admin) |
| `/admin/proxmox/servers` | GET/POST | List/create Proxmox servers (admin) |
| `/admin/proxmox/servers/{id}` | PATCH/DELETE | Update/delete Proxmox server (admin) |
| `/admin/proxmox/servers/{id}/validate` | POST | Validate Proxmox server (admin) |

### I.web — Next.js Frontend (apps/web)

| Surface | Purpose |
|---|---|
| `/` | Redirect → `/assistant` |
| `/login` | Login form (email/password) |
| `/assistant/[[...threadId]]` | Chat workspace (thread list + thread + tool UIs) |
| `/admin` | Redirect → `/admin/users` |
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

**WHM (17):** `whm_list_servers` (R), `whm_validate_server` (R), `whm_check_binary_exists` (R), `whm_mail_log_failed_auth_suspects` (R), `whm_list_accounts` (R), `whm_search_accounts` (R), `whm_preflight_account` (R), `whm_preflight_primary_domain_change` (R), `whm_preflight_firewall_entries` (R), `whm_suspend_account` (C), `whm_unsuspend_account` (C), `whm_change_contact_email` (C), `whm_change_primary_domain` (C), `whm_firewall_unblock` (C), `whm_firewall_allowlist_add_ttl` (C), `whm_firewall_allowlist_remove` (C), `whm_firewall_denylist_add_ttl` (C).

**Proxmox (13):** `proxmox_list_servers` (R), `proxmox_validate_server` (R), `proxmox_get_vm_status_current` (R), `proxmox_get_vm_config` (R), `proxmox_get_vm_pending` (R), `proxmox_get_user_by_email` (R), `proxmox_preflight_vm_cloudinit_password_reset` (R), `proxmox_preflight_move_vms_between_pools` (R), `proxmox_preflight_vm_nic_toggle` (R), `proxmox_reset_vm_cloudinit_password` (C), `proxmox_move_vms_between_pools` (C), `proxmox_disable_vm_nic` (C), `proxmox_enable_vm_nic` (C).

### I.db — Postgres Schema (15 tables)

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
- V3. `/auth/me` accepts cookie only (`noa_session` httpOnly cookie). Missing cookie → 401 `missing_authentication`. Invalid token → 401 `invalid_token`. Inactive user → 403 `user_pending_approval`. Bearer token auth path removed.
- V4. Passwords and access tokens never appear in structured logs.
- V5. JWT secret required in production (auto-generated ≥32 chars in dev). Insecure LDAP transport (`ldap://`) rejected in production unless `ldap_allow_insecure_transport=True` (explicit override). `auth_dev_bypass_ldap=True` rejected in production.
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

- V19. Missing `threadId` → 422. Missing thread → 404 `thread_not_found`. No-op request → JSON ack with current `runStatus` and `activeRunId` from canonical thread state (may be non-null if run active).
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
- V69. Pool membership move (Change Email PIC) ! require `old_email` + `new_email` (not single `email`). `old_email` ! exist on `source_pool` ACL, `new_email` ! exist on `destination_pool` ACL. `old_email` ≠ `new_email` (same PIC → `invalid_request`). Cross-validation prevents typo-based customer mismatch. System prompt ! map "change email PIC" / "change PIC" → pool membership move workflow.
- V70. Proxmox email params (`old_email`, `new_email`, `email`) ! accept both plain email (`user@domain.com`) and Proxmox userid (`user@domain.com@pve`). `_normalize_proxmox_userid` strips/appends realm as needed. Argument validation ! not reject `@pve` realm suffix as invalid email format.
- V71. Pool membership preflight ! check user **exists on pool ACL** (any role/permission entry), not specific privileges. `PVEConsoleUser`, `PVEVMAdmin`, any custom role — all valid pool association. `_has_any_pool_permission` ! return non-None when user has any ACL entry on pool path, regardless of which permissions granted.

### Workflows

- V40. Workflow templates produce todos, reply templates, evidence templates, approval context. Approval context includes `activity`, `beforeState`, `evidenceSections`, `argumentSummary`, `replyTemplate`.
- V41. `update_workflow_todo`: only one item `in_progress` at a time. Invalid status rejected with valid status list. `waiting_on_user` and `waiting_on_approval` are valid blocked statuses. Empty list clears thread state.
- V42. 7 registered workflow families: 4 WHM (`whm-account-lifecycle`, `whm-account-contact-email`, `whm-account-primary-domain`, `whm-firewall-batch-change`) + 3 Proxmox (`proxmox-vm-cloudinit-password-reset`, `proxmox-pool-membership-move`, `proxmox-vm-nic-connectivity`).

### Firewall

- V68. ∀ WHM firewall CHANGE tools (`whm_firewall_unblock`, `whm_firewall_allowlist_add_ttl`, `whm_firewall_allowlist_remove`, `whm_firewall_denylist_add_ttl`) ! reject non-IPv4 targets (CIDR, IPv6, hostname). `whm_preflight_firewall_entries` (READ) may accept all types for inspection.

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

### GitHub Workflow

- V59. Bug report template requires: description, steps to reproduce, expected vs actual behavior, environment (browser, OS, Node/Python version). Auto-labels `bug`.
- V60. Feature request template requires: problem statement, proposed solution, alternatives considered. Auto-labels `feature`.
- V61. PR template includes: summary, changes list, testing checklist (unit, lint, build, manual), security checklist (no secrets, no auth bypass, no raw SQL), related issues (`Closes #`).
- V62. CONTRIBUTING.md documents: prerequisites, dev setup, branch naming (`feat/`, `fix/`, `docs/`, `refactor/`, `test/`, `chore/`), Conventional Commits format, PR process, code style per app, testing requirements.
- V63. CODE_OF_CONDUCT.md uses Contributor Covenant v2.1. Enforcement contact matches C14.
- V64. SECURITY.md documents: supported versions (latest `master`), reporting channel (email + GitHub private advisory), response timeline (ack 48h, assessment 5 business days), severity-based fix timeline.
- V65. Web CI workflow triggers on `apps/web/**` changes. Steps: checkout, setup Node 20, install deps, lint (`npm run lint`), typecheck (`npm run typecheck`), test (`npm run test`).
- V66. API CI workflow triggers on `apps/api/**` changes. Steps: checkout, setup Python 3.12, setup uv, sync, lint (`uv run ruff check`), test (`uv run pytest -q`).
- V67. Issue template config (`config.yml`) disables blank issues, provides external links to discussions/docs if applicable.

### Approval UX

- V72. Approve/Deny buttons on approval-request card ! show confirmation dialog before dispatch. Dialog ! display action summary (activity, subject, reason). Single-click on Approve/Deny ! ⊥ trigger action directly.
- V73. Approval reply text ! show each fact exactly once. Action, reason, success criteria, preflight evidence — ⊥ duplicated across `summary`/`evidence_summary`/`approval_presentation`. `render_workflow_reply_text` output ! structured: title → preflight evidence (bullets) → key-value details (action, reason, success criteria each 1x) → next step.
- V74. Approval card subtitle ! derive from `argumentSummary` items only. Evidence sections (before-state, raw CSF/iptables) ⊥ leak into subtitle preview. `summarizeDetails` in `request-approval-tool-ui.tsx` ! receive `argumentSummary` items, ⊥ `evidenceSections` items.
- V75. Firewall approval before-state ! show only `csf.deny`/`csf.allow` log line (human-readable block reason). Raw iptables table dump (filter DENYIN/DENYOUT rules, ip6tables) ⊥ in before-state. `_firewall_entry_receipt_items` ! ⊥ pass `include_full_csf_raw_output=True` for approval before-state; use `_firewall_csf_receipt_value` (extracts last meaningful line) instead.

## §T Tasks

| id | status | task | cites |
|---|---|---|---|
| T1 | x | Add ESLint config for apps/web | C2 |
| T2 | x | Add dedicated web test runner (Vitest configured but no lint scripts) | C2 |
| T3 | x | Migrate legacy `integrations/whm/` to `whm/integrations/` (refactoring-map.md) | I.tools |
| T4 | x | Implement OpenTelemetry backend observability (traces + metrics, currently NoOp default) | I.ext |
| T5 | x | Add Proxmox postflight verification for pool-move and NIC-toggle workflows | V39,V40 |
| T6 | x | Remove Bearer token auth path (cookie-only) | V3,C4 |
| T7 | x | Extract shared telemetry helpers (`_safe_trace`/`_safe_metric`/`_safe_report`) to single module; remove 6+ copy-pasted versions across `error_handling.py`, `routes/auth.py`, `auth_dependencies.py`, `routes/whm_admin.py`, `routes/proxmox_admin.py`, `assistant/assistant_operations.py` | V49 |
| T8 | x | Consolidate `_require_admin` into `api/admin/guards.py`; remove duplicate implementations in `routes/whm_admin.py:288-316` and `routes/proxmox_admin.py:243-271` | V50 |
| T9 | x | Consolidate `_require_active_user` into `auth_dependencies.get_active_current_auth_user`; remove local copies in `routes/threads.py:89-108` and `routes/assistant.py:91-104` | V54 |
| T10 | x | Extract shared server name validation (`_validate_server_name`) and base URL normalization (`_normalize_*_base_url`) for WHM+Proxmox admin routes into shared module; also deduplicate `_status_family` | V49,V50 |
| T11 | x | Deduplicate `ensure_role`/`assign_role`/`get_role_names` between `SQLAuthRepository` (`auth_service.py:122-154`) and `SQLAuthorizationRepository` (`authorization_repository.py:146-178`); extract to shared base or mixin | V49 |
| T12 | x | Extract `AuthorizationUser` construction helper in `authorization_service.py`; replace ~8 repeated `AuthorizationUser(user_id=..., tools=[], direct_tools=[])` + `get_allowed_tool_names` + reconstruct patterns | V49 |
| T13 | x | Type `AssistantService` repository/runner via Protocol; remove all `getattr(self._repository, "method_name", None)` dispatch in `service.py:99-182` | V51,B5 |
| T14 | x | Add public query methods to `AssistantRunCoordinator` (`get_task_done`, `get_sequence`); remove `getattr` on `_tasks`/`_sequences` in `run_lifecycle.py:82-104` | V52 |
| T15 | x | Encrypt WHM server secrets (`api_token`, `ssh_password`, `ssh_private_key`, `ssh_private_key_passphrase`) and Proxmox server secrets (`api_token_secret`) at rest via Fernet | V53,C7 |
| T16 | x | Fix `_decrypt_sensitive_args` to only decrypt values where `is_sensitive_key(key)` is True; make symmetric with `_encrypt_sensitive_args` | V55,B2 |
| T17 | x | Audit and remove dead `api/routes/assistant_*.py` files (8 files) that duplicate `api/assistant/` modules after refactor | V49 |
| T18 | x | Remove god-module re-exports from `core/agent/runner.py` (~70 lines of `# noqa: F401`); update callers to import from submodules directly | V49 |
| T19 | x | Remove dead code branch in `runner.py:958` (`execute_kwargs is not args` always True since `dict(args)` creates new object) | V49 |
| T20 | x | Add DB connection pool size config (`pool_size`, `max_overflow`) to `Settings`; pass to `create_async_engine` | V46 |
| T21 | x | Fix `deps.py` pending-approval commit: add `error_code` attribute to `AuthPendingApprovalError`; or change check to `isinstance` | V56,B1 |
| T22 | x | Fix CORS origins validator `_normalize_cors_origins` to handle JSON array strings per C9 (same pattern as `_normalize_prompt_extra_paths`) | V57,B3 |
| T23 | x | Fix `delete_user` self-delete guard: move `SelfDeleteAdminError` check inside `if is_admin_user` block; use generic error for non-admin self-delete | V58,B4 |
| T24 | x | Align codebase with DESIGN.md (canonical source: `getdesign claude`). Fixed all oklch color values to match DESIGN.md hex targets, replaced cool-slate shadows with warm ring-based system, added missing design tokens (coral-accent, charcoal-warm, olive-gray, stone-gray, dark-warm, warm-silver, ring-warm, ring-deep, focus-blue, border-warm), added button variants (white-surface, dark-charcoal), added font-serif to DialogTitle/SheetTitle. | C2 |
| T25 | x | Create `.github/ISSUE_TEMPLATE/bug_report.md` — YAML front matter, auto-label `bug`, title prefix `bug: `, sections: description, steps to reproduce, expected/actual behavior, environment | V59,C12 |
| T26 | x | Create `.github/ISSUE_TEMPLATE/feature_request.md` — YAML front matter, auto-label `feature`, title prefix `feat: `, sections: problem statement, proposed solution, alternatives, design considerations | V60,C12 |
| T27 | x | Create `.github/ISSUE_TEMPLATE/config.yml` — disable blank issues, add external link to README | V67,C12 |
| T28 | x | Create `.github/pull_request_template.md` — summary, changes, testing checklist, security checklist, related issues | V61,C12 |
| T29 | x | Create `CONTRIBUTING.md` — prerequisites, dev setup (Docker/Postgres/API/Web), branch naming, Conventional Commits, PR process, code style (Python ruff + TS ESLint), testing (pytest + vitest) | V62,C12 |
| T30 | x | Create `CODE_OF_CONDUCT.md` — Contributor Covenant v2.1, enforcement contact `github@rendy.dev` | V63,C14 |
| T31 | x | Create `.github/SECURITY.md` — supported versions, reporting (email + private advisory), response timeline, severity tiers, security architecture summary | V64,C14 |
| T32 | x | Create `.github/workflows/web-ci.yml` — trigger on `apps/web/**`, Node 20, install, lint, typecheck, test | V65,C13 |
| T33 | x | Enhance `.github/workflows/api-scaffold-verify.yml` — add ruff lint step before pytest | V66,C13 |
| T34 | x | Update `README.md` — add badges (CI status), contributing link, code of conduct link, security link, license placeholder | V62 |
| T35 | x | Restrict `whm_firewall_unblock` & `whm_firewall_allowlist_remove` to IPv4-only targets; reject CIDR/IPv6/hostname same as `_add_ttl` tools. Update `docs/integrations/whm.md:199` to match | V68 |
| T36 | x | Reframe pool membership move as "Change Email PIC": replace single `email` → `old_email` + `new_email`; validate both against respective pools; update tool defs, prompt_hints, system prompt, workflow templates, matching, evidence, tests, docs | V69 |
| T37 | x | Fix Proxmox email param validation: strip `@pve` realm suffix before `_EMAIL_RE` check in `argument_validation.py`, or use custom format for Proxmox email params (not `"email"`). Update preflight error msgs to show normalized userid for clarity | V70,B7 |
| T38 | x | Fix pool membership preflight: replace `_REQUIRED_POOL_PERMISSIONS` intersection check with any-ACL-entry check. `_has_any_pool_permission` ! return non-None when user has any permission entry on pool path (any role counts as pool association). Update tests | V71,B8 |
| T39 | x | Add confirmation dialog to approval-request card Approve/Deny buttons. Dialog shows activity, subject, reason. Requires explicit confirm before dispatch | V72 |
| T40 | x | Deduplicate approval reply text across all 7 workflow families. Refactor `build_reply_template` (approval phase) → structured sections, each fact 1x. Update `render_workflow_reply_text` if needed. Add regression tests | V73,B9 |
| T41 | x | Fix firewall approval card: (1) subtitle use `argumentSummary` only in `request-approval-tool-ui.tsx:81-84`; (2) before-state drop raw iptables dump — remove `include_full_csf_raw_output=True` from `firewall.py:637` or pass `False` for before-state section | V74,V75,B10 |

## §B Bugs

| id | date | cause | fix |
|---|---|---|---|
| B1 | 2026-04-24 | `AuthPendingApprovalError` has no `error_code` attr; `deps.py:41` `getattr(exc, "error_code", None)` always `None`; pending-approval branch never commits → first-time user insert rolled back | V56,T21 |
| B2 | 2026-04-24 | `_decrypt_sensitive_args` calls `maybe_decrypt_text` on ALL string values regardless of key; `_encrypt_sensitive_args` only encrypts `is_sensitive_key()` keys → asymmetric; non-sensitive strings matching `enc:v1:fernet:` prefix would be corrupted | V55,T16 |
| B3 | 2026-04-24 | `_normalize_cors_origins` splits on comma but doesn't handle JSON array format; env `API_CORS_ALLOWED_ORIGINS=["http://localhost:3000"]` produces `['["http://localhost:3000"]']` with brackets in URL; violates C9 | V57,T22 |
| B4 | 2026-04-24 | `authorization_service.delete_user:187-188` raises `SelfDeleteAdminError("Admins cannot delete their own account")` before checking `is_admin_user`; non-admin self-delete gets misleading admin error | V58,T23 |
| B5 | 2026-04-24 | `AssistantService` methods use `getattr(self._repository, "method_name", None)` → typo in method name silently returns `None` instead of raising; no type safety on repository/runner | V51,T13 |
| B6 | 2026-04-26 | `whm_firewall_unblock` & `whm_firewall_allowlist_remove` accept CIDR/IPv6/hostname targets; `_add_ttl` tools correctly reject non-IPv4. Doc `whm.md:199` says "IPv4 only" for all ops but code only enforces on add. Drift both directions: doc imprecise, code too permissive | V68,T35 |
| B7 | 2026-04-26 | `_EMAIL_RE` (`^[^@\s]+@[^@\s]+\.[^@\s]+$`) in `argument_validation.py:10` rejects Proxmox userids containing `@pve` realm suffix (two `@` symbols). LM sees `user@domain.com@pve` in Proxmox data, echoes it back → argument validation blocks tool call with "not valid email". Without `@pve` tool works (code appends internally) but LM behavior unpredictable — sometimes includes realm from upstream data | V70,T37 |
| B8 | 2026-04-26 | `_meaningful_permission_entries` in `pool_tools.py:65` checks intersection with `_REQUIRED_POOL_PERMISSIONS` (`VM.Allocate`, `Pool.Allocate`, `Pool.Audit`). User with `PVEConsoleUser` role (grants `VM.Console`, `VM.PowerMgmt`, etc.) has valid ACL entry on pool but zero overlap with required set → preflight rejects as "does not have permissions". Change PIC only needs to confirm user exists on pool ACL, not specific privileges | V71,T38 |
| B9 | 2026-04-27 | `build_reply_template` (approval phase) passes same data to `details`, `approval_presentation.details`, & `approval_presentation.evidence_summary` → `render_workflow_reply_text` concatenates all → action 3x, reason 2x, success criteria 2x. `evidence` list includes `"Success condition: ..."` & `"Recorded reason: ..."` duplicating `details` rows. ∀ 7 workflow families | V73,T40 |
| B10 | 2026-04-27 | Firewall approval card 2 readability bugs: (a) `summarySourceItems` in `request-approval-tool-ui.tsx:81-84` prefers `evidenceSections` over `argumentSummary` → subtitle shows raw iptables/csf dump instead of action summary; (b) `firewall.py:637` passes `include_full_csf_raw_output=context.phase == "waiting_on_approval"` → before-state gets full iptables table dump instead of just `csf.deny`/`csf.allow` log line. After-state already clean (uses summary) | V74,V75,T41 |
