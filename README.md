# Project NOA

AI operations workspace: chat UI + controlled tools.

The goal is a natural-language control center for infrastructure and operations.
The model interprets and proposes actions; the platform enforces permissions, approvals, and auditability.

Docs:
- `ARCHITECTURE.md`
- `docs/STATUS.md`
- `docs/assistant/workflow-templates.md`
- `docs/observability/README.md`
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

Create `./.env` from the repo-root `.env.example` (do not commit `.env`; it is gitignored).

```bash
cp .env.example .env
cd apps/api
uv sync
uv run alembic upgrade head
uv run uvicorn noa_api.main:app --reload --port 8000
```

Notes:
- `AUTH_BOOTSTRAP_ADMIN_EMAILS` and `API_CORS_ALLOWED_ORIGINS` must be JSON arrays (see examples).
- `NOA_DB_SECRET_KEY` is required for encrypted database-backed secrets such as WHM API tokens and SSH credentials. CSF/firewall execution uses the SSH credentials, not the WHM API token. Use a valid Fernet key.
- `python-ldap` may require OS packages to build (Ubuntu example: `sudo apt-get install -y libldap2-dev libsasl2-dev libssl-dev`).

### 3) Configure + run web

Create `./.env` from the repo-root `.env.example`.

Notes:
- The browser never calls the FastAPI backend directly. The web app calls same-origin `/api/...`, and a Next route handler proxies those requests server-side.
- Configure the proxy with `NOA_API_URL=http://localhost:8000` (server-side; used by Next). `NEXT_PUBLIC_API_URL` is a legacy fallback; prefer `NOA_API_URL`.

```bash
cp .env.example .env
cd apps/web
npm install
npm run dev
```

Open: http://localhost:3000

## Manual Smoke Test

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
- Workflow template registry for approval-oriented tool families, with WHM as the reference implementation
- WHM server inventory with encrypted stored API tokens for WHM API-backed tools
- Optional WHM SSH credentials with DB-pinned host fingerprints captured during validation
- CSF/firewall WHM tools now execute over SSH/bash instead of the WHM API token path
- Shared SSH execution layer for future server-backed READ/CHANGE tools
- READ-only WHM SSH binary checker tool (`whm_check_binary_exists`)

## Workflow Templates

Approval-oriented tool families use workflow templates on the API side to drive the assistant workflow dock, approval context, preflight enforcement, postflight verification, and waiting-on-user state.

- Shared contract: `apps/api/src/noa_api/core/workflows/types.py`
- Registry: `apps/api/src/noa_api/core/workflows/registry.py`
- WHM family implementation: `apps/api/src/noa_api/core/workflows/whm.py`
- Extension guide: `docs/assistant/workflow-templates.md`

## Known Limitations

- WHM is currently the only real server integration wired into the tool layer
- SSH trust is pinned per WHM server record; admins must run server validation after SSH credentials are added or rotated
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
