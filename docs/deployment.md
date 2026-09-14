# Deployment

Image, local dev stack, domain and cookie layout, replica and readiness choice — this file say
them. Rule live where enforce; this file repeat, not decide, except where line say decision made
here.

Machine-read block below mark with `# noa-…` comment and check by
`apps/api/tests/test_deployment.py`. Edit one, not other — test angry.

## Three images, three contexts

One image per deployable. Build context differ, and not by taste:

| Image | Context | Dockerfile | Port |
|---|---|---|---|
| API — FastAPI `/admin` + FastMCP `/mcp` | **repo root** | `apps/api/Dockerfile` | 8000 |
| Admin panel — Next.js + BIGSU | `apps/admin-web` | `apps/admin-web/Dockerfile` | 3000 |
| Embed — approval card + result tables | `apps/web-embed` | `apps/web-embed/Dockerfile` | 3001 |

```bash
docker build -f apps/api/Dockerfile -t noa-api .
docker build -t noa-admin-web apps/admin-web
docker build \
  --build-arg NOA_LIBRECHAT_ORIGIN=https://chat.noa.internal \
  --build-arg NEXT_PUBLIC_NOA_LIBRECHAT_ORIGIN=https://chat.noa.internal \
  -t noa-embed apps/web-embed
```

API context be repo root because `noa-api` be uv workspace member and `noa-core` be workspace
root: `apps/api`-scope context cannot find own dependency. Two web context be own directory
because those package be separate artifact with own lockfile and no shared source (AGENTS.md) —
repo-root context put each one file inside other image, which be boundary, not optimisation.

Three build fact worth know before first build:

- **`python-ldap==3.4.7` have no wheel.** `uv.lock` record sdist only, so API image compile it
  against OpenLDAP header in builder stage and ship only runtime library.
- **Web image need Node 22, not 20.** `engines.node` say `>=20.9.0` and `next@16.3.0` agree, so
  20 would *run* built app — but `packageManager` pin pnpm 11.x, whose own `engines` demand
  `>=22.13`, and on Node 20 `pnpm install` die with
  `ERR_UNKNOWN_BUILTIN_MODULE: No such built-in module: node:sqlite` before resolve any package.
  Measured while build this image on `node:20-bookworm-slim`. `docs/admin-web.md` say "Node
  20 LTS" for CI runner; that clause and `packageManager` clause fight each other, so doc fixed
  in same change.
- **API image ship `core/` from OLDER commit if workspace install read shared uv cache.**
  `--no-editable` build `noa-core` and `noa-api` into wheel. uv decide whether local package
  need rebuild from its `cache-keys`, which default to `pyproject.toml`. Code-only commit touch
  no `pyproject.toml`, so on runner whose BuildKit cache survive between build, that sync
  reinstall wheel built from PREVIOUS source — while `COPY core/ core/` bust its Docker layer,
  so every layer look freshly built. Measured 2026-09-14: staging image `staging-15707d34` was
  built from commit `15707d3`, and container from it print previous commit
  `firewall_gate.MESSAGE_SUDO_REQUIRED` and have no `availability.FIREWALL_PROBE_TARGET` at
  all. Reproduced local by build two commit in order against one cache mount. Fix = **no cache
  mount on that one `RUN`**; dependency sync above keep its cache, because its input be
  `uv.lock`, and unchanged lock DO mean same third-party wheel.
  `apps/api/tests/test_deployment.py::test_the_workspace_install_does_not_read_a_shared_uv_cache`
  fail if mount come back.

  **Consequence for every live check: image tag not proof of code inside image.** Verify by read
  a symbol from running container, never by read tag:

  ```bash
  kubectl -n staging exec deploy/noa-api -- python -c "import core.<module> as m; print(m.<NAME>)"
  ```

