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
- C21. UPSTREAM DRIFT — C17 chain measured 2026-08-06 at pin `45cc53c4`, ⊥ inherited from mcp-ui docs. Source-read outcome (R10–R17): `text/uri-list` ⇒ iframe `src` + `sandbox="allow-scripts allow-same-origin"`, zero proxy indirection ⇒ first-party cookie rides ⇒ path A = **PASS-candidate, ⊥ PASS** — live cookie-into-frame run at that pin still owed (T59). LibreChat owns ⊥ mimeType logic; whole behaviour comes from `@mcp-ui/client` `^5.7.0` (lockfile-exact `5.7.0`). Drift source = PR #13831 "MCP Apps support" — OPEN **draft**, base `dev`, ⊥ merged, ⊥ in any release tag (R15): swaps `@mcp-ui/client` → `@modelcontextprotocol/ext-apps ^1.7.4`, classifies ONLY `text/html;profile=mcp-app` as app-backed, plain `srcDoc` → `sandbox=""` inert, grants `allow-same-origin` only when sandbox on dedicated origin (`VITE_MCP_SANDBOX_URL`). MCP Apps spec = SEP-1865, MVP scope = `text/html;profile=mcp-app` only; `text/uri-list` listed "deferred from MVP" (R16) ⇒ if #13831 becomes default render path, `text/uri-list` has ⊥ render path at all — ⊥ merely "proxy breaks same-origin". CORRECTION: ext-apps "#318" = OPEN discussion **issue**, ⊥ spec text (R16). T59 = **blocking gate** ∀ V22,V24,V37,V39,V40,V44,V64,V80 + T32,T41–T46,T56. Re-verify ∀ LibreChat bump.
- C22. Never-implement boundary (DECISIONS §6.2 — management policy, ⊥ technical): `whm_change_contact_email`, `whm_change_primary_domain`, `proxmox_move_vms_between_pools`, `proxmox_preflight_move_vms_between_pools`, `proxmox_get_user_by_email`, `whm_check_binary_exists`, `whm_firewall_denylist_add_ttl`. ⊥ port, ⊥ expose, ⊥ re-add. Re-add = policy decision, ⊥ agent call.
- C23. MCP protocol era = **handshake (`2025-06-18`)**, ⊥ sessionless `2026-07-28`. DECIDED 2026-08-04 by evidence, ⊥ preference. Driver = C24: LibreChat = sole client, `packages/api/package.json` declares `@modelcontextprotocol/sdk: ^1.29.0` ⇒ v1.x line ⇒ `LATEST_PROTOCOL_VERSION = "2025-06-18"`, `initialize` handshake + `Mcp-Session-Id`. Serving `2026-07-28` to a v1.x client buys nothing. Server dep = `fastmcp==3.4.5` (stable 2026-07-27, `requires_python >=3.10`, satisfies C1). ⊥ `fastmcp` 4.x — beta only (4.0.0b1, needs `--pre`), removes server-initiated sampling/roots, drops 3.x shims, relocates `fastmcp.server.auth.*` ⇒ breaks T11 `TokenVerifier` for zero gain. Re-open when LibreChat ships SDK v2 — even then v2 emits `2026-07-28` only on explicit opt-in. Pin-verified 2026-08-06: `"@modelcontextprotocol/sdk": "^1.29.0"` still declared at `packages/api/package.json:112` (R11). ?PREMISE GAP: the "v1.x ⇒ `LATEST_PROTOCOL_VERSION="2025-06-18"`" step ⊥ read from TS SDK source (R9) — T71 closes it. Server side ⊥ blocked either way: `mcp==1.29.0` SUPPORTED = `2024-11-05`,`2025-03-26`,`2025-06-18`,`2025-11-25` (R8) ⇒ `2025-06-18` servable now, negotiated per client `initialize`. `2026-07-28` ∉ that list ⇒ era bump = SDK bump, ⊥ config flip (bounds V74's future path).

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

## §R RESEARCH

Sourced findings. `?` prefix = unverified. Local paths = installed package source (primary), relative to `.venv/lib/python3.11/site-packages/`.

id|topic|finding|source
R1|fastmcp auth base|`class TokenVerifier(AuthProvider)` in `fastmcp.server.auth`; abstract `async def verify_token(self, token: str) -> AccessToken \| None`, `None` = reject. Ctor `(base_url=None, required_scopes=None, resource_base_url=None)` — `resource_server_url` ⊥ exist|`fastmcp/server/auth/auth.py:366,408,373-378` (fastmcp==3.4.5)
R2|AccessToken shape|Return fastmcp `AccessToken`, ⊥ SDK's. Required `token: str`, `client_id: str`, `scopes: list[str]`; optional `expires_at: int \| None`, `resource`, `subject`, `claims: dict` (fastmcp default `{}`). SDK middleware rejects when `expires_at < int(time.time())` ⇒ expiry enforced upstream|`fastmcp/server/auth/auth.py:54-57`; `mcp/server/auth/provider.py:39-46`; `mcp/server/auth/middleware/bearer_auth.py:70-71`
R3|verifier wiring|`FastMCP(..., auth=<TokenVerifier instance>)` — keyword-only, instance passed direct, ⊥ wrapper (TokenVerifier ⊂ AuthProvider). `RemoteAuthProvider` only if RFC 9728 metadata routes wanted|`fastmcp/server/server.py:329,424`
R4|headers inside `verify_token`|`verify_token` receives token string only, BUT `RequestContextMiddleware` inserted outermost (`middleware.insert(0, …)`) ahead of `AuthenticationMiddleware` ⇒ `get_http_request()`/`get_http_headers()` DO work inside `verify_token`. LIVE-VERIFIED 2026-08-06: `X-Noa-LibreChat-User` read there, 200 ⇒ TOFU binding may live in verifier|`fastmcp/server/http.py:404,361-383`; `mcp/server/auth/middleware/bearer_auth.py:54-65`
R5|header helper caveat|`get_http_headers()` default excludes `authorization` ∧ `mcp-session-id` — need `include={"authorization"}`. Custom `x-noa-librechat-user` returned, key lowercased. Identity in tools: `get_access_token()`, DI `CurrentAccessToken()`, `TokenClaim(name)` from `fastmcp.server.dependencies`|`fastmcp/server/dependencies.py:430-448,467,1299,1356`
R6|ASGI mount|`http_app(path=None, middleware=None, json_response=None, stateless_http=None, transport="http", event_store=None, retry_interval=None, host_origin_protection=None, allowed_hosts=None, allowed_origins=None) -> StarletteWithLifespan`. `combine_lifespans(*lifespans)` EXISTS at `fastmcp.utilities.lifespan` (⊥ in `fastmcp.__all__`); documented pattern `FastAPI(lifespan=combine_lifespans(app_lifespan, mcp_app.lifespan))` + `app.mount("/mcp", mcp_app)`|`fastmcp/server/mixins/transport.py:370-382`; `fastmcp/utilities/lifespan.py:12-14,26-37`
R7|v3 removed ctor kwargs|`FastMCP(stateless_http=…)`, `json_response`, `streamable_http_path`, `sse_path`, `host`, `port`, `debug`, `log_level`, `tool_serializer`, `include_tags`/`exclude_tags` raise `TypeError` — pass to `http_app()`/`run_http_async()` or `FASTMCP_*` env. `JWTVerifier`/`StaticTokenVerifier` behind lazy `__getattr__` ⇒ import from `fastmcp.server.auth.providers.jwt` for static analysis|`fastmcp/server/server.py:148-174`; `fastmcp/server/auth/__init__.py:34-55`
R8|server protocol + session id|`mcp==1.29.0` (Python): `LATEST_PROTOCOL_VERSION="2025-11-25"`, `DEFAULT_NEGOTIATED_VERSION="2025-03-26"`, `SUPPORTED_PROTOCOL_VERSIONS=["2024-11-05","2025-03-26","2025-06-18","2025-11-25"]`. `Mcp-Session-Id` minted by default (`stateless_http=False`, `uuid4().hex`) — LIVE-VERIFIED. `stateless_http=True` ⇒ no header, no event store, routes drop GET|`mcp/types.py:27,35`; `mcp/shared/version.py:3`; `mcp/server/streamable_http_manager.py:288,205`
?R9|C23 premise gap|C23 claim "TS SDK v1.x ⇒ `LATEST_PROTOCOL_VERSION = "2025-06-18"`" ⊥ verified — read Python SDK, ⊥ `@modelcontextprotocol/sdk` TS source. `2026-07-28` ∉ R8 SUPPORTED list. Pin still operable ∵ `2025-06-18` ∈ server SUPPORTED|UNVERIFIED — T71
R10|LibreChat pin candidate|`main` HEAD `45cc53c40b47645b887c3bb996168e06aaa83f4c` (2026-08-06T02:25Z). Latest tag `v0.8.7` (2026-06-24) but `prerelease=true`|github.com/danny-avila/LibreChat/commit/45cc53c4
R11|renderer deps at pin|`client/package.json:41` `"@mcp-ui/client": "^5.7.0"`, lockfile exact `5.7.0`. `packages/api/package.json:112` `"@modelcontextprotocol/sdk": "^1.29.0"` (C23 cite confirmed). Code search `ext-apps` on default branch = 0 hits|github.com/danny-avila/LibreChat/blob/45cc53c4/client/package.json#L41
R12|`text/uri-list` still `src`|LibreChat owns ⊥ mimeType logic — 3 render sites delegate to `UIResourceRenderer` (`ToolCallInfo.tsx:167`, `MCPUIResource.tsx:40`, `UIResourceCarousel.tsx:111`). mcp-ui 5.7.0: `text/uri-list` ⇒ `externalUrl` ⇒ `iframeRenderMode:'src'`; `text/html` ⇒ `rawHtml` ⇒ `srcDoc`|mcp-ui `v5.7.0/sdks/typescript/client/src/components/UIResourceRenderer.tsx#L20-25`; `utils/processResource.ts:110,117,126`
R13|sandbox value at pin|src path: `mergeSandboxPermissions(sandboxPermissions ?? '', 'allow-scripts allow-same-origin')`. Effective: `ToolCallInfo` ⇒ `allow-scripts allow-same-origin`; `MCPUIResource` ⇒ `allow-popups allow-scripts allow-same-origin`. **`allow-forms` ABSENT ∀ paths** ⇒ native `<form>` submit blocked in-frame, JS `fetch` POST works. srcDoc branch omits `allow-same-origin` deliberately|mcp-ui `v5.7.0/.../HTMLResourceRenderer.tsx#L147-153,183-184`
R14|proxy / off-origin risk|NONE at pin: `proxy?: string` opt-in, LibreChat passes none, no default, no dedicated sandbox origin. mcp-ui self-guards same-host proxy ("proxy origin must not be the same as the host origin"). For `externalUrl` a proxy IS forwarded if ever set|mcp-ui `v5.7.0/.../utils/processResource.ts#L85-110`
R15|PR #13831 state|OPEN + **draft**, base `dev` (⊥ `main`), head `feat/mcp-apps-support`, 52 commits/56 files, updated 2026-08-05, ⊥ in any tag. Swaps `@mcp-ui/client ^5.7.0` → `@modelcontextprotocol/ext-apps ^1.7.4`; `isMcpAppResource` = mimeType includes `profile=mcp-app`; plain `srcDoc` ⇒ `sandbox=""`, app-backed ⇒ `sandbox="allow-scripts allow-forms"`; `mcp-sandbox.html` adds `allow-same-origin` only when `dedicatedOrigin` (`VITE_MCP_SANDBOX_URL`). Related #11799 OPEN, stale|github.com/danny-avila/LibreChat/pull/13831/files
R16|MCP Apps spec scope|`text/html;profile=mcp-app` ONLY — "MVP supports only `text/html;profile=mcp-app` (rawHtml), with other types explicitly deferred"; `text/uri-list` sits under "Content Types (deferred from MVP)". Spec = SEP-1865, extension per SEP-1724. CORRECTION: "#318" = ext-apps **issue** #318 "Thoughts about a mimeType equivalent to text/uri-list", OPEN discussion, ⊥ spec text|modelcontextprotocol/ext-apps `specification/draft/apps.mdx#L277`; ext-apps issues/318
R17|T59 evidence verdict|PASS-candidate at pin `45cc53c4` + lockfile `@mcp-ui/client@5.7.0`: `text/uri-list` ⇒ iframe `src` at NOA origin, `allow-same-origin` present, zero proxy ⇒ first-party cookie rides. ⊥ closes T59 — live cookie-into-frame run at pin still owed. Caveats = R13 (⊥ `allow-forms`) + R15 (draft kills uri-list)|mcp-ui `v5.7.0/.../HTMLResourceRenderer.tsx#L147-153`
R18|logout ! be server-side|OWASP Session CS §Session Expiration: "must take active actions to invalidate the session on both sides, client and server. The latter is the most relevant and mandatory". ASVS V3.3.1 (L1/L2/L3, CWE-613): "logout and expiration invalidate the session token" ⇒ cookie-clear-only logout (V6) fails V3.3.1|cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html §Session Expiration; OWASP ASVS 4.0 V3.3.1
R19|account-disable ⊥ normative|Zero source found requiring termination of LIVE sessions on account disable. NIST SP 800-63B-4 covers authenticators ⊥ sessions; OWASP nearest = §Reauthentication After Risk Events (disable ⊥ listed) ⇒ per-request `is_active` re-read = common practice, ⊥ normative backing, ∧ ⊥ substitute for R18|pages.nist.gov/800-63-4/sp800-63b.html; OWASP Session CS §Reauthentication After Risk Events
R20|session TTL numbers|NIST SP 800-63B-4 §2.2.3 (AAL2, final 2025-07-31): overall reauth SHOULD ≤ 24h, inactivity SHOULD ≤ 1h; §5.2 "When either timeout expires, the session SHALL be terminated"; §5.1.1 cookie expiry "SHALL NOT be relied upon to enforce session timeouts". OWASP: idle 15–30 min low-risk / 2–5 min high-value, absolute 4–8h. Current `AUTH_JWT_ACCESS_TOKEN_TTL_SECONDS=3600` ∈ range|pages.nist.gov/800-63-4/sp800-63b.html §2.2.3,§5.1.1,§5.2; OWASP Session CS §Session Expiration
R21|CONFLICT — denylist|OWASP JWT CS §JWT revocation: denylist = "workaround for the 'stateless session' invalidation problem", key `(jti, iss)`, raw-JWT/SHA-256 key "not safe … denylist bypass through JWT malleability", + "consider whether there is a better solution" (Token Status List, session-bound nonce, short exp, DPoP). OWASP REST CS §JWT: on explicit termination `jti` "should be submitted to a denylist". BOTH logged, ⊥ averaged|OWASP JSON_Web_Token_Cheat_Sheet §JWT revocation; OWASP REST_Security_Cheat_Sheet §JWT
R22|short TTL + rotation|Only mechanism with hard normative language: RFC 9700 §2.2.2 public-client refresh tokens "MUST be sender-constrained or use refresh token rotation"; §4.14 short access lifetime "reduc[es] the potential impact of access token leakage"; §4.14.2 rotation invalidates prior refresh token — revoke-on-logout = **MAY**, refresh-only. RFC 7009 §2: refresh revocation MUST, access SHOULD. RFC 6819 §3.1: "Implementing token revocation is more difficult with assertions than with handles"|datatracker.ietf.org/doc/html/rfc9700 §2.2.2,§4.14,§4.14.2; rfc7009 §2; rfc6819 §3.1
?R23|no propagation window|Zero normative max revocation-propagation window ∧ zero access-token-lifetime digits in RFC 9700/6819/7009. RFC 8725 (JWT BCP) silent on `jti`/replay. Per-user token-version / session-epoch counter ⊥ present in any normative source ⇒ common practice only|rfc-editor.org/rfc/rfc8725.txt; pages.nist.gov/800-63-4/sp800-63b.html §5.3
R24|PyJWT 2.13.0|VERIFIED at tag 2.13.0: `leeway: float \| timedelta = 0` default; `if iat > (now + leeway): raise ImmatureSignatureError("The token is not yet valid (iat)")` (`InvalidIssuedAtError` = non-numeric case only) ⇒ V79 correct. `_validate_jti` = type check only (`InvalidJTIError` if ⊥ str), zero revocation ⇒ denylist wholly caller's. jti/sub validation landed 2.10.0 (#1005); iat check 2.6.0 (#794), touched 2.7.0 (#847)|raw.githubusercontent.com/jpadilla/pyjwt/2.13.0/jwt/api_jwt.py; pyjwt.readthedocs.io changelog

## §V INVARIANTS

### Auth & Tokens

- V1. ∀ MCP req → resolve `users.id` + re-check `is_active`. `tools/list` RBAC-filtered per user. `tools/call` re-checks permission.
- V2. MCP auth = per-user bearer token, minted by NOA admin. Token ↔ `users.id`. Hashed at rest (SHA-256). Plaintext shown once at mint. Revoke = delete row. ⊥ logged.
- V3. Token TOFU binding: minted `librechat_user_id=NULL`; NULL + `X-Noa-LibreChat-User` header present → bind; non-NULL → header ! present & equal else 401. Absent header → 401 ∀ states (bound ∧ unbound) ∵ C24 LibreChat-only. 401 body names cause: `librechat_user_header_missing` \| `librechat_user_mismatch`. ⊥ unbound-token client path.
- V4. Token revalidation: `last_ldap_check_at` on row, staleness interval. LDAP down → fail closed. Cascade revoke on admin disable + LDAP path.
- V5. Resolve MCP identity in exactly one function, called ∀ MCP request. Auth mechanism swap = one file.
- V6. Login sets httpOnly `noa_session` cookie (SameSite=Lax, Domain=`.noa.internal`, Path=/). Logout clears with max-age=0. Logout idempotent without auth. Session JWT ⊥ revocable pre-`exp` — ⊥ `jti`, ⊥ denylist ⇒ logout kills the *cookie*, ⊥ the token: a held copy verifies until `exp`. V4 cascade-revoke-on-disable = `mcp_tokens` only (T11), ⊥ session equivalent exists. ⇒ T8/T9 ! re-read `users.is_active` ∀ authenticated request — that re-read is the ONLY thing bounding a disabled admin's session, ⊥ an optimization. Unbounded window otherwise = `AUTH_JWT_ACCESS_TOKEN_TTL_SECONDS` (default 3600). Session-kill-on-disable (denylist \| `jti` \| shorter TTL) = OPEN owner decision, ⊥ agent call. Research feeding that decision: R18 (server-side invalidation on logout = mandatory per OWASP + ASVS V3.3.1 ⇒ cookie-clear-only fails it), R19 (⊥ normative mandate to kill *live* sessions on account disable; per-request `is_active` re-read = common practice, ⊥ substitute for R18), R20 (numbers: NIST AAL2 reauth SHOULD ≤ 24h ∧ inactivity SHOULD ≤ 1h; OWASP absolute 4–8h ⇒ 3600s ∈ range), R21 (denylist = CONFLICTING OWASP guidance, key `(jti, iss)`, ⊥ raw-JWT key), R22 (short TTL + refresh rotation = only mechanism with MUST-level text). ⇒ logout-invalidation gap is a spec-acknowledged deviation, ⊥ an oversight.
- V79. Session verify clock discipline: leeway = 0 (PyJWT default, ⊥ passed). `exp` 1s past → expired; `iat` 1s future → invalid (PyJWT `iat > now + leeway` ⇒ `ImmatureSignatureError`). Holds ∵ single API process — mint & verify share one clock. **Revisit trigger: >1 API replica.** Then drift breaks the MINT side first: minter clock ahead ⇒ verifier rejects a token the instant login returns it (⊥ merely logouts near expiry). Fix then = explicit `leeway`, ⊥ silent widening. Future-`iat` rejection = `PyJWT==2.13.0` behaviour, moved across 2.x releases ⇒ pinned + tested, ⊥ assumed. VERIFIED at tag 2.13.0 (R24): `leeway` default `0`; `iat > (now + leeway)` → `ImmatureSignatureError` (`InvalidIssuedAtError` = non-numeric `iat` only); check landed 2.6.0 (#794), touched 2.7.0 (#847). `_validate_jti` = type check only ⇒ ∀ revocation logic = ours (bears on V6).
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
- V80. In-iframe approve/deny ! POST via JS `fetch` — ⊥ native `<form>` submit, ⊥ `<form action>` fallback. ∵ sandbox at pin `45cc53c4` = `allow-scripts allow-same-origin` (+`allow-popups` on one render site) with **`allow-forms` ABSENT ∀ render paths** (R13) ⇒ a form submit dies silently in-frame. Cookie + CSRF discipline unchanged (V22,V39). Link-out path (V25) = top-level document ⇒ unaffected, forms fine there. Re-check sandbox string ∀ T59 re-verify.

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
- V81. Mounted-app path guards ! compare **mount-relative** path — `scope["path"]` minus `scope["root_path"]`. Starlette `Mount` extends `root_path`, ⊥ rewrites `path` (mirrors `starlette._utils.get_route_path`) ⇒ raw-`scope["path"]` guard matches standalone ∧ never matches mounted. Failure silent: guard falls through, SDK bare `invalid_token` answers same 401 status, only body differs ⇒ status-code test ⊥ catch it (B1). Helper = `mount_relative_path()` in `noa_api/mcp_request_auth.py`, ⊥ `starlette._utils` (private module).

### Firewall TTL

- V77. `whm_firewall_release_and_allow` ! require `duration_minutes` (integer, 1–525600). ⊥ permanent allowlist entry, ⊥ server-side default — operator states duration in chat ("5 min", "2 hours", "5 days"), LLM converts to minutes as ordinary tool arg (⊥ a reason, C8 unaffected). Receipt after-state ! show resolved expiry timestamp. Preserves `noa-old` `whm_firewall_allowlist_add_ttl` bound — merge (§6.5) ⊥ drop TTL.
- V78. `whm_firewall_allowlist_remove` = undo path, ⊥ TTL param ∵ removal is immediate.

## §T TASKS

id|status|task|cites
T1|x|git init + `.gitignore` (Python, Node, env, IDE, OS). Commit as "chore: init repo"|-
T2|x|scaffold monorepo structure: `apps/api/`, `apps/admin-web/`, `apps/web-embed/`, `core/`, `docs/`. Root `pyproject.toml` (workspace), `.env.example`, `README.md`, `AGENTS.md`|C12,C14
T3|x|`apps/api/` scaffold: FastAPI + FastMCP skeleton, `pyproject.toml` deps pinned exact: `fastmcp==3.4.5` (⊥ 4.x per C23), fastapi, uvicorn, sqlalchemy[asyncio], asyncpg, alembic, pydantic, pydantic-settings, structlog, orjson, cryptography, PyJWT, python-ldap, `pgpy>=0.6.0`, asyncssh, httpx. `requires-python = ">=3.11,<3.13"`|C1,C3,C7,C23
T4|x|Postgres schema v1: `users`, `roles`, `user_roles`, `role_tool_permissions`, `mcp_tokens`, `whm_servers`, `proxmox_servers`, `pmg_servers`. Alembic initial migration|C3,V1,V2,V3,V11
T5|x|`core/config.py`: pydantic-settings, all env vars (DB, LDAP, Fernet key, JWT secret, yopass, token TTL, LDAP revalidate interval, per-user concurrency cap, pending TTL, etc.)|C9,C7
T6|x|LDAP auth service: `LDAPService` with service-account bind + search. `user_exists_and_enabled(email)`. Dev bypass mode|C4,V7
T7|x|JWT service: mint, verify, httpOnly cookie set/clear. SameSite=Lax, `Domain=.noa.internal`|V6,V8
T8|x|Login flow: `POST /auth/login` → LDAP auth → JWT cookie. `POST /auth/logout` → clear. `GET /auth/me`. Rate limiter. Build `JWTService` ONCE at app startup (lifespan/module singleton), ⊥ request-scoped dependency ∵ algorithm-allowlist + key-length guards raise at construction — per-request build turns a config error into a 500 on first login, ⊥ a boot failure. `GET /auth/me` + ∀ session-authed route ! re-read `users.is_active` (V6: ⊥ token revocation exists)|V6,V7,V8,V9,V79
T9|x|RBAC engine: `role_tool_permissions` CRUD, `get_permitted_tools(user_id)`, admin bypass for known tools, disabled-user zero-permissions. Disable ! take effect on the live session's next request ∵ V6 (⊥ session revocation) ⇒ permission resolution reads the row, ⊥ trusts the cookie's claims|V6,V10,V11,V12,V13,V14
T10|x|Token management: `mcp_tokens` model + migration (id, user_id FK, token_hash, label, librechat_user_id nullable, last_used_at, last_ldap_check_at, created_at, expires_at). Mint: generate crypto token, hash SHA-256, return plaintext once. List: prefix + label + timestamps. Revoke: delete row|C5,V2
T11|x|Token verification: subclass `fastmcp.server.auth.TokenVerifier` (R1) — `async def verify_token(self, token: str) -> AccessToken \| None`, `None` = reject. Lookup `token_hash` in DB → return **fastmcp** `AccessToken(token=…, client_id=str(user.id), scopes=[…], expires_at=…, claims={…})` — `client_id`+`scopes` REQUIRED, SDK enforces `expires_at` itself (R2). TOFU binding runs INSIDE `verify_token` via `get_http_headers()` (R4, live-verified): NULL + header → bind; non-NULL → match else 401. LDAP staleness revalidation. Cascade revoke on admin disable|C20,V2,V3,V4,R1,R2,R4
T12|x|`resolve_mcp_identity(context, *, presented_token=None) → McpIdentity` (`noa_api/mcp_request_auth.py`): single HTTP-side function — extract `Authorization: Bearer <token>`, hash, DB lookup, TOFU binding, LDAP revalidation, `is_active` check. AS BUILT ≠ this line, 2 deviations: (a) ⊥ `request` param — `verify_token` gets no request arg, R4 contextvar serves both callers; a `Request` param ⇒ middleware ∧ verifier reach one function 2 ways. (b) → `McpIdentity` (T11 shape, ⊥ token material), ⊥ `users` row. 401 shapes named in body by `McpAuthErrorMiddleware`, ⊥ by `verify_token` (R2: ⊥ body hook) — middleware inside `AuthenticationMiddleware`, outside route, reads refusal stashed on ASGI scope ⇒ 1 resolve/req. Shapes: missing header, invalid token, expired, bound mismatch. Rate-limit failed auth keys = `x-noa-librechat-user` ∧ bearer SHA-256, ⊥ source IP (in-cluster addr ⊥ stable; C24 ⇒ IP block = fleet outage). Counted: `mcp_token_invalid`, `librechat_user_mismatch` ONLY. Store = `login_rate_limits` new scopes, ⊥ migration; math shared via `AttemptLimiter`. Header reads: `get_http_headers()` default STRIPS `authorization` ∧ `mcp-session-id` ⇒ pass `include={"authorization"}`; custom keys lowercased (`x-noa-librechat-user`) (R5). In-tool identity = `current_mcp_identity()` over `get_access_token()`, ⊥ re-parse bearer|V1,V4,V5,V9,V66,V73,R4,R5
T13|x|FastMCP server: `mcp = FastMCP("NOA", auth=<TokenVerifier instance>)` — `auth=` keyword-only, instance direct, ⊥ provider wrapper (R3). Mount: `mcp_app = mcp.http_app(path="/")` (transport default `"http"`) + `app.mount("/mcp", mcp_app)` + `from fastmcp.utilities.lifespan import combine_lifespans` → `FastAPI(lifespan=combine_lifespans(app_lifespan, mcp_app.lifespan))` (R6 — exact path, ⊥ in `fastmcp.__all__`). ⊥ pass `stateless_http`/`json_response`/`host`/`port`/`log_level` to `FastMCP()` — `TypeError` in v3, they belong on `http_app()` ∨ `FASTMCP_*` env (R7). Session default ON: `Mcp-Session-Id` minted unless `stateless_http=True` (R8). Mount ! carry T12: `auth=NoaTokenVerifier(context=build_mcp_auth_context(...))` + `http_app(path=…, middleware=[Middleware(McpAuthErrorMiddleware, mcp_path=…)])` — ⊥ middleware ⇒ V3 named bodies ⊥ ship, SDK bare `invalid_token` stands. AS BUILT ≠ this line, 3 deviations: (a) ⊥ module singleton — `build_mcp_server(*, auth)` + `build_mcp_http_app(*, auth_context)` in `noa_api/mcp_server.py`, one server per app ∵ `create_app()` runs many times in suite; `get_mcp_server()` dropped; T19+ register tools inside `build_mcp_server`. (b) `create_app()` ! own `AppRuntime` (settings, engine, session factory, `LDAPService`) ∵ `http_app()` snapshots `self.auth` at build time (`fastmcp/server/mixins/transport.py:419`) ⇒ verifier ! precede `FastAPI(...)`; lifespan keeps `JWTService` (T8 boot-failure rule) + engine dispose. (c) `MCP_APP_PATH="/"` ∵ Mount strips prefix (fastmcp default `/mcp` ⇒ `/mcp/mcp`) ⇒ endpoint answers `/mcp/`; bare `/mcp` → 307, method+body preserved — measured, ⊥ 404|V1,V3,V81,I.mcp,R3,R6,R7,R8,T12
T14|x|Port from `noa-old`: `core/remote_exec/` (SSH with banner stripping, `sudo -n`, host-key pinning, TOFU refresh)|C13,V55,V56,V69
T15|x|Port from `noa-old` branch `MCP`: `core/secrets/` (Fernet `SecretCipher`, `_generate_password()`, `_yopass_store()`, `docs/integrations/yopass.md`). ⊥ branch `staging` — `yopass.py`/`password.py`/yopass doc absent there. AS BUILT ≠ this line, 3 deviations: (a) settings INJECTED — `SecretCipher.from_settings(settings)`, `_yopass_store(..., *, settings)`; `noa-old`'s module-global `settings` import + `@lru_cache get_secret_cipher()` + module-level `encrypt_text`/`decrypt_text` wrappers dropped ∵ ⊥ settings singleton here (T5 `get_settings()` called once, in `build_runtime`); T54 builds 1 cipher on `AppRuntime`. (b) errors hoisted to `core/secrets/errors.py` ∧ subclass `NoaError` (⊥ bare `Exception` like `noa-old`) ∵ T54 decrypt raises out of an HTTP route ⇒ 1 handler shapes it (V73). Mapped: `SecretCryptoError` 500, `YopassError` 502, `YopassNotConfiguredError` 500. Codes verbatim: `yopass_not_configured`, `yopass_store_failed`. (c) `redaction.py` ⊥ ported — ⊥ named in this line, ⊥ caller until `tool_runs.args` redaction (T35,T47,T55); lands with them|C7,C13,C15,V48,V49,V50,V73
T16|x|Port from `noa-old`: WHM integration layer (`whm/integrations/ssh.py` — CSF + Imunify dual-backend, `asyncio.gather` parallel check)|C13,V57,V69
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
T41|.|Embed: `/approvals/[id]` page — GET detail, render approval card (provenance, before-state, evidence, reason input, Approve/Deny buttons). CSRF token. Compact, self-scrolling. Buttons ! be JS-`fetch` handlers, ⊥ `<form>` submit (V80)|I.embed,V35,V34,V80
T42|.|Embed: approve/deny POST handler — JS `fetch` to NOA API with `noa_session` cookie + CSRF (⊥ form submit, V80/R13). Poll `/action-requests/{id}` to terminal. Render receipt on completion|I.embed,V29,V34,V80
T43|.|Embed: 401 state — explicit "cannot authenticate here" + "Sign in to NOA" button (new-tab link-out). ⊥ LDAP redirect inside iframe|V38,V42
T44|.|Embed: session plumbing — proxy `app/api/[...path]` or direct API origin with `Domain=.noa.internal` cookie|V40,I.embed
T45|.|Embed: framing headers — `Content-Security-Policy: frame-ancestors https://chat.noa.internal`. No `X-Frame-Options` needed|V37,V41
T46|.|Embed: CSRF token server-minted, signed, session-bound. Embedded in approval page, validated on POST. Carried in `fetch` body ∨ header — ⊥ hidden form field (V80)|V39,V80
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
T57|.|LibreChat `librechat.yaml` entry: `mcpServers.noa` → `streamable-http` → `https://noa.internal/mcp`, `customUserVars.NOA_MCP_TOKEN` (`sensitive: true`), headers `Authorization: Bearer {{NOA_MCP_TOKEN}}` + `X-Noa-LibreChat-User: {{LIBRECHAT_USER_ID}}`. Operator-YAML registration. URL ! carry trailing slash `https://noa.internal/mcp/` ∵ T13 mount answers there; bare `/mcp` costs a 307 per request (works, ⊥ free)|I.mcp,I.ext,V70,V71,T13
T58|.|LibreChat agent config: system prompt with safety policy (preflight-first, approval gates, no fabrication, argument discipline, `\ui{}` emission instruction)|V71
T59|.|**GATE — blocks T32,T41–T46,T56 + selects V24/V37/V64 branch. Run before embed work starts, ⊥ after.** Pin candidate = `45cc53c40b47645b887c3bb996168e06aaa83f4c` (R10; tag `v0.8.7` = `prerelease`). Source-read items (a)–(c) + (e) ANSWERED at that pin by R11–R16: (a) `text/uri-list` ⇒ `src` YES, (b) `@mcp-ui/client@5.7.0` still renderer ∧ #13831 unmerged draft on `dev`, (c) `profile=mcp-app` classification absent at HEAD, (e) sandbox = `allow-scripts allow-same-origin`, **⊥ `allow-forms`** ⇒ V80. STILL OWED: (d) live cookie-into-frame run against a NOA-served `text/uri-list` resource at that pin, (f) does LibreChat honour `notifications/tools/list_changed` (V74/T66). PASS → path A (iframe). FAIL → path B (link-out primary, V25). Re-verify ∀ version bump|C17,C21,V24,V25,V37,V64,V80,R10,R17
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
T70|.|**DECIDED by research 2026-08-04, ⊥ owner input needed** — record era `2025-06-18` + `fastmcp==3.4.5` in `pyproject.toml` + README + `ARCHITECTURE.md` with C23 rationale (LibreChat on SDK v1.x `^1.29.0`) so pin reads as decision, ⊥ accident. Record re-open trigger: LibreChat bumps to `@modelcontextprotocol/sdk` v2. Cite R8 (server SUPPORTED list) + R11 (pin-verified dep) — ⊥ R9's unread step|C23,I.mcp,R8,R11
T71|.|Close R9: read `@modelcontextprotocol/sdk` v1.29.0 TS source (`node_modules` ∨ npm tarball ∨ GitHub tag) for `LATEST_PROTOCOL_VERSION` + `SUPPORTED_PROTOCOL_VERSIONS`. Confirm ∨ correct C23 premise sentence, drop `?` on R9. Cheap, read-only, ⊥ blocking — but ⊥ ship C23 rationale text as fact until done|C23,R8,R9

## §B BUGS

id|date|cause|fix
B1|2026-08-07|`McpAuthErrorMiddleware` path guard compared raw `scope["path"]` to sub-app path; Starlette `Mount` extends `root_path`, ⊥ rewrites `path` ⇒ mounted at `/mcp` guard never matched, ∀ refusal fell to SDK bare 401 with empty body, V3 named codes (`librechat_user_header_missing` \| `librechat_user_mismatch`) vanished behind unchanged 401. Caught by T13 contract test, ⊥ shipped|V81
