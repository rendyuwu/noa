# Project NOA

AI operations workspace: chat UI + controlled tools.

The goal is a natural-language control center for infrastructure and operations.
The model interprets and proposes actions; the platform enforces permissions, approvals, and auditability.

Docs:
- `ARCHITECTURE.md`
- `docs/STATUS.md`
- `docs/plans/2026-03-09-project-noa-mvp-design.md`
- `docs/reports/2026-03-09-task-11-verification.md`

## Repo Layout

- `apps/api`: FastAPI backend (LDAP auth, RBAC, tools, Assistant Transport)
- `apps/web`: Next.js frontend (assistant-ui) with a same-origin `/api/*` proxy to the backend
- `docker-compose.yml`: Postgres (dev)

## Prerequisites

- Docker + Docker Compose
- Python 3.11+ and `uv` (API)
- Node.js 20+ and npm (web)

## Dev Quickstart

Run API and web in separate terminals.

### 1) Start Postgres

```bash
docker compose up -d postgres
```

### 2) Configure + run API

Create `apps/api/.env` from `apps/api/.env.example` (do not commit `.env`; it is gitignored).

```bash
cd apps/api
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run uvicorn noa_api.main:app --reload --port 8000
```

Notes:
- `AUTH_BOOTSTRAP_ADMIN_EMAILS` and `API_CORS_ALLOWED_ORIGINS` must be JSON arrays (see examples).
- `python-ldap` may require OS packages to build (Ubuntu example: `sudo apt-get install -y libldap2-dev libsasl2-dev libssl-dev`).

### 3) Configure + run web

Create `apps/web/.env.local` from `apps/web/.env.example`.

Notes:
- The browser never calls the FastAPI backend directly. The web app calls same-origin `/api/...`, and a Next route handler proxies those requests server-side.
- Configure the proxy with `NOA_API_URL=http://localhost:8000` (server-side; used by Next). `NEXT_PUBLIC_API_URL` is a legacy fallback; prefer `NOA_API_URL`.

```bash
cd ../web
cp .env.example .env.local
npm install
npm run dev
```

Open: http://localhost:3000

## Manual Smoke Test

Env handling for smoke tests is strict:
- Reuse `apps/api/.env` from the `master` worktree so smoke tests hit the real configured LLM and do not fall back to mock/rule-based behavior.
- If a subagent/worktree needs an env file, copy `apps/api/.env` from the `master` worktree without reading, printing, or summarizing secret values.
- After copying, only these keys may be edited for local smoke tests: `API_CORS_ALLOWED_ORIGINS`, `AUTH_BOOTSTRAP_ADMIN_EMAILS`.
- Do not inspect, print, summarize, or modify any other secret values.
- Real `.env` files remain uncommitted.

1) Login via LDAP at `/login` using a user in `AUTH_BOOTSTRAP_ADMIN_EMAILS`.
2) In `/assistant`, create a thread and ask: `what time is it` (READ tool).
3) Ask: `set demo flag foo=bar` (CHANGE tool) and approve/deny the action card.
4) Visit `/admin` to enable/disable users and update tool allowlists.

## What’s Implemented (MVP)

- LDAP-only auth + JWT session; new users default to pending approval
- Admin RBAC: enable/disable users, assign tool allowlists
- Thread persistence (list/create/rename/archive/delete) backed by Postgres
- Assistant Transport streaming endpoint (`POST /assistant`)
- Tool registry with READ vs CHANGE risk and explicit approval gate for CHANGE tools

## Known Limitations

- No real infrastructure integrations yet (demo tools only)
- LLM token streaming is not implemented; assistant text is chunked after completion
- No multi-tenant orgs or shared threads (threads are owner-scoped)
- The assistant workspace is intentionally styled as a Claude-like UI; some controls are visible-but-disabled ("Coming soon") for layout parity: Edit/Reload, attachments, tools menu, extended thinking toggle, model selector, feedback.

```bash
cd apps/api
uv sync
uv run uvicorn noa_api.main:app --reload --port 8000
```

#### Terminal 2: Web

```bash
cd apps/web
npm install
npm run dev
```

Open: http://localhost:3000
