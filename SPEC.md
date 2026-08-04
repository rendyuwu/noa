# SPEC

## §G GOAL

NOA: MCP server for hosting-infrastructure operations. Exposes 14 RBAC-gated tools (READ + approval-gated CHANGE) to LibreChat via Streamable HTTP. Operator approves/denies CHANGE through iframe-embedded approval card served from NOA origin — reason typed there, ⊥ supplied by LLM. Admin panel manages users, roles, tool permissions, servers, audit log. Single repo, three deployables: API (`/mcp` + `/admin` routes), admin web (BIGSU), embed app (approval card).

## §C CONSTRAINTS

- C1. Python `<3.13` ≥3.11 (pgpy 0.6.0 imports removed `imghdr`; crashes tool-registry import at 3.13+). `uv` package manager, hatchling build.
- C2. Node.js 20+, Next.js 16, React 19, BIGSU (`@gio/bigsu-ui` `@gio/bigsu-icons` from `bigsu.biznetgio.pt/registry/`), pnpm. Versions pinned exact in lockfile — ⊥ caret ranges on `next`/`react`/`react-dom`. Bump = deliberate + T59 re-verify. Same discipline as C1 (Python) & T59 (LibreChat commit).
- C3. Postgres 16 via SQLAlchemy async + asyncpg. Alembic migrations. One shared DB schema.
- C4. LDAP authentication (dev bypass for local dev). LDAP = source of truth for employment, not just login.
- C5. Token-based MCP auth — ⊥ OAuth. Each user has own token minted by admin. Token hashed at rest (SHA-256), plaintext shown once at mint. Bearer token in `Authorization` header per MCP request.
- C6. RBAC: admin assigns roles → role has tool permissions → user has roles. `tools/list` RBAC-filtered per user. Execution gate re-checks permission. Disabled user → zero permissions regardless of roles.
- C7. Secrets (API tokens, SSH creds) encrypted at rest via Fernet (`NOA_SECRET_ENCRYPTION_KEY`). Key encrypts server credentials, ⊥ the DB — name says so.
- C8. Reason = single field, operator-typed in approval iframe. CHANGE tool schemas ⊥ carry any reason param — ⊥ `reason`, ⊥ `proposed_reason`. LLM ⊥ author, ⊥ relay, ⊥ see reason. Reason born at approve time, ⊥ at call time. Preflight runs in-process inside CHANGE call — evidence ⊥ cross tool boundaries.
- C24. LibreChat = sole MCP client. ⊥ Claude Desktop, ⊥ VS Code, ⊥ Goose. ⇒ `X-Noa-LibreChat-User` header required ∀ MCP req (bound ∧ unbound). Link-out text (V25) survives as T59 escape hatch + iframe-load-failure path, ⊥ as other-client support. Adding a second client = spec change, reopens V3 + V25.
- C9. One-workflow-one-tool: preflight, checks, execution → internal functions. Only final workflow exposed as MCP tool. Discovery READ tools stay separate.
- C10. Ambiguous identifiers → structured "ambiguous, here are candidates" result. ⊥ guess.
- C11. No secrets in git (`.env*` gitignored except `.env.example`). Env vars for lists use JSON arrays.
- C12. Single repo, three deploy artifacts: `apps/api/` (FastAPI + FastMCP), `apps/admin-web/` (Next.js BIGSU admin), `apps/web-embed/` (Next.js approval card iframe). One Alembic. One shared `core/`.
- C13. Old repo `noa-old` = reference/patterns source, ⊥ fork base. **Port source branch = `MCP`** — ⊥ `staging`, ⊥ working tree. `core/secrets/yopass.py`, `core/secrets/password.py`, `docs/integrations/yopass.md` exist ONLY on `MCP`/`main`. DECISIONS §2 LOC table measured on `staging` ⇒ ⊥ match port source (`core/secrets` 136 staging vs 290 MCP; `whm/integrations` 1,083 vs 1,179; `core/agent` 2,281 vs 2,854). Copy mature integration layers (WHM/Proxmox/PMG, `remote_exec`, `secrets`) — ⊥ rewrite.
- C14. Hygiene: `.py` ≤ 900 lines, `.ts` ≤ 300 lines, `.tsx` ≤ 450 lines. Reusable functions — ⊥ duplication. Code quality: type hints, tests for new code, conventional commits.
- C15. yopass credential delivery: password generated server-side (`_generate_password()`), stored via PGPy client-side encrypt (`_yopass_store()`). Plaintext ⊥ cross LLM boundary, ⊥ persisted, ⊥ in vault. URL returned to operator. Shared internal helper, ⊥ welded to password-reset tool.
- C16. `core/workflows/` (7,913 LOC old) → simplification target — much weight serves chat presentation being dropped.
- C17. Embed document ! sit on NOA origin — property, ⊥ mechanism. Iframe path (if T59 passes): `text/uri-list` selects `src` render mode → `allow-same-origin` → cookie rides; `text/html` renders `srcDoc` → opaque origin → cookie ⊥ sent → decision path dies silently. MIME dependency = upstream-fragile ⇒ see C21. Link-out path (T59 fail): top-level NOA origin, cookie rides natively, ⊥ MIME dependency.
- C18. Approve/deny ⊥ travel through LLM. Only path = cookie POST from NOA-origin document (iframe ∨ top-level tab) to NOA with `noa_session` cookie + CSRF token. "May this run?" read from DB, ⊥ from LLM claim or tool arg.
- C19. LibreChat's `toolApproval` ⊥ used. Authorization split: NOA RBAC = who may call; NOA approval gate = whether specific change may run; LibreChat = transport + rendering only.
- C20. Token binding TOFU: mint → `librechat_user_id` NULL; first MCP call with `X-Noa-LibreChat-User` header → bind; subsequent calls → header ! match else 401. Header absent → 401 ∀ states ∵ C24 (LibreChat-only).
- C21. UPSTREAM DRIFT — C17 chain **unverified** against pinned LibreChat, ⊥ inherited. LibreChat PR #13831 (open) drops `@mcp-ui/client` for official `@modelcontextprotocol/ext-apps` SDK; classifies ONLY `text/html;profile=mcp-app` as app-backed; plain `text/html` `ui://` → `sandbox=""` inert `srcDoc`; grants `allow-same-origin` to inner frame only when sandbox runs on dedicated origin. `text/uri-list` = mcp-ui concept, ∉ MCP Apps spec (ext-apps #318: only `text/html;profile=mcp-app` in scope). ⇒ if #13831 becomes default render path, `text/uri-list` has ⊥ render path at all — ⊥ merely "proxy breaks same-origin". T59 = **blocking gate** ∀ V22,V24,V37,V39,V40,V44,V64 + T32,T41–T46,T56.
- C22. Never-implement boundary (DECISIONS §6.2 — management policy, ⊥ technical): `whm_change_contact_email`, `whm_change_primary_domain`, `proxmox_move_vms_between_pools`, `proxmox_preflight_move_vms_between_pools`, `proxmox_get_user_by_email`, `whm_check_binary_exists`, `whm_firewall_denylist_add_ttl`. ⊥ port, ⊥ expose, ⊥ re-add. Re-add = policy decision, ⊥ agent call.
- C23. MCP protocol era = **handshake (`2025-06-18`)**, ⊥ sessionless `2026-07-28`. DECIDED 2026-08-04 by evidence, ⊥ preference. Driver = C24: LibreChat = sole client, `packages/api/package.json` declares `@modelcontextprotocol/sdk: ^1.29.0` ⇒ v1.x line ⇒ `LATEST_PROTOCOL_VERSION = "2025-06-18"`, `initialize` handshake + `Mcp-Session-Id`. Serving `2026-07-28` to a v1.x client buys nothing. Server dep = `fastmcp==3.4.5` (stable 2026-07-27, `requires_python >=3.10`, satisfies C1). ⊥ `fastmcp` 4.x — beta only (4.0.0b1, needs `--pre`), removes server-initiated sampling/roots, drops 3.x shims, relocates `fastmcp.server.auth.*` ⇒ breaks T11 `TokenVerifier` for zero gain. Re-open when LibreChat ships SDK v2 — even then v2 emits `2026-07-28` only on explicit opt-in.

