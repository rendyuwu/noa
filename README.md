# NOA

MCP server for hosting-infrastructure operations. Exposes 14 RBAC-gated tools (READ + approval-gated
CHANGE) to LibreChat over Streamable HTTP. Operators approve or deny CHANGE actions from an approval
card served on the NOA origin — the reason is typed there, never supplied by the LLM.

`SPEC.md` is the working artifact. Read it before changing code; every task, invariant, and
constraint referenced below lives there.

## Layout

Single repo, three deploy artifacts, one shared core.

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
carries the full rationale, the re-open triggers, and the tests that hold them.

## Prerequisites

- Python `>=3.11,<3.13`. The upper bound is load-bearing: `pgpy` 0.6.0 imports the stdlib `imghdr`
  module, removed in 3.13, at import time.
- `uv` for Python dependency management.
- Node.js **22** and `pnpm` for the two web apps. `engines.node` allows 20 and the built apps run
  there, but the pinned pnpm 11.x needs `>=22.13` and `pnpm install` fails outright on 20.
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

## Deployment

One image per deployable, and the build contexts differ: the API builds from the repo root
because it is a uv workspace member, each web app builds from its own directory because the two
share no source.

```bash
docker compose up -d postgres          # Postgres only — no image builds
docker compose --profile apps up -d    # Postgres, migrations, all three apps
```

`docs/deployment.md` is the reference: images and contexts, which settings are baked at build time
versus read at runtime, the one-registrable-parent domain layout the session cookie requires
(`*.noa.internal` in development, `*.simondayce.my.id` deployed), why the
API runs as a single replica, and why there is no readiness probe. Two things to know before a
first build — the admin panel image only builds inside the Biznet Gio network (`@gio/*` is on an
internal-only registry), and the embed image takes `NOA_LIBRECHAT_ORIGIN` as a **build argument**,
because `frame-ancestors` is compiled into the standalone output and no runtime variable can move
it.

## Checks

From the repo root:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

Per web app: `pnpm lint`, `pnpm typecheck`, `pnpm test`, and `pnpm test:e2e` where a browser lane
exists (`apps/web-embed` boots its own dev server, a stub upstream for the proxy and card specs, and
a parent page that frames the card the way LibreChat does; run `pnpm exec playwright install
chromium` once). `apps/admin-web` has no browser lane yet; its `pnpm test:server` boots a dev server
on a free port and reads real responses off the wire — the framing header and the `/api/*`
proxy hop against a recording stub upstream — separate from `pnpm test` because it costs
minutes.

## Conventions

- Conventional commits. No secrets in git — `.env*` is ignored except `.env.example`.
- File size limits: `.py` <= 900 lines, `.ts` <= 300, `.tsx` <= 450.
- Type hints on new Python, TypeScript types on new frontend code, tests alongside.
- Env vars holding lists use JSON arrays, e.g. `AUTH_BOOTSTRAP_ADMIN_EMAILS=["a@b.com"]`.
- `NOA_SECRET_ENCRYPTION_KEY` encrypts server credentials, not the database. The name says so on
  purpose.
- `NOA_EMBED_BASE_URL` and `NOA_API_URL` are required outside development: their `localhost`
  defaults are refused at startup. Both are addresses handed onward — the approval URL to
  an operator, the proxy target to a web app — and unlike a missing secret, a wrong address raises
  nothing at first use. It just resolves nowhere.

## Reference repo

`noa-old` is a pattern source, not a fork base. Port from branch `MCP` — `core/secrets/yopass.py`,
`core/secrets/password.py`, and the yopass doc exist only there. Mature integration layers (WHM,
Proxmox, PMG, `remote_exec`, `secrets`) get copied rather than rewritten; SSH banner stripping and
`sudo -n` escalation are already hardened in them.

Host-key pinning and TOFU refresh are **not**, despite what this section used to say. Upstream
passes `known_hosts=None`, which is asyncssh's documented off switch, so the port inherited a pin
that accepted any host key until it was fixed here. Upstream provenance is not
evidence that a control works: a ported security control needs a test against the real mechanism
before any doc calls it hardened.

The same rule caught the *refresh* half in the admin validate route. Upstream's WHM validate captured whatever key
answered and overwrote the stored pin on every run, which makes the pin worth nothing — any admin
pressing Validate silently re-trusted whatever was on the other end of the address. NOA pins
**once**: a row with no fingerprint gets one captured and stored only if the probe that follows it
passes, and a stored pin that no longer matches answers `ssh_host_key_mismatch` instead of being
refreshed. A legitimate key rotation is a deliberate two-step — clear the fingerprint, then
validate. Its test stands on a real `asyncssh` server on loopback and asserts the server recorded
zero authentication attempts (`apps/api/tests/test_server_host_key_validation.py`).