**Admin panel image only build inside Biznet Gio network.** `.npmrc` map `@gio/*` to
`https://bigsu.biznetgio.pt/registry/`, which resolve to internal-only address; from outside,
`pnpm install` fail with `ENOTFOUND bigsu.biznetgio.pt`. Access boundary be network, not token,
so no credential go in image and none should — see `docs/admin-web.md`, "Registry access and CI",
for runner requirement this put on pipeline.

## Configuration: build time versus runtime

Two setting bake at build time, they name one origin, and neither can be anything else.

| Setting | When | Read by | If unset |
|---|---|---|---|
| `NOA_LIBRECHAT_ORIGIN` | **build** | `apps/web-embed` `next.config.ts` → `config/framing.ts` | falls back to `https://chat.noa.internal`; the header is never omitted |
| `NEXT_PUBLIC_NOA_LIBRECHAT_ORIGIN` | **build** | `apps/web-embed` `src/lib/embed/frame-origin.ts` | no target origin: the card posts no height and renders `data-noa-frame-size="no-target-origin"` |
| `NOA_API_URL` | runtime | both web apps' `/api/*` proxy, server-side | proxy raises — no `NEXT_PUBLIC_*` twin exists |
| `NOA_SIGN_IN_URL` | runtime | embed 401 card | card names the state and offers no link |
| everything else | runtime | API, from the environment | per `core/config.py`; secrets and addresses refuse dev defaults outside development |

`output: 'standalone'` never run Next config at runtime, so `frame-ancestors` compile into output
(one `frame-ancestors` entry on every response; framing-header build bake it in). Consequence
have two side and both matter: runtime variable cannot **widen** framing allowlist, and cannot
**change** it either. Move LibreChat mean rebuild embed image, and image built for one
environment cannot be promote to another by retag. So neither name appear anywhere as container
environment variable, compose `environment:` key or ConfigMap entry — setting that look like it
move this value while do nothing be exactly the silent-wrong-address failure the
production-default refusal exist to stop, one setting over.

Second name be origin the card frame-sizing `postMessage` go to, and its `NEXT_PUBLIC_` prefix be
what make Next compile value into output, nothing to do with browser. Both must pass, with one
value: pass only first and header be correct while every sizing message get drop by browser for
origin mismatch — frame that render and never grow, with nothing in any log. Pass only second and
header fall back to development origin, which refuse real parent flat. `docker-compose.yml` feed
both build argument from one `.env` key so they cannot disagree there. Detail and check that
value survive build: `docs/embed-frame.md`.

Measured against built image, three way, because one alone prove nothing:

1. `--build-arg NOA_LIBRECHAT_ORIGIN=https://chat.example.test:8443` →
   `Content-Security-Policy: frame-ancestors https://chat.example.test:8443` on wire. Distinct
   origin, on purpose: measure with `https://chat.noa.internal` cannot tell argument working from
   fallback default.
2. `--build-arg NOA_LIBRECHAT_ORIGIN='https://*.example.test'` → **build fail** with
   `NOA_LIBRECHAT_ORIGIN must be a single origin like https://chat.noa.internal (scheme, host,
   optional port — no wildcard, no path, no second origin)`. Widening value cannot ship.
3. `printenv NOA_LIBRECHAT_ORIGIN` in running container → absent. Same for
   `NEXT_PUBLIC_NOA_LIBRECHAT_ORIGIN`: both be build argument, declare in builder stage only, and
   `apps/api/tests/test_deployment.py` assert each name absent from every image `ENV` and every
   compose `environment:` block.

## Domains and the session cookie

`noa_session` cookie scope `Domain=.noa.internal`, `SameSite=Lax`, `Path=/`, httpOnly. So
everything operator browser touch must sit under `noa.internal`, or cookie simply not ride:
sign-in look like it work and then every authenticated request answer 401 with nothing in any log
to say why. Same class of failure as wrong address, and why local stack document at these name
instead of `localhost`.

Hosts entry be the one render-path gate rig already use, verbatim:

```
# noa-hosts (asserted by apps/api/tests/test_deployment.py)
127.0.0.1 noa.internal embed.noa.internal chat.noa.internal
```