## §I INTERFACES

### I.mcp — MCP Server (`apps/api`)

| Item | Contract |
|---|---|
| Transport | Streamable HTTP, single endpoint `https://noa.internal/mcp` |
| Auth | Per-user NOA-minted bearer token. LibreChat side: `customUserVars.NOA_MCP_TOKEN` (`sensitive: true`) → `Authorization: Bearer {{NOA_MCP_TOKEN}}` in `librechat.yaml`. Plus `X-Noa-LibreChat-User: {{LIBRECHAT_USER_ID}}` for TOFU binding — required ∀ req per C24. |
| Protocol | Handshake era `2025-06-18` (C23). `initialize` + `Mcp-Session-Id` in play. Server = `fastmcp==3.4.5`. ⊥ `Mcp-Method`/`Mcp-Name` headers, ⊥ `ttlMs`/`cacheScope` — both are `2026-07-28`-only. |
| `tools/list` | RBAC-filtered per user. Returns only tools permitted by user's role permissions. Cache hints bounded per V74. |
| `tools/call` | Authenticate → resolve user → RBAC check → READ executes now; CHANGE (unapproved) → INSERT `action_requests(status=pending)` → return text + approval surface; CHANGE (approved) → execute → write `tool_runs` + `action_receipts` + audit. CHANGE args ⊥ include any reason field (C8,V15). |
| Tool result | READ: text (+ optional table surface for large results, V64). CHANGE: `build_change_gate_response()` → 3 branches: link-out text \| UI resource \| elicitation (future). Active branch = T59 outcome. URL carries `action_request_id` only. |
| Exposed tools | **14 tools** (DECISIONS §6 + §9 + `noa_get_action_result` restored per V76): `whm_suspend_account`, `whm_unsuspend_account`, `whm_list_accounts`, `whm_search_accounts`, `whm_preflight_firewall_entries`, `whm_firewall_release_and_allow`, `whm_firewall_allowlist_remove`, `proxmox_reset_vm_password`, `proxmox_vm_nic(action)`, `pmg_whitelist(action)`, `pmg_whitelist_list`, `pmg_whitelist_search`, `whm_list_servers`, `noa_get_action_result` |
| Never implement | C22 list — ⊥ port, ⊥ expose, ⊥ re-add (management policy boundary) |
| Internal functions | All `*_preflight_*` tools, `update_workflow_todo`, `proxmox_list_servers`, `pmg_list_servers`, `proxmox_get_vm_status_current`, `proxmox_get_vm_config`, `proxmox_get_vm_pending`, `whm_mail_log_failed_auth_suspects`, `get_current_time`/`get_current_date`, `whm_validate_server`, `proxmox_validate_server`, `pmg_validate_server` |

### I.admin-api — Admin REST (`apps/api`)

