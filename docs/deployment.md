# Deployment

Images, the local development stack, domain and cookie layout, and the replica and readiness
decisions (§T.60). `SPEC.md` is the source for every invariant cited here; this file restates
rather than decides, except where a line says a decision was made at §T.60.

Machine-readable blocks below are marked with a `# noa-…` comment and asserted by
`apps/api/tests/test_deployment.py`. Editing one without the other fails a test.

## Three images, three contexts

One image per deployable (C12). The build contexts differ, and not by preference:

| Image | Context | Dockerfile | Port |
|---|---|---|---|
| API — FastAPI `/admin` + FastMCP `/mcp` | **repo root** | `apps/api/Dockerfile` | 8000 |
| Admin panel — Next.js + BIGSU | `apps/admin-web` | `apps/admin-web/Dockerfile` | 3000 |
| Embed — approval card + result tables | `apps/web-embed` | `apps/web-embed/Dockerfile` | 3001 |

```bash
docker build -f apps/api/Dockerfile -t noa-api .
docker build -t noa-admin-web apps/admin-web
docker build --build-arg NOA_LIBRECHAT_ORIGIN=https://chat.noa.internal -t noa-embed apps/web-embed
```

The API's context is the repo root because `noa-api` is a uv workspace member and `noa-core` is
the workspace root: an `apps/api`-scoped context cannot resolve its own dependency. The two web
contexts are their own directories because those packages are independent artifacts with their
own lockfiles and no shared source (C12, AGENTS.md) — a repo-root context would put each one's
files inside the other's image, which is the boundary rather than an optimisation.

Two build facts worth knowing before a first build:

- **`python-ldap==3.4.7` has no wheel.** `uv.lock` records an sdist only, so the API image
  compiles it against OpenLDAP headers in a builder stage and ships only the runtime libraries.
- **The web images need Node 22, not 20.** `engines.node` says `>=20.9.0` and `next@16.3.0`
  agrees, so 20 would *run* the built app — but `packageManager` pins pnpm 11.x, whose own
  `engines` demand `>=22.13`, and on Node 20 `pnpm install` dies with
  `ERR_UNKNOWN_BUILTIN_MODULE: No such built-in module: node:sqlite` before resolving a package.
  Measured at §T.60 on `node:20-bookworm-slim`. `docs/admin-web.md` said "Node 20 LTS" for CI
  runners; that clause and the `packageManager` clause contradicted each other and the doc was
  corrected in the same change (V84a).

**The admin panel image only builds inside the Biznet Gio network.** `.npmrc` maps `@gio/*` to
`https://bigsu.biznetgio.pt/registry/`, which resolves to an internal-only address; from outside,
`pnpm install` fails with `ENOTFOUND bigsu.biznetgio.pt`. The access boundary is the network, not
a token, so no credential is added to the image and none should be — see `docs/admin-web.md`,
"Registry access and CI", for the runner requirement this puts on the pipeline.

## Configuration: build time versus runtime

Exactly one setting is baked at build time, and it is the one that cannot be anything else.

| Setting | When | Read by | If unset |
|---|---|---|---|
| `NOA_LIBRECHAT_ORIGIN` | **build** | `apps/web-embed` `next.config.ts` → `config/framing.ts` | falls back to `https://chat.noa.internal`; the header is never omitted |
| `NOA_API_URL` | runtime | both web apps' `/api/*` proxy, server-side | proxy raises — no `NEXT_PUBLIC_*` twin exists |
| `NOA_SIGN_IN_URL` | runtime | embed 401 card | card names the state and offers no link |
| everything else | runtime | API, from the environment | per `core/config.py`; secrets and addresses refuse dev defaults outside development (V52, V53, V95) |

`output: 'standalone'` never executes the Next config at runtime, so `frame-ancestors` is
compiled into the output (V41, §T.45). The consequence is two-sided and both sides matter: a
runtime variable cannot **widen** the framing allowlist, and it cannot **change** it either.
Moving LibreChat means rebuilding the embed image. `NOA_LIBRECHAT_ORIGIN` therefore appears
nowhere as a container environment variable, a compose `environment:` key or a ConfigMap entry —
a setting that looks like it moves this value while doing nothing is exactly the
silently-wrong-address failure V95 exists to stop, one setting over.

Measured at §T.60 against the built image, three ways, because one of them alone proves nothing:

1. `--build-arg NOA_LIBRECHAT_ORIGIN=https://chat.example.test:8443` →
   `Content-Security-Policy: frame-ancestors https://chat.example.test:8443` on the wire. A
   distinct origin, deliberately: measuring with `https://chat.noa.internal` cannot separate the
   argument working from the fallback default (V87).
