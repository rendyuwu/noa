# Project NOA - Status

Last updated: 2026-03-10

This is a living checklist of what is implemented in the MVP and what is intentionally not implemented yet.

## Implemented (MVP)

- Monorepo structure (`apps/api`, `apps/web`) + Postgres dev stack (`docker-compose.yml`)
- Backend (FastAPI)
  - LDAP-only login (python-ldap)
  - JWT session
  - New users default to pending approval; bootstrap admin(s) via `AUTH_BOOTSTRAP_ADMIN_EMAILS`
  - Admin RBAC: enable/disable users, assign tool allowlists
  - Postgres persistence + Alembic migrations
  - Thread CRUD API for assistant-ui remote thread list
  - Assistant Transport endpoint (`POST /assistant`) using assistant-stream
  - Tool registry (READ vs CHANGE) + demo tools
  - Approval gate for CHANGE tools, including approve/deny execution path
  - Audit log events for auth/admin/actions/tools

- Frontend (Next.js + assistant-ui)
  - Login page (`/login`)
  - Assistant workspace (`/assistant`) with thread list + chat
  - Claude-style UI skin
  - Same-origin `/api/*` proxy (Next route handler) so the browser never calls the FastAPI backend directly (configure via `NOA_API_URL`)
  - Admin UI (`/admin`) for user and tool management
  - Approval card UI for CHANGE actions

## Verified

- Backend unit/integration tests exist and pass locally (see `docs/reports/2026-03-09-task-11-verification.md`)
- Web build/typecheck passes (`npm run build`)

## Not Implemented Yet (By Design)

- Real integrations (WHM/Proxmox/DNS/monitoring/billing/support)
- True LLM token streaming (current MVP chunks completed text)
- Multi-tenant org/team model and shared threads
- File uploads / attachments / sync server (Assistant Cloud)
- Claude-like UI controls are visible-but-disabled (kept for layout parity; show "Coming soon"): Edit/Reload, attachments, tools menu, extended thinking toggle, model selector, feedback.
- StartTLS toggle for LDAP (current code expects LDAP URI; use `ldaps://...` in production)
- Production deployment (Docker images, k8s manifests, ingress, etc.)

## Known Rough Edges

- Environment variable parsing for list/set fields requires JSON arrays (e.g. `API_CORS_ALLOWED_ORIGINS=["http://localhost:3000"]`).
- LDAP dependency is installed by default via `uv sync` and may require system libraries.

## Suggested Next Steps

1) Add one real integration (pick a small READ-only subset first).
2) Add true LLM streaming (server-side streamed completions).
3) Add per-tool “preview” summaries for CHANGE actions (diff/impact explanation).