| Route | Method | Purpose |
|---|---|---|
| `/health` | GET | Health check → `{"status":"ok"}` |
| `/auth/login` | POST | LDAP auth → JWT in httpOnly cookie |
| `/auth/logout` | POST | Clear session cookie |
| `/auth/me` | GET | Current user (cookie-only) |
| `/admin/users` | GET/POST | List/create users |
| `/admin/users/{id}` | PATCH/DELETE | Enable/disable/delete user |
| `/admin/users/{id}/roles` | PUT | Replace user roles |
| `/admin/users/{id}/tokens` | GET/POST | List tokens / mint new (plaintext once) |
| `/admin/users/{id}/tokens/{token_id}` | DELETE | Revoke token |
| `/admin/roles` | GET/POST | List/create roles |
| `/admin/roles/{name}` | DELETE | Delete role |
| `/admin/roles/{name}/tools` | GET/PUT | Get/set tool permissions |
| `/admin/audit/tool-runs` | GET | Query tool runs (READ+CHANGE); filters: toolName, status, conversationRef, requestedByEmail, from/to date, cursor pagination |
| `/admin/audit/tool-runs/{id}` | GET | Tool run detail: args (redacted), result summary, timing |
| `/admin/whm/servers` | GET/POST | List/create WHM servers |
| `/admin/whm/servers/{id}` | PATCH/DELETE | Update/delete WHM server |
| `/admin/whm/servers/{id}/validate` | POST | Validate WHM server (SSH connect + fingerprint) |
| `/admin/proxmox/servers` | GET/POST | List/create Proxmox servers |
| `/admin/proxmox/servers/{id}` | PATCH/DELETE | Update/delete Proxmox server |
| `/admin/proxmox/servers/{id}/validate` | POST | Validate Proxmox server |
| `/admin/pmg/servers` | GET/POST | List/create PMG servers |
| `/admin/pmg/servers/{id}` | PATCH/DELETE | Update/delete PMG server |
| `/admin/pmg/servers/{id}/validate` | POST | Validate PMG server |
| `/me/mcp-tokens` | GET/POST | List own tokens / mint new |
| `/me/mcp-tokens/{id}` | DELETE | Revoke own token |

### I.embed — Embed App (`apps/web-embed`)

| Route | Purpose |
|---|---|
| `/approvals/[id]` | Approval card (GET detail + POST approve/deny). Reason input box lives here — sole origin of `reason` (C8,V15). Compact, self-scrolling, no AppShell. |
| `/action-requests/{id}` | Thread-less confirmation detail + execution status (polling surface) |
| `/action-requests/{id}/approve` | POST approve → 202 `{tool_run_id}`, async execute. Body `{reason, csrf}`. Blank/whitespace `reason` → 409 `change_reason_required`. Session cookie. |
| `/action-requests/{id}/deny` | POST deny. Body `{reason, csrf}`. Session cookie. |
| `/tables/[token]` | Large-READ table surface (V64). Read-only, requester-matched, ⊥ decision controls. |
| `/healthz` | Liveness check |

Framing headers: only embed app allowlists `frame-ancestors https://chat.noa.internal`. Admin app sends `frame-ancestors 'none'`. Embed 401 → explicit "cannot authenticate here" state, ⊥ blank card, ⊥ live Approve button.

### I.admin-web — Admin Panel (`apps/admin-web`)

PORT from old repo `apps/web-bigsu/src/app/(protected)/admin/`: users, roles, audit, audit/tool-runs, whm, proxmox, pmg. BIGSU components. Own build/deploy. ⊥ frameable.

### I.ext — External Dependencies

| System | Interface |
|---|---|
| LibreChat | `librechat.yaml` MCP server entry: `streamable-http` → `https://noa.internal/mcp`, `customUserVars` for token + user ID headers. LDAP auth same directory, local registration disabled. |
| LDAP | Service-account bind + search for `user_exists_and_enabled(email)`. Token revalidation on staleness interval. |
| Postgres | One DB, one schema. Alembic migrations. |
| WHM servers | SSH (asyncssh) with host-key pinning + TOFU refresh. `sudo -n` for non-root. CSF + Imunify. |
| Proxmox servers | Proxmox API (HTTP). |
| PMG servers | SSH + `pmgsh` CLI (argv-only, ⊥ shell string). |
| yopass | `POST <YOPASS_BASE_URL>/secret` for encrypted secret delivery. PGPy client-side encrypt. |

## §V INVARIANTS

### Auth & Tokens

- V1. ∀ MCP req → resolve `users.id` + re-check `is_active`. `tools/list` RBAC-filtered per user. `tools/call` re-checks permission.
- V2. MCP auth = per-user bearer token, minted by NOA admin. Token ↔ `users.id`. Hashed at rest (SHA-256). Plaintext shown once at mint. Revoke = delete row. ⊥ logged.
- V3. Token TOFU binding: minted `librechat_user_id=NULL`; NULL + `X-Noa-LibreChat-User` header present → bind; non-NULL → header ! present & equal else 401. Absent header → 401 ∀ states (bound ∧ unbound) ∵ C24 LibreChat-only. 401 body names cause: `librechat_user_header_missing` \| `librechat_user_mismatch`. ⊥ unbound-token client path.
- V4. Token revalidation: `last_ldap_check_at` on row, staleness interval. LDAP down → fail closed. Cascade revoke on admin disable + LDAP path.
- V5. Resolve MCP identity in exactly one function, called ∀ MCP request. Auth mechanism swap = one file.
- V6. Login sets httpOnly `noa_session` cookie (SameSite=Lax, Domain=`.noa.internal`, Path=/). Logout clears with max-age=0. Logout idempotent without auth.
- V7. New LDAP users auto-provisioned `is_active=False`. Bootstrap admin auto-activated with `admin` role.
- V8. Passwords and tokens ⊥ appear in structured logs.
- V9. Rate limiter blocks after configured max failures within window. Rate-limited → 429 with `Retry-After`.

### RBAC

- V10. Admin role bypasses tool permission checks for known tools but rejects unknown/unregistered tools.
- V11. Disabled users (`is_active=False`) → zero permissions regardless of roles.
- V12. Cannot disable last active admin. Admin cannot self-deactivate. Admin self-delete → 409.
- V13. Non-admin users → 403 on admin endpoints. `admin` role reserved (cannot edit tools or delete). Internal roles cannot be assigned via API.
- V14. Admin changes produce audit events. Permission updates take effect immediately.