2. `--build-arg NOA_LIBRECHAT_ORIGIN='https://*.example.test'` → the **build fails** with
   `NOA_LIBRECHAT_ORIGIN must be a single origin like https://chat.noa.internal (scheme, host,
   optional port — no wildcard, no path, no second origin)`. A widening value cannot ship.
3. `printenv NOA_LIBRECHAT_ORIGIN` in the running container → absent.

## Domains and the session cookie

The `noa_session` cookie is scoped `Domain=.noa.internal`, `SameSite=Lax`, `Path=/`, httpOnly
(V6, V40). Everything an operator's browser touches therefore has to sit under `noa.internal`, or
the cookie simply does not ride: sign-in appears to succeed and then every authenticated request
answers 401 with nothing in any log to say why. This is the same class of failure as a wrong
address (V95) and it is why the local stack is documented at these names rather than at
`localhost`.

The hosts entry is the one the §T.59 render-gate rig already used, verbatim (R29, V66):

```
# noa-hosts (asserted by apps/api/tests/test_deployment.py)
127.0.0.1 noa.internal embed.noa.internal chat.noa.internal
```

Remove it with
`sudo sed -i '/noa.internal embed.noa.internal chat.noa.internal/d' /etc/hosts`.

Local development origins, one per deployable plus the chat client:

```
# noa-origins (asserted by apps/api/tests/test_deployment.py)
api        http://noa.internal:8000
admin-web  http://noa.internal:3000
embed      http://embed.noa.internal:3001
librechat  http://chat.noa.internal:3080
```

Three names, which is what §T.60's row specifies, because the admin panel shares the API's host
and differs only by port. Cookies ignore ports, so V40 holds; the browser never calls FastAPI
directly anyway (AGENTS.md), so the two ports never collide over a route.

**The parent domain above is development's, not NOA's.** V40 fixes a *property* — every
operator-facing origin under one registrable parent, and the cookie scoped to that parent — and
names `.noa.internal` only as what development uses. Deployed, the parent is
`.simondayce.my.id` (§T.75):

| | staging | production |
|---|---|---|
| API (`/mcp`, `/admin`, `/auth`) | `noa-api-staging.simondayce.my.id` | `noa-api.simondayce.my.id` |
| admin panel | `noa-admin-staging.simondayce.my.id` | `noa-admin.simondayce.my.id` |
| embed (approval card) | `noa-embed-staging.simondayce.my.id` | `noa-embed.simondayce.my.id` |
| LibreChat (not this repo) | `chat.simondayce.my.id` | `chat.simondayce.my.id` |

`AUTH_SESSION_COOKIE_DOMAIN=.simondayce.my.id` in both, and TLS terminates at the ingress
(`simondayce-my-id-tls`) so `AUTH_SESSION_COOKIE_SECURE=true` holds. LibreChat sits under the same
parent deliberately: the operator reaches the approval card *through* the chat document and signs in
on the admin origin, so a chat host outside the parent breaks the cookie's ride before any
`frame-ancestors` question is reached.

**A deployment needs a fourth name**, and it is a name rather than a path. Behind TLS there is one
port, so the API's host cannot also serve the admin panel; `NOA_SIGN_IN_URL` points at
`https://noa-admin.simondayce.my.id/login`. That is a §T.60 decision and the one place this file
goes past that row's own three-name list. The alternative — a path prefix on the API host — was not
taken, because it puts the admin panel and the MCP endpoint on one origin and makes
`frame-ancestors 'none'` versus the embed's allowlist a per-path property instead of a per-origin
one. In development the same split is by port instead, which costs nothing because cookies ignore
ports (V40) and the browser never calls FastAPI directly.

After the hosts line, set three values in the repo-root `.env` to match:

```bash
NOA_EMBED_BASE_URL=http://embed.noa.internal:3001
NOA_SIGN_IN_URL=http://noa.internal:3000/login
NOA_LIBRECHAT_ORIGIN=http://chat.noa.internal:3080
```

`AUTH_SESSION_COOKIE_DOMAIN=.noa.internal` is already what `.env.example` carries. Leaving the
`localhost` defaults in place while browsing at `*.noa.internal` produces the silent 401 above;
setting these while browsing at `localhost` produces the same thing from the other direction.

## Local development stack

```bash
cp .env.example .env
docker compose up -d postgres          # Postgres only — no image builds
docker compose --profile apps up -d    # Postgres, migrations, all three apps
```

Only `postgres` is profile-free. The documented development loop runs the API from a checkout
(`uv run uvicorn`) against a containerised database, and the admin panel image cannot be built
outside the internal network at all — so making the full stack the default would break the
one-liner for a reason unrelated to what it asks for.

