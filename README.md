# Project NOA

[![API CI](https://github.com/rendyuwu/noa/actions/workflows/api-scaffold-verify.yml/badge.svg)](https://github.com/rendyuwu/noa/actions/workflows/api-scaffold-verify.yml)
[![Web CI](https://github.com/rendyuwu/noa/actions/workflows/web-ci.yml/badge.svg)](https://github.com/rendyuwu/noa/actions/workflows/web-ci.yml)

AI operations workspace: chat UI + controlled tools.

The goal is a natural-language control center for infrastructure and operations.
The model interprets and proposes actions; the platform enforces permissions, approvals, recorded reasons, and auditability.

Docs:
- `ARCHITECTURE.md`
- `DESIGN.md`
- `docs/STATUS.md`
- `docs/integrations/whm.md`
- `docs/integrations/proxmox.md`
- `docs/assistant/workflow-templates.md`
- `docs/observability/README.md`

The integration docs are the canonical record of the admin endpoints, upstream API calls,
and SSH commands currently used by the codebase. Update them whenever WHM or Proxmox
features change.

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
- `AUTH_BOOTSTRAP_ADMIN_EMAILS` must be a JSON array. `API_CORS_ALLOWED_ORIGINS` accepts either a JSON array or comma-separated string.
- `NOA_DB_SECRET_KEY` is required for encrypted database-backed secrets (WHM API tokens, SSH credentials, Proxmox API tokens). Use a valid Fernet key. Generate one with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- `NOA_DB_SECRET_KEY` must be exported in the shell environment (not just present in `.env`) for Alembic migrations that encrypt secrets.
- `LLM_API_KEY` is required for the assistant runtime; there is no demo fallback path. Optionally set `LLM_MODEL` and `LLM_BASE_URL` to use a different OpenAI-compatible endpoint.
- `POSTGRES_URL` defaults to `postgresql+asyncpg://postgres:postgres@localhost:5432/noa`. Override if your Postgres credentials differ.
- `AUTH_JWT_SECRET` is auto-generated in dev but required in production (≥32 chars).
- `python-ldap` may require OS packages to build (Ubuntu example: `sudo apt-get install -y libldap2-dev libsasl2-dev libssl-dev`).

### 3) Configure + run web

Create `./.env` from the repo-root `.env.example`.

Notes:
- The browser never calls the FastAPI backend directly. The web app calls same-origin `/api/...`, and a Next route handler proxies those requests server-side.
- Configure the proxy with `NOA_API_URL=http://localhost:8000` (server-side; used by Next). `NEXT_PUBLIC_API_URL` is a legacy fallback; prefer `NOA_API_URL`.
- Optional: set `NEXT_PUBLIC_ERROR_REPORTING_ENABLED=true` with `NEXT_PUBLIC_ERROR_REPORTING_DSN` for Sentry error reporting.

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
3) If you have configured WHM/Proxmox inventory plus a preflight-ready target, try a CHANGE action such as `suspend a WHM account` and include a reason like `Ticket #1661262` before approving/denying the action card.
4) Visit `/admin` to enable/disable users and update tool allowlists.

## What’s Implemented (MVP)

- LDAP-backed auth + JWT session (with optional dev bypass via `AUTH_DEV_BYPASS_LDAP`); new users default to pending approval
- Admin RBAC: enable/disable users, role-based tool allowlists
- Thread persistence (list/create/rename/archive/delete) backed by Postgres
- Assistant Transport: `POST /assistant` (JSON ack), `GET /assistant/threads/{thread_id}/state` (canonical state), `GET /assistant/runs/{run_id}/live` (SSE stream), frontend `/api/assistant` wraps both into assistant-ui-compatible SSE
- Tool registry with READ vs CHANGE risk and explicit approval gate for CHANGE tools plus recorded reasons
- Workflow template registry for approval-oriented tool families, with WHM as the reference implementation and reason/evidence capture
- WHM server inventory with encrypted stored API tokens for WHM API-backed tools
- Optional WHM SSH credentials with DB-pinned host fingerprints captured during validation
- CSF/firewall WHM tools execute over SSH/bash instead of the WHM API token path
- Proxmox server inventory with encrypted API token storage
- Proxmox VM NIC enable/disable, cloud-init password reset, and VM pool membership move workflows with approval, preflight/postflight verification, and recorded reasons
- Shared SSH execution layer for future server-backed READ/CHANGE tools
- READ-only WHM SSH binary checker tool (`whm_check_binary_exists`)

## Workflow Templates

Approval-oriented tool families use workflow templates on the API side to drive the assistant workflow dock, approval context, preflight enforcement, postflight verification, and waiting-on-user state.

- Shared contract: `apps/api/src/noa_api/core/workflows/types.py`
- Registry: `apps/api/src/noa_api/core/workflows/registry.py`
- WHM family implementation: `apps/api/src/noa_api/core/workflows/whm/` (package with per-family modules)
- Extension guide: `docs/assistant/workflow-templates.md`

## Known Limitations

- WHM is currently the most complete server integration; Proxmox server inventory, validation, VM NIC enable/disable, cloud-init password reset, and VM pool membership move flows are also implemented
- SSH trust is pinned per WHM server record; admins must run server validation after SSH credentials are added or rotated
- LLM streaming uses buffered provider chunks (not true token-level streaming to the client)
- No multi-tenant orgs or shared threads (threads are owner-scoped)
- The assistant workspace is intentionally styled as a Claude-like UI; some controls are visible-but-disabled ("Coming soon") for layout parity: attachments, tools menu, extended thinking toggle, model selector, search

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding standards, and PR process.

## Code of Conduct

This project follows the [Contributor Covenant v2.1](CODE_OF_CONDUCT.md).

## Security

To report a vulnerability, see [SECURITY.md](.github/SECURITY.md).