### Tool Discipline

- V15. CHANGE tool schemas ⊥ carry reason param of any name. Reason enters exactly once: operator types in approval iframe → approve POST body. Approve without non-blank `reason` → 409 `change_reason_required`. Gate = approve endpoint, ⊥ tool call.
- V16. READ tools execute immediately. CHANGE tools go through approval gate: create `action_request` (PENDING) → operator approves/denies → execute if approved.
- V17. One-workflow-one-tool: preflight runs in-process inside CHANGE call. Evidence born in-process, lives milliseconds, same user, ⊥ in transcript. ⊥ evidence store needed.
- V18. Ambiguous identifiers → structured "ambiguous, here are candidates" result. ⊥ guess.
- V19. Tool error sanitization: raw exceptions ⊥ reach LLM. `RuntimeError` → `tool_execution_failed`. `TimeoutError` → `timeout`.
- V20. Lifecycle enums machine-stable, **3 distinct enums** — ⊥ one flat set. `ToolRisk` = `READ` \| `CHANGE` (classification, ⊥ a status). `ActionRequestStatus` = `PENDING` \| `APPROVED` \| `DENIED` \| `EXPIRED`. `ToolRunStatus` = `STARTED` \| `COMPLETED` \| `FAILED`. `tool_runs` carries risk + status as separate columns ⇒ failed READ representable. `EXPIRED` = new work, ∉ `noa-old` `ActionRequestStatus`.
- V21. Whitespace-only required strings rejected. Duplicate firewall targets rejected. Invalid server_ref rejected.

### Approval Gate

- V22. Approve/deny ⊥ travel through LLM. Only path = POST from embed iframe → NOA with `noa_session` cookie + CSRF token.
- V23. "May this run?" read from `action_requests.status` in DB ∀ time — ⊥ from LLM claim, ⊥ from tool arg.
- V24. `build_change_gate_response()` shapes every CHANGE tool result; 3 branches: link-out text \| UI resource \| elicitation (future). Active branch selected by T59 outcome, ⊥ assumed. Upgrade path = swap branch, backend unchanged.
- V25. Text content ! always carry approval URL plain ⇒ link-out always live. Serves 2 paths: T59-fail (C21 → link-out becomes primary) + iframe-load-failure. ⊥ a non-LibreChat-client feature (C24).
- V26. Embed URL carries `action_request_id` only — ⊥ token, ⊥ ops data. Tool-result artifacts persist in LibreChat MongoDB ⇒ assume LibreChat admin can read them.
- V27. Embed access control: requester-match primary (`requested_by_user_id == caller`, caller = cookie identity). Mismatch → 404, not 403 (existence ⊥ leak).
- V28. Exactly one `pending → decided` transition per request, under row lock. Concurrent double-click → one wins, loser gets 409.
- V29. Approve → async execution, 202 + `tool_run_id`, embed polls to terminal. State in DB, ⊥ in connection.
- V30. Async host = in-process asyncio task with own session + reaper for runs stuck in STARTED.
- V31. Explicit per-user cap on in-flight CHANGE executions (default 1). Over limit → 409, ⊥ silent queue.
- V32. Pending requests expire on TTL → terminal `expired`; approve/deny on expired → 409.
- V33. Approval context built at gate time and persisted on row, ⊥ rebuilt from transcript at render time.
- V34. One URL per request; `/approvals/[id]` owns whole lifecycle through receipt.
- V35. Approval card shows provenance: created-at, conversation ref, origin, requesting identity.
- V36. Post-decision notification to LLM = notification only. Ignored ⇒ change still executed and recorded.

### Embed & Security

- V37. Embed iframe document ! sit on NOA origin — property holds ∀ render paths. Mechanism T59-gated (C21), ⊥ inherited: path A = `text/uri-list` → `src` mode → `allow-same-origin` → cookie rides (requires T59 pass); path B = link-out new-tab, top-level NOA origin, cookie rides natively (⊥ iframe, ⊥ MIME dependency). T59 fail → path B primary, V22 unchanged ∵ decision still cookie POST from NOA origin.
- V38. Embed 401 renders explicit "cannot authenticate here" state — ⊥ blank card, ⊥ live Approve button.
- V39. CSRF token server-minted, signed, session-bound. Double-submit alone insufficient — any `*.noa.internal` sibling can plant cookie.
- V40. Same registrable domain; `noa_session` scoped `Domain=.noa.internal`, SameSite=Lax.
- V41. Admin app sends `frame-ancestors 'none'`. Embed app sends `frame-ancestors https://chat.noa.internal`.
- V42. Embed has no login page, no LDAP form, no credential handling. 401 → "Sign in to NOA" + retry.
- V43. Exactly ONE reason field exists, operator-typed in approval iframe (C8). ⊥ `proposed_reason`, ⊥ LLM-authored reason anywhere in schema/DB/receipt. LLM-authored text (tool args, activity label) = evidence only, ⊥ authoritative, ⊥ stored in `action_requests.reason`.
- V44. Embed ⊥ trigger decision from inbound `postMessage`. Any listener validates `event.origin` and is read-only.

### Audit

- V45. Every MCP READ writes `tool_runs` row: requester, truncated summary, redacted args, queryable in admin audit.
- V46. Every CHANGE (approved) writes `tool_runs` + `action_receipts` + audit log.
- V47. Tool run audit: `requested_by_user_id`, `tool_name`, `status`, `conversation_ref`, `result_summary` (truncated), args (redacted), timing.

### Secrets & Crypto