Remove it with
`sudo sed -i '/noa.internal embed.noa.internal chat.noa.internal/d' /etc/hosts`.

Local development origin, one per deployable plus chat client:

```
# noa-origins (asserted by apps/api/tests/test_deployment.py)
api        http://noa.internal:8000
admin-web  http://noa.internal:3000
embed      http://embed.noa.internal:3001
librechat  http://chat.noa.internal:3080
```

Three name, which be what deployment decision row say, because admin panel share API host and
differ only by port. Cookie ignore port, so one-registrable-parent rule hold; browser never call
FastAPI direct anyway (AGENTS.md), so two port never clash over route.

**Parent domain above be development one, not NOA one.** One-registrable-parent rule fix a
*property* — every operator-facing origin under one registrable parent, cookie scope to that
parent — and name `.noa.internal` only as what development use. Deployed, parent be
`.simondayce.my.id` (deploy overlay):

| | staging | production |
|---|---|---|
| API (`/mcp`, `/admin`, `/auth`) | `noa-api-staging.simondayce.my.id` | `noa-api.simondayce.my.id` |
| admin panel | `noa-admin-staging.simondayce.my.id` | `noa-admin.simondayce.my.id` |
| embed (approval card) | `noa-embed-staging.simondayce.my.id` | `noa-embed.simondayce.my.id` |
| LibreChat (not this repo) | `chat.simondayce.my.id` | `chat.simondayce.my.id` |

`AUTH_SESSION_COOKIE_DOMAIN=.simondayce.my.id` in both, and TLS end at ingress
(`simondayce-my-id-tls`) so `AUTH_SESSION_COOKIE_SECURE=true` hold. LibreChat sit under same
parent on purpose: operator reach approval card *through* chat document and sign in on admin
origin, so chat host outside parent break cookie ride before any `frame-ancestors` question come
up.

**Deployment need fourth name**, and it be name, not path. Behind TLS there be one port, so API
host cannot also serve admin panel; `NOA_SIGN_IN_URL` point at
`https://noa-admin.simondayce.my.id/login`. That be deployment decision and the one place this
file
go past that row own three-name list. Alternative — path prefix on API host — not taken, because
it put admin panel and MCP endpoint on one origin and make `frame-ancestors 'none'` versus embed
allowlist a per-path property instead of per-origin one. In development same split go by port
instead, which cost nothing because cookie ignore port and browser never call FastAPI direct.

After hosts line, set three value in repo-root `.env` to match:

```bash
NOA_EMBED_BASE_URL=http://embed.noa.internal:3001
NOA_SIGN_IN_URL=http://noa.internal:3000/login
NOA_LIBRECHAT_ORIGIN=http://chat.noa.internal:3080
```

`AUTH_SESSION_COOKIE_DOMAIN=.noa.internal` already what `.env.example` carry. Leave `localhost`
default in place while browse at `*.noa.internal` make silent 401 above; set these while browse
at `localhost` make same thing from other direction.

## Local development stack

```bash
cp .env.example .env
docker compose up -d postgres          # Postgres only — no image builds
docker compose --profile apps up -d    # Postgres, migrations, all three apps
```

Only `postgres` be profile-free. Documented development loop run API from checkout
(`uv run uvicorn`) against containerised database, and admin panel image cannot build outside
internal network at all — so make full stack the default would break one-liner for reason
unrelated to what it ask for.

Configuration come from repo-root `.env` and nowhere else. This be rule `apps/*/config/root-env.ts`
already state: two env file for one deployment be two place for `NOA_API_URL` to disagree, and
proxy quietly talk to wrong API be failure nobody see. Each service therefore override **only**
what container network force — service name instead of `localhost` — and read rest from `.env`:

- `api`, `migrate` — `POSTGRES_URL` host become `postgres`.
- `admin-web`, `embed` — `NOA_API_URL` become `http://api:8000`.

