# NOA embed image — approval card iframe + large-result tables.
#
# Build context is THIS directory, not the repo root, for the reason in `apps/admin-web`'s copy:
# the two web packages are independent deploy artifacts and share nothing. This one has no
# `.npmrc` and no `@gio/*` dependency at all — eslint refuses both — so unlike the admin
# image it builds from the public registry alone.
#
#   docker build --build-arg NOA_LIBRECHAT_ORIGIN=https://chat.noa.internal -t noa-embed apps/web-embed
#
# Node 22 for the reason spelled out in `apps/admin-web/Dockerfile`: `engines.node` allows 20 and
# the built app runs there, but the pinned pnpm 11.x needs `>=22.13` and dies on Node 20 with a
# missing `node:sqlite`. Both web images stay on one base, asserted by test.

FROM node:22-bookworm-slim AS deps

ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable

WORKDIR /app

COPY package.json pnpm-lock.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile


FROM node:22-bookworm-slim AS builder

ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# The one build-time input this repo has, and the only place it can be supplied.
#
# `Content-Security-Policy: frame-ancestors` is emitted from `next.config.ts` (the framing-headers rule), and
# `output: 'standalone'` never executes that config at runtime — the origin is baked into the
# compiled output here. So a runtime variable cannot widen the allowlist, and cannot change it
# either: moving LibreChat means rebuilding this image. A declared ARG is visible to `pnpm build`
# below as an environment variable, which is how `config/framing.ts` reads it.
#
# Unset or blank falls back to the pinned `https://chat.noa.internal`, so the header is never
# omitted by a variable nobody passed. A wildcard, a path or a second origin throws inside
# `resolveFrameAncestor`, which fails THIS BUILD rather than shipping a header nobody meant.
#
# Deliberately absent from the runtime stage below. An `ENV NOA_LIBRECHAT_ORIGIN` there — or the
# same key in a compose service or a ConfigMap — would read as the lever that moves this value
# while doing nothing at all: exactly the silently-wrong-address failure the no-silent-dev-
# default rule exists to stop, one setting over. `apps/api/tests/test_deployment.py` asserts both halves.
ARG NOA_LIBRECHAT_ORIGIN

RUN pnpm build


FROM node:22-bookworm-slim AS runtime

WORKDIR /app

# 3001 is this app's port throughout: `package.json` scripts, the root `.env.example`
# (`NOA_EMBED_BASE_URL`), and the Playwright lane all name it.
ENV NODE_ENV=production \
    PORT=3001 \
    HOSTNAME=0.0.0.0

# Runtime configuration, both read server-side per request and neither with a `NEXT_PUBLIC_*`
# twin, because the browser never calls FastAPI direct (AGENTS.md):
#   NOA_API_URL     — the same-origin proxy's target. Required; the proxy raises without it.
#   NOA_SIGN_IN_URL — where the 401 card points an operator. Absent, blank or
#                     not an http(s) URL leaves the card naming the state with no link, which is
#                     the intended answer rather than a door to a host nobody deployed.
# Neither is given a default here: a default address is the failure the no-silent-dev-default rule describes.

COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static

USER node

EXPOSE 3001

# Liveness only, and it touches nothing — not the API, not a cookie, not the DB
# (`src/app/healthz/route.ts`). Readiness is not probed; see `docs/deployment.md`.
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3001/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]

CMD ["node", "server.js"]