- V48. `SecretCipher` round-trips encrypt/decrypt. Encrypted format `enc:v1:fernet:...`. Server secrets encrypted at rest.
- V49. Password generation server-side (`_generate_password()`), ⊥ LLM arg. Plaintext lives only in `execute()` scope — ⊥ persisted, ⊥ cross LLM boundary. Tool returns only `yopass_url`.
- V50. yopass: blob = `username` + `password` → PGPy client-side encrypt → `POST /secret` → URL with key in fragment. Fragment ⊥ sent to yopass server.

### Infrastructure

- V51. `/health` → 200 `{"status":"ok"}`.
- V52. Fernet key (`NOA_SECRET_ENCRYPTION_KEY`) required in production. Auto-generated in dev. Name reflects scope: encrypts server credentials, ⊥ the DB.
- V53. JWT secret required in production (auto-generated ≥32 chars in dev).

### Firewall

- V54. ∀ WHM firewall CHANGE tools ! reject non-IPv4 targets (CIDR, IPv6, hostname). Preflight READ may accept all types.
- V55. SSH commands prefix `sudo -n` ⟺ resolved ssh user ≠ `root`. `sudo -n` non-interactive.
- V56. SSH command output ! strip login/PAM/LVE banners before tool-specific parsing. `CommandResult` ! retain raw output for audit.
- V57. Dual-backend CSF+Imunify: each tool checks `available["csf"]` and `available["imunify"]`, acts on both in parallel via `asyncio.gather`. **Zero backends available → error `no_firewall_backend`, ⊥ success.** `noa-old` bug: empty task dict → `gather()` → `[]` → reports ok having changed nothing. Silent no-op on approved CHANGE = \⊥ acceptable.

### PMG

- V58. PMG whitelist target = PMG mynetworks (`/config/mynetworks`). PMG commands use SSH + argv-safe builder only. `TERM=dumb`, `pmgsh` (argv-only).
- V59. `pmg_whitelist_search` normalizes single-host inputs: IPv4 `1.2.3.4` ≡ `1.2.3.4/32`. Search checks CIDR list exact membership.
- V60. `pmg_whitelist_add` validates IP/CIDR via `ipaddress`; single host → `/32`. Add → `pmgsh create /config/mynetworks -cidr <cidr>` then `pmgconfig sync --restart 1`.
- V61. `pmg_whitelist_remove` validates + normalizes, deletes exact CIDR via `pmgsh delete /config/mynetworks/<cidr>` then `pmgconfig sync --restart 1`.

### Proxmox

- V62. `proxmox_reset_vm_password`: generate password internally → yopass store → set cipassword + regenerate cloud-init + verify (crypt-compare). yopass fail → abort before set. Set fail after yopass → URL holds unapplied password, old creds valid. **libcrypt absent (`_load_crypt_lib()` → `None`) → receipt states `verification_unavailable`, ⊥ silent pass.** Verification-unavailable ≠ verified.
- V63. `proxmox_vm_nic(action: enable|disable)` — single tool, enum action param. Preflight runs inside.

### Large Output

- V64. Large READ results (`whm_list_accounts`, `pmg_whitelist_list`) → tool result carries short summary + URL to NOA-origin table page, ⊥ full table in context. Zero token cost for table body. Render mechanism = same T59 gate as V37 (C21): path A iframe UI resource \| path B plain link-out. ⊥ mix `ui://` scheme with `text/uri-list` — one mechanism, chosen once by T59. Shared capability, ⊥ per-tool special case.

### Code Quality

- V65. `.py` files ≤ 900 lines. `.ts` files ≤ 300 lines. `.tsx` files ≤ 450 lines.
- V66. Reusable functions over duplication. DRY. Shared code in `core/`.
- V67. New code ! have type hints (Python) / TypeScript types. Tests for new functionality.
- V68. Conventional commits. No secrets in git.
- V69. `noa-old` = reference for patterns. Mature integration layers (WHM/Proxmox/PMG, `remote_exec`, `secrets`) copied, ⊥ rewritten. SSH banner stripping, `sudo -n` escalation, host-key pinning, TOFU refresh already hardened there.

### LibreChat

- V70. LibreChat auth = same LDAP directory, local registration disabled.
- V71. LibreChat agent config carries model-facing safety policy: preflight-first, approval gates, no fabrication, argument discipline, `\ui{}` instruction. ⊥ mention reason-writing ∵ C8 (LLM never authors reason).
- V72. LibreChat `toolApproval` ⊥ configured, ⊥ relied on. VERIFIED 2026-08-04 `packages/api/src/agents/hitl/policy.ts`: `isHITLEnabled` = `policy?.enabled === true` ⇒ absent config ∧ `enabled:false` both fully bypass. ⊥ default-ask trap. §7.3 spike CLOSED. Glob-vs-RBAC mismatch (V19 split) stands regardless.

### Error Shape & Grants

- V73. ∀ error response carries `request_id` in body + `x-request-id` header. (`noa-old` V8.)
- V74. Stale tool catalog ⊥ become a security hole: V1 execution-time RBAC re-check = backstop ⇒ cached `tools/list` may *show* a revoked tool, calling it still 403s. Handshake era (C23) has ⊥ `ttlMs`/`cacheScope` fields ⇒ nothing to bound today. NOA ! emit `notifications/tools/list_changed` on permission change; whether LibreChat honours it = unverified → T59 checklist. V14 "immediately" = authoritative at execution, best-effort in catalog display. C23 → `2026-07-28` later ⇒ add `ttlMs` ceiling + per-user `cacheScope`.
- V75. Direct per-user tool grants disabled → 410 `direct_tool_grants_disabled`. Permissions flow role→user only. Role replacement preserves internal roles. (`noa-old` V14.)
- V76. `noa_get_action_result(id)` (READ, exposed) enforces V27 requester-match against MCP-token identity. Foreign id ∧ unknown id → identical not-found shape ⇒ ⊥ enumeration oracle. Prompt injection ⊥ pull another operator's action into transcript. (`noa-old` V162.)