Two web service get their variable one by one instead of through `env_file`. They read one or two
setting each, and hand them whole file would put Fernet key, JWT secret and LDAP bind password
into container where nothing read them.

`migrate` run `alembic upgrade head` to end and exit; `api` wait on
`service_completed_successfully`. Failed migration then stop stack with migration own error,
instead of leave web server that answer 500 on every route.

Every published port bind `127.0.0.1`. Postgres credential here be throwaway pair `.env.example`
already carry, and loopback publish be what make that safe whatever password be — nothing off
host can reach database.

## One replica, deliberately

API image run single uvicorn process: no `--workers`, and one replica per deployment. Four thing
in design be per-process, not shared:

- **Session-clock rule** pin session-JWT verify to zero clock leeway because mint and verify
  share one clock. Second replica make that two clock, and first symptom be not logout near
  expiry — it be login whose token get reject the instant it issue, on whichever replica be
  behind. Session-clock rule name ">1 API replica" as its revisit trigger, and fix then be
  explicit `leeway`, not silent widening.
- **Approved-change executor** be in-process asyncio task with own DB session.
- **Expiry sweep** and **stranded-run reaper** be in-process loop on interval. N replica mean N
  sweeper fight over same row.
- **List-changed emitter session register** be per-process `WeakSet`, so notifier in one replica
  know only MCP session that replica hold.

Scale out be deliberate change carrying session-clock rule revisit trigger, not a replica count.

TLS and forwarded header belong to ingress. `--proxy-headers` deliberately **not** set on uvicorn:
it safe only alongside explicit `--forwarded-allow-ips`, and default that trust wrong hop let
client dictate own source address. Turn on both together or neither.

## Health and readiness — decided here

Every process expose **liveness** endpoint that touch nothing: `/health` on API (answer with
Postgres down on purpose) and `/healthz` on both web app. All three container healthcheck target
exactly those, so database or API outage never get report as some other process being dead, where
restart would be wrong cure.

**There be no readiness probe, and there will not be one on web tier.** `docs/admin-web.md` park
this question here: `/readyz` that read `NOA_API_URL` must define readiness across two deployable.
That be reason to refuse it, not reason to design it. Such probe make one deployable readiness a
function of another health, so rolling API restart pull both web app out of rotation at once — one
outage become three — and neither web app need API to serve its own error and 401 state (the 401
link-out state), which be the state operator see during exactly
that window.

Readiness that mean something get express as ordering, not endpoint: in compose, `postgres`
healthy plus `migrate` exit zero. Under orchestrator, use same liveness endpoint for both probe
kind on web tier and gate API rollout on migration job.

## Not in this repo

- **CI and orchestrator manifest — on `master` and `staging`, not on `main`.** `.gitlab-ci.yml`
  and `k8s/{staging,production}/` live on those two branch and be absent from `main` **by design**
  (CI-lane rule, deploy-overlay rule): GitHub remote be public and be pull-request surface, while
  GitLab `master` and `staging` carry `main` plus that overlay. So reader on `main` who grep for
  pipeline and find none be reading repository right. `docs/admin-web.md` record internal-runner
  requirement BIGSU registry force.

  Those two ref be the only one that mean anything downstream: every pipeline job filter to them,
  and ArgoCD Application — owner one, configure in ArgoCD not here — watch them and nothing else.
  Third branch hold overlay would build nothing and sync nothing, which be why overlay sit on
  `master` and `staging` direct.
- Configuration reach deployed pod through those ConfigMap and Secret — one ConfigMap per
  deployable and single Secret API alone mount — which be what `.env.example` say at top and why
  no image bake a `.env` (no secret in git; every `.dockerignore` here exclude it). Note that
  `NOA_LIBRECHAT_ORIGIN` and `NEXT_PUBLIC_NOA_LIBRECHAT_ORIGIN` be *not* among them: both bake at
  build and ConfigMap key of either name would read as the lever that move framing origin while
  do nothing at all.