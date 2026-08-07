# NOA

MCP server for hosting-infrastructure operations. Exposes 14 RBAC-gated tools (READ + approval-gated
CHANGE) to LibreChat over Streamable HTTP. Operators approve or deny CHANGE actions from an approval
card served on the NOA origin — the reason is typed there, never supplied by the LLM.

`SPEC.md` is the working artifact. Read it before changing code; every task, invariant, and
constraint referenced below lives there.

## Layout

Single repo, three deploy artifacts, one shared core (C12).

```
noa/
  apps/api/         FastAPI + FastMCP — /mcp + /admin router sets, one Alembic
  apps/admin-web/   Next.js + BIGSU  — admin panel (users, roles, servers, audit)
  apps/web-embed/   Next.js          — approval card iframe + large-result tables
  core/             shared: config, auth, remote_exec, secrets, integrations
  docs/             integration + operations reference
```

`apps/admin-web` and `apps/web-embed` are separate Node packages with their own lockfiles. They
share no source, deps, or aliases with each other.

## Architecture in one pass

1. LibreChat calls `POST /mcp` with a per-user NOA-minted bearer token plus
   `X-Noa-LibreChat-User`. NOA resolves the user, re-checks `is_active`, and filters `tools/list`
   by role permissions.
2. READ tools execute immediately and write a `tool_runs` row.
3. CHANGE tools do not execute on call. They insert `action_requests(status=pending)` and return an
   approval surface carrying only the request id.
4. The operator opens the approval card on the NOA origin, types a reason, and approves. That POST
   carries the `noa_session` cookie plus a server-minted CSRF token — it never travels through the
   LLM.
5. Approval triggers async execution, then writes `tool_runs`, `action_receipts`, and audit.

Authorization is split deliberately: NOA RBAC decides who may call a tool, the NOA approval gate
decides whether a specific change may run, and LibreChat handles transport and rendering only.

## Protocol pin

MCP handshake era, negotiated per client, server `fastmcp==3.4.5`.

NOA does not pin one era. `mcp==1.29.0` answers `initialize` with whatever the client asked for when
that version is in its `SUPPORTED_PROTOCOL_VERSIONS` — `2024-11-05`, `2025-03-26`, `2025-06-18`,
`2025-11-25`. LibreChat is the sole MCP client, locks `@modelcontextprotocol/sdk` at exactly 1.29.0,
and its `Client` sends that SDK's `LATEST_PROTOCOL_VERSION` with no override, so the era this
deployment actually negotiates is `2025-11-25`. Every one of those is a handshake era, so
`Mcp-Session-Id` is in play throughout.

The sessionless `2026-07-28` era is in neither SDK's supported list, so moving there is an SDK bump,
not a setting — and it buys nothing against a client that will not ask for it. FastMCP holds at 3.x
because 4.x is beta-only, drops the 3.x shims, and relocates `fastmcp.server.auth.*`, which would
break token verification for no gain.

Re-open when LibreChat ships SDK v2 — and note that a bump *inside* 1.x moves the negotiated era
with no error and no config change, which is why no digit above is load-bearing. `ARCHITECTURE.md`
carries the full rationale, the re-open triggers, and the tests that hold them (C23, T70).

## Prerequisites

- Python `>=3.11,<3.13`. The upper bound is load-bearing: `pgpy` 0.6.0 imports the stdlib `imghdr`
  module, removed in 3.13, at import time (C1).
- `uv` for Python dependency management.
- Node.js 20+ and `pnpm` for the two web apps.
- Postgres 16.

## Setup

```bash
cp .env.example .env
docker compose up -d postgres
```

API:

```bash
cd apps/api
uv sync
uv run alembic upgrade head
uv run uvicorn noa_api.main:app --reload --port 8000
```

Web apps use `pnpm install` and `pnpm dev` in their own directories.

## Checks

From the repo root:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

Per web app: `pnpm lint`, `pnpm typecheck`, `pnpm test`.

## Conventions

- Conventional commits. No secrets in git — `.env*` is ignored except `.env.example`.
- File size limits: `.py` <= 900 lines, `.ts` <= 300, `.tsx` <= 450 (C14).
- Type hints on new Python, TypeScript types on new frontend code, tests alongside.
- Env vars holding lists use JSON arrays, e.g. `AUTH_BOOTSTRAP_ADMIN_EMAILS=["a@b.com"]`.
- `NOA_SECRET_ENCRYPTION_KEY` encrypts server credentials, not the database. The name says so on
  purpose.

## Reference repo

`noa-old` is a pattern source, not a fork base. Port from branch `MCP` — `core/secrets/yopass.py`,
`core/secrets/password.py`, and the yopass doc exist only there. Mature integration layers (WHM,
Proxmox, PMG, `remote_exec`, `secrets`) get copied rather than rewritten; SSH banner stripping,
`sudo -n` escalation, host-key pinning, and TOFU refresh are already hardened in them (C13, V69).
