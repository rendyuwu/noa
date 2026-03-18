# AGENTS.md (Project NOA)

This repo is a monorepo:
- `apps/api`: FastAPI backend (LDAP auth, RBAC, tools, Assistant Transport), Postgres persistence
- `apps/web`: Next.js frontend (assistant-ui) with a same-origin `/api/*` proxy to the backend

## Build / lint / test

### Postgres (dev)
From repo root:

```bash
docker compose up -d postgres
```

### API (apps/api)
Prereqs: Python 3.11+ and `uv`.

Setup deps:

```bash
cd apps/api
cp .env.example .env
uv sync
```

DB migrations:

```bash
uv run alembic upgrade head
```

Run the API:

```bash
uv run uvicorn noa_api.main:app --reload --port 8000
```

Lint/format (Python):

```bash
uv run ruff check src tests
uv run ruff format src tests
```

Tests:

```bash
uv run pytest -q
```

Run a single test:

```bash
uv run pytest -q tests/test_threads.py
uv run pytest -q tests/test_threads.py::test_threads_routes_initialize_is_idempotent_per_user_local_id
uv run pytest -q -k "rbac"
```

### Web (apps/web)
Prereqs: Node.js 20+ and npm.

Setup + dev server:

```bash
cd apps/web
cp .env.example .env.local
npm install
npm run dev
```

Build/typecheck + run production build:

```bash
npm run build
npm run typecheck
npm run start
```

Notes:
- No dedicated web lint/test scripts are configured yet (no ESLint/Jest/Vitest/Playwright config found).
- `npm run typecheck` currently runs `next build` (same as `npm run build`).

## Repo gotchas

- Do not commit secrets: `.env`, `.env.local`, `.env.*` are gitignored (except `.env.example`).
- API list/set env vars are JSON arrays; examples:
  - `AUTH_BOOTSTRAP_ADMIN_EMAILS=["admin@example.com"]`
  - `API_CORS_ALLOWED_ORIGINS=["http://localhost:3000"]`
- Browser code should never call FastAPI directly; use same-origin `/api/...`.
  - `apps/web/app/api/[...path]/route.ts` proxies to `NOA_API_URL` server-side.

## Code style & conventions

### General
- Match existing style in the touched file (don't reformat unrelated code).
- Prefer small, focused changes; keep edits scoped to one app when possible.
- Keep user-facing error strings stable; clients/tests often assert on `detail` messages.

### Python (apps/api)
Imports/formatting:
- Group imports: standard library, third-party, then `noa_api.*`; keep one blank line between groups.
- Prefer `from __future__ import annotations` in new modules (matches most of the codebase).
- Use type hints everywhere; prefer `X | None` unions (Python 3.11+).

Pydantic / request+response models:
- Define request/response shapes as `BaseModel` in route modules.
- When API JSON uses camelCase, use `Field(alias="...")` and set `model_config = {"populate_by_name": True}`.
- Normalize optional strings with `@field_validator(..., mode="before")` + `.strip()`; treat empty strings as `None`.

Error handling:
- Keep domain/service errors as typed exceptions; translate to HTTP in the route layer.
- When mapping errors, use `raise HTTPException(status_code=..., detail="...") from exc`.
- Status codes: `401` invalid credentials/token; `403` inactive/pending approval; `404` missing resource; `409` conflict.

DB / SQLAlchemy async:
- Use `AsyncSession`; after writes use `await session.flush()`.
- Handle commit/rollback in dependency generators (`try: yield ...; commit; except: rollback`).
- On `IntegrityError`, rollback first; only "recover" when the operation is intended to be idempotent.

Tools + approval gate:
- Tool metadata lives in `noa_api.core.tools.registry`.
- Every tool is classified as `ToolRisk.READ` or `ToolRisk.CHANGE`.
- Any mutating behavior must be `CHANGE` and go through the persisted approval flow (`request_approval` -> approve/deny).

Assistant workflows:
- Approval-oriented tool families register workflow templates in `apps/api/src/noa_api/core/workflows/registry.py`; avoid adding new family-specific branches in `apps/api/src/noa_api/core/agent/runner.py` when a template hook can own the behavior.
- Shared workflow contract lives in `apps/api/src/noa_api/core/workflows/types.py`.
- WHM is the reference implementation in `apps/api/src/noa_api/core/workflows/whm.py`.
- Keep family-specific operational presentation in workflow templates: `build_reply_template(...)` owns conversational outcome wording, and `build_evidence_template(...)` owns structured before/after, verification, and batch-outcome evidence.
- Treat `build_before_state(...)` as a compatibility shim; new workflow families should prefer `build_evidence_template(...)` and let the registry derive approval `beforeState` from the `before_state` evidence section.
- When adding a new workflow family, set `ToolDefinition.workflow_family`, implement a template module, register it, and keep the existing web workflow UI generic unless the user explicitly asks for a new surface.
- Canonical workflow UI/docs reference: `docs/assistant/workflow-templates.md`.

### TypeScript / Next.js (apps/web)
Imports/formatting:
- Order imports: React/Next, third-party, then internal `@/`.
- Use `import type { ... }` for type-only imports.
- Match existing formatting: 2-space indent, semicolons, double quotes, trailing commas.

Client/server boundaries:
- Add `"use client"` only when needed (hooks, `window`, client-only APIs).
- Prefer server-side proxying via Next route handlers for backend calls.

API calls + errors:
- From the browser, call same-origin paths (`/api/...`) only.
- Use `fetchWithAuth()` + `jsonOrThrow()` (`apps/web/components/lib/fetch-helper.ts`) for authenticated requests.
- Handle auth expiry via the existing pattern (401 triggers `clearAuth()` and redirects to `/login`).

Naming:
- Components: `PascalCase`; hooks: `useX`; types: `PascalCase` (`AuthUser`).
- Files/dirs: kebab-case (matches `claude-workspace.tsx`, `thread-list-adapter.ts`).
- Prefer `type` for object shapes; use `interface` mainly for module augmentation (see `apps/web/assistant.config.ts`).

## Cursor / Copilot rules
- Cursor rules: none found in `.cursor/rules/` or `.cursorrules`.
- Copilot rules: none found in `.github/copilot-instructions.md`.