Configuration comes from the repo-root `.env` and nowhere else. This is the rule
`apps/*/config/root-env.ts` already states: two env files for one deployment is two places for
`NOA_API_URL` to disagree, and a proxy quietly talking to the wrong API is not a failure anyone
sees. Each service therefore overrides **only** what the container network forces — a service
name in place of `localhost` — and reads the rest from `.env`:

- `api`, `migrate` — `POSTGRES_URL` host becomes `postgres`.
- `admin-web`, `embed` — `NOA_API_URL` becomes `http://api:8000`.

The two web services are given their variables individually rather than through `env_file`. They
read one or two settings each, and handing them the whole file would put the Fernet key, the JWT
secret and the LDAP bind password into containers where nothing reads them (C7, V8).

`migrate` runs `alembic upgrade head` to completion and exits; `api` waits on
`service_completed_successfully`. A failed migration then stops the stack with the migration's own
error, instead of leaving a web server that answers 500 on every route.

Every published port binds `127.0.0.1`. The Postgres credentials here are the throwaway pair
`.env.example` already carries, and loopback publishing is what makes that safe regardless of the
password — nothing off the host can reach the database.

## One replica, deliberately

The API image runs a single uvicorn process: no `--workers`, and one replica per deployment. Four
things in the design are per-process, not shared:

- **V79** pins session-JWT verification to zero clock leeway because mint and verify share one
  clock. A second replica makes that two clocks, and the first symptom is not a logout near
  expiry — it is a login whose token is rejected the instant it is issued, on whichever replica
  is behind. V79 names ">1 API replica" as its revisit trigger, and the fix then is an explicit
  `leeway`, not a silent widening.
- **T38's approved-change executor** is an in-process asyncio task with its own DB session.
- **T39's expiry sweep** and **T38's stranded-run reaper** are in-process loops on an interval.
  N replicas means N sweepers competing over the same rows.
- **T66's session register** is a per-process `WeakSet`, so a notifier in one replica knows only
  the MCP sessions that replica holds.

Scaling out is a deliberate change carrying V79's revisit trigger, not a replica count.

TLS and forwarded headers belong to the ingress. `--proxy-headers` is deliberately **not** set on
uvicorn: it is only safe alongside an explicit `--forwarded-allow-ips`, and a default that trusts
the wrong hop lets a client dictate its own source address. Enable both together or neither.

## Health and readiness — decided at §T.60

Every process exposes a **liveness** endpoint that touches nothing: `/health` on the API (V51,
answers with Postgres down on purpose) and `/healthz` on both web apps. All three container
healthchecks target exactly those, so a database or API outage is never reported as some other
process being dead, where a restart would be the wrong remedy.

**There is no readiness probe, and there will not be one on the web tier.** `docs/admin-web.md`
parked this question here: a `/readyz` that reads `NOA_API_URL` has to define readiness across two
deployables. That is the reason to refuse it rather than a reason to design it. Such a probe makes
one deployable's readiness a function of another's health, so a rolling API restart pulls both web
apps out of rotation at once — one outage becomes three — and neither web app needs the API to
serve its own error and 401 states (§T.43), which are the states an operator sees during exactly
that window.

Readiness that means something is expressed as ordering, not as an endpoint: in compose,
`postgres` healthy plus `migrate` exited zero. Under an orchestrator, use the same liveness
endpoints for both probe kinds on the web tier and gate the API rollout on the migration job.

## Not in this repo

- **CI and orchestrator manifests — on `master` and `staging`, not on `main`.** `.gitlab-ci.yml`
  and `k8s/{staging,production}/` live on those two branches and are absent from `main` **by
  design** (§T.61, §T.75): the GitHub remote is public and is the pull-request surface, while
  GitLab `master` and `staging` carry `main` plus that overlay. So a reader on `main` who greps for
  a pipeline and finds none is reading the repository correctly. `docs/admin-web.md` records the
  internal-runner requirement the BIGSU registry forces.

  Those two refs are the only ones that mean anything downstream: every pipeline job is filtered
  to them, and the ArgoCD Applications — the owner's, configured in ArgoCD rather than here —
  watch them and nothing else. A third branch holding the overlay would therefore build nothing
  and sync nothing, which is why the overlay sits on `master` and `staging` directly.
- Configuration reaches a deployed pod through those ConfigMaps and Secrets — one ConfigMap per
  deployable and a single Secret the API alone mounts — which is what `.env.example` says at the
  top and why no image bakes a `.env` (C11; every `.dockerignore` here excludes it). Note that
  `NOA_LIBRECHAT_ORIGIN` is *not* among them: it is baked at build (V41) and a ConfigMap key would
  read as the lever that moves it while doing nothing at all (V101).
