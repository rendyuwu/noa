# NOA admin panel image — Next.js + BIGSU (§T.60, C2, C12).
#
# Build context is THIS directory, not the repo root. `apps/admin-web` and `apps/web-embed` are
# independent packages with their own lockfiles and no shared source, deps or aliases (C12,
# AGENTS.md), and a repo-root context would put each one's files inside the other's image.
#
#   docker build -t noa-admin-web apps/admin-web
#
# This image can only be built from inside the Biznet Gio network: `.npmrc` maps `@gio/*` to
# `https://bigsu.biznetgio.pt/registry/`, which resolves to an internal-only address, and the
# access boundary is the network rather than a token (docs/admin-web.md "Registry access and
# CI"). No credential is added here, and none should be.

# Node 22, and the floor is the PACKAGE MANAGER's, not this app's.
#
# `engines.node` here says `>=20.9.0` and `next@16.3.0` agrees, so 20 would run the built app.
# It cannot BUILD it: `packageManager` pins pnpm 11.x, whose own `engines` demand `>=22.13`, and
# on Node 20 `pnpm install` dies with `ERR_UNKNOWN_BUILTIN_MODULE: No such built-in module:
# node:sqlite` before it resolves a single package. MEASURED on `node:20-bookworm-slim` at
# §T.60, not inferred — and it is why `docs/admin-web.md`'s "Node 20 LTS" runner expectation was
# corrected in the same change (V84a): the two clauses of that sentence contradicted each other.
#
# `-bookworm-slim` is pinned rather than plain `-slim` so a Debian release bump is a deliberate
# edit. `apps/api/tests/test_deployment.py` asserts this tag against `engines.node`, holds both
# web images on the same base, and goes red on a pnpm MAJOR bump — the floor above is measured
# for pnpm 11, so pnpm 12 has to be measured again rather than assumed.
FROM node:22-bookworm-slim AS deps

# Corepack activates the exact pnpm from `packageManager`, so the lockfile is installed by the
# resolver that wrote it. Installing pnpm any other way makes `--frozen-lockfile` a check
# against a different resolver's idea of the same file.
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable

WORKDIR /app

COPY package.json pnpm-lock.yaml .npmrc ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile


FROM node:22-bookworm-slim AS builder

ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Nothing is read from the environment at build time here, unlike `apps/web-embed`: this app's
# framing header names no origin (`frame-ancestors 'none'`, V41/§T.49), so there is no build
# argument to pass and no way for a variable to widen it. `next.config.ts` calls `loadRootEnv`,
# which looks for a `.git` above the package and finds none in this context — so the build reads
# no `.env` and is deterministic.
RUN pnpm build


FROM node:22-bookworm-slim AS runtime

WORKDIR /app

ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# `output: 'standalone'` with `outputFileTracingRoot` set to the package directory puts
# `server.js` and a traced `node_modules` at the root of `.next/standalone`. Static assets are
# not traced and have to be copied beside it, or every page loads without its CSS and JS.
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static

USER node

EXPOSE 3000

# `/healthz` is liveness only: it touches no dependency, so an API outage is never reported as
# this app being dead (docs/admin-web.md "Config and health"). There is no readiness probe, and
# that is a decision with a reason — `docs/deployment.md`.
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]

CMD ["node", "server.js"]