### Firewall TTL

- V77. `whm_firewall_release_and_allow` ! require `duration_minutes` (integer, 1–525600). ⊥ permanent allowlist entry, ⊥ server-side default — operator states duration in chat ("5 min", "2 hours", "5 days"), LLM converts to minutes as ordinary tool arg (⊥ a reason, C8 unaffected). Receipt after-state ! show resolved expiry timestamp. Preserves `noa-old` `whm_firewall_allowlist_add_ttl` bound — merge (§6.5) ⊥ drop TTL.
- V78. `whm_firewall_allowlist_remove` = undo path, ⊥ TTL param ∵ removal is immediate.

## §T TASKS

id|status|task|cites
T1|x|git init + `.gitignore` (Python, Node, env, IDE, OS). Commit as "chore: init repo"|-
T2|x|scaffold monorepo structure: `apps/api/`, `apps/admin-web/`, `apps/web-embed/`, `core/`, `docs/`. Root `pyproject.toml` (workspace), `.env.example`, `README.md`, `AGENTS.md`|C12,C14
T3|x|`apps/api/` scaffold: FastAPI + FastMCP skeleton, `pyproject.toml` deps pinned exact: `fastmcp==3.4.5` (⊥ 4.x per C23), fastapi, uvicorn, sqlalchemy[asyncio], asyncpg, alembic, pydantic, pydantic-settings, structlog, orjson, cryptography, PyJWT, python-ldap, `pgpy>=0.6.0`, asyncssh, httpx. `requires-python = ">=3.11,<3.13"`|C1,C3,C7,C23
T4|.|Postgres schema v1: `users`, `roles`, `user_roles`, `role_tool_permissions`, `mcp_tokens`, `whm_servers`, `proxmox_servers`, `pmg_servers`. Alembic initial migration|C3,V1,V2,V3,V11
T5|.|`core/config.py`: pydantic-settings, all env vars (DB, LDAP, Fernet key, JWT secret, yopass, token TTL, LDAP revalidate interval, per-user concurrency cap, pending TTL, etc.)|C9,C7
T6|.|LDAP auth service: `LDAPService` with service-account bind + search. `user_exists_and_enabled(email)`. Dev bypass mode|C4,V7
T7|.|JWT service: mint, verify, httpOnly cookie set/clear. SameSite=Lax, `Domain=.noa.internal`|V6,V8
T8|.|Login flow: `POST /auth/login` → LDAP auth → JWT cookie. `POST /auth/logout` → clear. `GET /auth/me`. Rate limiter|V6,V7,V8,V9
T9|.|RBAC engine: `role_tool_permissions` CRUD, `get_permitted_tools(user_id)`, admin bypass for known tools, disabled-user zero-permissions|V10,V11,V12,V13,V14
T10|.|Token management: `mcp_tokens` model + migration (id, user_id FK, token_hash, label, librechat_user_id nullable, last_used_at, last_ldap_check_at, created_at, expires_at). Mint: generate crypto token, hash SHA-256, return plaintext once. List: prefix + label + timestamps. Revoke: delete row|C5,V2
T11|.|Token verification: custom `TokenVerifier` subclass — lookup `token_hash` in DB → return `AccessToken(user_id, scopes=[...])`. TOFU binding logic: NULL + header → bind; non-NULL → match else 401. Revalidation via LDAP staleness check. Cascade revoke on admin disable|C20,V2,V3,V4
T12|.|`resolve_mcp_identity(request) → User`: single function — extract `Authorization: Bearer <token>`, hash, DB lookup, TOFU binding, LDAP revalidation, `is_active` check. 401 shapes: missing header, invalid token, expired, bound mismatch. Rate-limit failed auth per source|V1,V5,V4,V9
T13|.|FastMCP server: `mcp = FastMCP("NOA")` with custom `TokenVerifier`. Mount into FastAPI via `mcp.http_app(path="/")` + `app.mount("/mcp", mcp_app)` + `combine_lifespans`|V1,I.mcp
T14|.|Port from `noa-old`: `core/remote_exec/` (SSH with banner stripping, `sudo -n`, host-key pinning, TOFU refresh)|C13,V55,V56,V69
T15|.|Port from `noa-old` branch `MCP`: `core/secrets/` (Fernet `SecretCipher`, `_generate_password()`, `_yopass_store()`, `docs/integrations/yopass.md`). ⊥ branch `staging` — `yopass.py`/`password.py`/yopass doc absent there|C7,C13,C15,V48,V49,V50
T16|.|Port from `noa-old`: WHM integration layer (`whm/integrations/ssh.py` — CSF + Imunify dual-backend, `asyncio.gather` parallel check)|C13,V57,V69
T17|.|Port from `noa-old`: Proxmox integration layer (`proxmox/integrations/client.py`)|C13,V69
T18|.|Port from `noa-old`: PMG integration layer (`pmg/integrations/ssh.py`, `pmg/integrations/pmgsh_cli.py` — argv-safe, `TERM=dumb`)|C13,V58,V69
T19|.|Tool: `whm_list_servers` (READ). Expose per DECISIONS §6.6. Return server list from DB. `resolve_whm_server_ref` for UUID/name/hostname → choices on ambiguity|I.mcp,V18
T20|.|Tool: `whm_list_accounts` (READ). Port from `noa-old`. Large result → summary + table URL per V64. Mechanism gated on T59|I.mcp,V64,T59
T21|.|Tool: `whm_search_accounts` (READ). Port from `noa-old`. Filter + limit 1–100|I.mcp
T22|.|Tool: `whm_suspend_account` (CHANGE). Internal preflight → suspend. Single tool, preflight in-process. ⊥ reason param in schema|I.mcp,C8,C9,V15,V16,V17
T23|.|Tool: `whm_unsuspend_account` (CHANGE). Internal preflight → unsuspend. Not merged with suspend (opposite risk directions, DECISIONS §9). ⊥ reason param in schema|I.mcp,C8,C9,V15,V16,V17
T24|.|Tool: `whm_preflight_firewall_entries` (READ exposed). DECISIONS §6.5 — operator decision point. CSF + Imunify dual check|I.mcp,V54,V57
T25|.|Tool: `whm_firewall_release_and_allow(server_ref, target, duration_minutes)` (CHANGE merged). `duration_minutes` REQUIRED, 1–525600, LLM converts operator phrasing ("5 min", "2 hours", "5 days") → minutes. Internal: release from denylist → add to allowlist with TTL. One approval, two-part receipt (before-state = block reason + log evidence, after-state = released & allowlisted + expiry timestamp). `sudo -n` where needed|I.mcp,C8,C9,V54,V55,V77
T26|.|Tool: `whm_firewall_allowlist_remove` (CHANGE). Undo path. `sudo -n` where needed|I.mcp,C8,V54,V55
T27|.|Tool: `proxmox_reset_vm_password` (CHANGE). Internal preflight → generate password → yopass store → set cipassword + regenerate cloud-init + verify. yopass fail → abort before set. libcrypt absent → `verification_unavailable`. ⊥ reason param in schema|I.mcp,C8,C15,V49,V62
T28|.|Tool: `proxmox_vm_nic(action: enable\|disable)` (CHANGE merged). Single tool, enum `action` param. Internal preflight. DECISIONS §9 enum collapse|I.mcp,C8,C9,V63
T29|.|Tool: `pmg_whitelist(action: add\|remove)` (CHANGE merged). Single tool, enum `action` param. Internal preflight → `pmgsh` command. DECISIONS §9 enum collapse|I.mcp,C8,C9,V58,V60,V61
T30|.|Tool: `pmg_whitelist_list` (READ). List all mynetworks CIDRs. Large result → summary + table URL per V64. Mechanism gated on T59|I.mcp,V58,V64,T59
T31|.|Tool: `pmg_whitelist_search` (READ). Normalize input → search exact CIDR membership|I.mcp,V58,V59
T32|.|CHANGE gate: `build_change_gate_response()` — single function, 3 branches (link-out text \| UI resource \| elicitation future). URL carries `action_request_id` only|V24,V25,V26
T33|.|CHANGE gate: MCP `tools/call` on CHANGE → INSERT `action_requests(status=pending)` + `approval_context` (persisted) + `conversation_ref`. ⊥ `proposed_reason` column — reason arrives at approve time only (C8,V43). "May this run?" from DB|V22,V23,V33,V43,I.mcp
T34|.|`action_requests` table + migration: `id`, `tool_name`, `requested_by_user_id`, `status` (`ActionRequestStatus` PENDING/APPROVED/DENIED/EXPIRED), `conversation_ref`, `approval_context` JSONB, `reason` (nullable until decided, operator-typed), `tool_run_id` nullable, `expires_at`, `created_at`, `decided_at`. ⊥ `proposed_reason` column|V20,V32,V33,V43
T35|.|`tool_runs` table + migration: `id`, `tool_name`, `requested_by_user_id`, `risk` (`ToolRisk` READ/CHANGE), `status` (`ToolRunStatus` STARTED/COMPLETED/FAILED), `conversation_ref`, `args` JSONB (redacted), `result_summary`, `timing`, `created_at`. Risk ∧ status = separate columns ⇒ failed READ representable|V20,V45,V46,V47
T36|.|`action_receipts` table + migration: `id`, `action_request_id` FK, `tool_run_id` FK, `receipt_data` JSONB, `created_at`|V46
T37|.|Approve/deny endpoints: `POST /action-requests/{id}/approve` (body `{reason, csrf}`) → validate non-blank `reason` else 409 `change_reason_required` → row lock → CAS pending→approved → async execute → 202 `{tool_run_id}`. `POST /action-requests/{id}/deny` → row lock → CAS pending→denied. Mismatch → 404, already decided → 409|V15,V27,V28,V29
T38|.|Async executor: in-process asyncio task with own DB session. `execute_approved_tool_run(tool_run_id)`. Reaper covers BOTH runs stuck in STARTED and requests stuck APPROVED with no run started. Per-user concurrency cap|V29,V30,V31
T39|.|Pending expiry: background sweep AND check-on-read (both, ⊥ either/or) — `expires_at < now()` & status=PENDING → EXPIRED. Sweep guarantees V32 terminality without traffic; check-on-read prevents serving a stale PENDING. Approve/deny on expired → 409|V32
T40|.|Embed app scaffold: `apps/web-embed` — Next.js 16, own `package.json`, `tsconfig`, `eslint`, `vitest`, `playwright`. Hygiene: `.ts≤300`, `.tsx≤450`|C2,C14,I.embed
T41|.|Embed: `/approvals/[id]` page — GET detail, render approval card (provenance, before-state, evidence, reason input, Approve/Deny buttons). CSRF token. Compact, self-scrolling|I.embed,V35,V34
T42|.|Embed: approve/deny POST handler — fetch to NOA API with `noa_session` cookie + CSRF. Poll `/action-requests/{id}` to terminal. Render receipt on completion|I.embed,V29,V34
T43|.|Embed: 401 state — explicit "cannot authenticate here" + "Sign in to NOA" button (new-tab link-out). ⊥ LDAP redirect inside iframe|V38,V42
T44|.|Embed: session plumbing — proxy `app/api/[...path]` or direct API origin with `Domain=.noa.internal` cookie|V40,I.embed
T45|.|Embed: framing headers — `Content-Security-Policy: frame-ancestors https://chat.noa.internal`. No `X-Frame-Options` needed|V37,V41
T46|.|Embed: CSRF token server-minted, signed, session-bound. Embedded in approval page, validated on POST|V39
T47|.|Admin web scaffold: `apps/admin-web` — Next.js 16, BIGSU dependencies (`@gio/bigsu-ui`, `@gio/bigsu-icons`), `.npmrc` for `bigsu.biznetgio.pt/registry/`. Hygiene: `.ts≤300`, `.tsx≤450`|C2,C14,I.admin-web
T48|.|Admin web: PORT from `noa-old/apps/web-bigsu/src/app/(protected)/admin/` — users, roles, audit, audit/tool-runs, whm, proxmox, pmg. Copy BIGSU components, ⊥ import from old repo. Include `AGENTS.md`, `CLAUDE.md`, `.claude/skills/bigsu/`, `docs/`|C13,I.admin-web
T49|.|Admin web: framing headers — `Content-Security-Policy: frame-ancestors 'none'`|V41
T50|.|Admin web: auth/session plumbing — proxy API calls, `noa_session` cookie. Login page redirect|I.admin-web
T51|.|Admin: user management CRUD — list, create, enable/disable, delete, assign roles|I.admin-api
T52|.|Admin: role management CRUD — list, create, delete, set tool permissions. `admin` role reserved|I.admin-api,V13
T53|.|Admin: token management — list tokens per user, mint (show-once plaintext), revoke|I.admin-api,V2
T54|.|Admin: server management — WHM/Proxmox/PMG CRUD + validate (SSH connect, fingerprint capture, TOFU refresh)|I.admin-api
T55|.|Admin: audit — tool runs list (filters: toolName, status, conversationRef, user, date range, cursor pagination), detail view (args redacted, result summary, timing)|I.admin-api,V45,V47
T56|.|Table surface for large READ results: `apps/web-embed` — route `/tables/[token]`, ⊥ `apps/api` (§8.8: same app, same origin rules, same framing headers). Renders full table. Tool result carries summary + URL. Used by `whm_list_accounts`, `pmg_whitelist_list`|V64,I.embed,T59
T57|.|LibreChat `librechat.yaml` entry: `mcpServers.noa` → `streamable-http` → `https://noa.internal/mcp`, `customUserVars.NOA_MCP_TOKEN` (`sensitive: true`), headers `Authorization: Bearer {{NOA_MCP_TOKEN}}` + `X-Noa-LibreChat-User: {{LIBRECHAT_USER_ID}}`. Operator-YAML registration|I.mcp,I.ext,V70,V71
T58|.|LibreChat agent config: system prompt with safety policy (preflight-first, approval gates, no fabrication, argument discipline, `\ui{}` emission instruction)|V71
T59|.|**GATE — blocks T32,T41–T46,T56 + selects V24/V37/V64 branch. Run before embed work starts, ⊥ after.** Pin exact LibreChat commit. Verify against THAT commit: (a) does `text/uri-list` still map to `src` render mode, (b) is `@mcp-ui/client` still the renderer or has #13831 (official `ext-apps` SDK) landed, (c) does any render site classify only `text/html;profile=mcp-app` as app-backed, (d) does cookie ride into the frame. PASS → path A (iframe). FAIL → path B (link-out primary, V25). Re-verify ∀ version bump|C17,C21,V24,V25,V37,V64
T60|.|Deployment: `Dockerfile` per app. `docker-compose.yml` for local dev (Postgres + API + admin-web + embed). Domain setup: `chat.`/`embed.`/`noa.internal` + cookie `Domain=.noa.internal`|C12,V40
T61|.|CI: lint + typecheck + test per app. Python: pytest + ruff. TypeScript: eslint + tsc + vitest + playwright. Conventional commits enforced|C14,V67,V68
T62|.|Docs: `README.md` (overview, setup, architecture), `ARCHITECTURE.md` (design decisions, topology), `AGENTS.md` (caveman communication, code standards)|-
T63|.|Tool: `noa_get_action_result(action_request_id)` (READ). Requester-match vs MCP-token identity. Foreign ∧ unknown → identical not-found shape. Returns status + receipt summary, redacted args|I.mcp,V27,V76
T64|.|Error envelope: ∀ error response body carries `request_id`, response carries `x-request-id` header. Shared exception handler, ⊥ per-route|V73
T65|.|Direct per-user tool grants → 410 `direct_tool_grants_disabled`. Role replacement preserves internal roles|V75,I.admin-api
T66|.|Emit `notifications/tools/list_changed` on role/permission change. Test: revoked tool → 403 at execution even when still in client's cached catalog (V1 backstop). Verify LibreChat honours notification → T59 checklist item|V14,V74,C23
T67|.|Rename Fernet env var → `NOA_SECRET_ENCRYPTION_KEY` in `core/config.py`, `.env.example`, Dockerfiles, `docker-compose.yml`, README. ⊥ `NOA_DB_SECRET_KEY` (misleading — encrypts server creds, ⊥ DB)|C7,V52
T68|.|Firewall no-backend guard: zero available backends → error `no_firewall_backend`. Test asserts ⊥ success-with-empty-gather. Fixes `noa-old` silent no-op|V57
T69|.|Proxmox crypt-verify guard: `_load_crypt_lib()` → `None` ⇒ receipt `verification_unavailable`, ⊥ silent pass. Test with libcrypt unavailable|V62
T70|.|**DECIDED by research 2026-08-04, ⊥ owner input needed** — record era `2025-06-18` + `fastmcp==3.4.5` in `pyproject.toml` + README + `ARCHITECTURE.md` with C23 rationale (LibreChat on SDK v1.x `^1.29.0`) so pin reads as decision, ⊥ accident. Record re-open trigger: LibreChat bumps to `@modelcontextprotocol/sdk` v2|C23,I.mcp

## §B BUGS

id|date|cause|fix
