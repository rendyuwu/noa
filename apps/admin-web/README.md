# apps/admin-web

Next.js 16 + BIGSU admin panel. Users, roles, tokens, servers (WHM/Proxmox/PMG), audit.

Independent package: own `package.json`, own lockfile, own CI, own deploy artifact. Shares no
source or deps with `apps/web-embed` — `eslint.config.mjs` holds that boundary as a rule, and
`tests/import-firewall.test.ts` runs ESLint against it so the rule cannot rot unnoticed.

## Run it

```bash
pnpm install     # @gio/* resolve from bigsu.biznetgio.pt via .npmrc
pnpm dev         # http://localhost:3000  (apps/web-embed owns 3001)
```

Config comes from the repo-root `.env`, not a per-app `.env.local`: `next.config.ts` reads it
through `config/root-env.ts`, and anything already set in the real environment wins. Two env files
for one deployment is two places for `NOA_API_URL` to disagree.

## Session and the API hop

The browser never calls FastAPI directly. Everything goes to `/api/*` on this origin and
`src/app/api/[...path]/route.ts` forwards it server-side to `NOA_API_URL`, carrying the httpOnly
`noa_session` cookie the registrable domain put here (§T.50, V40). `Authorization` is dropped
outbound: bearer tokens are MCP-only and LibreChat's to send, so `/api/mcp/` is reachable on
this origin and inert.

It is a pass-through, unlike the embed's four-entry allowlist (§T.44). The embed needs one because
it is the single NOA origin LibreChat may frame; this app answers `frame-ancestors 'none'`, and
the surface it needs is `§I.admin-api` in full — an allowlist would have to be edited by every later
admin task and a stale entry there fails as a 404 the panel cannot explain.

`/login` is the LDAP sign-in form (§T.50). Its primary action is a `type="button"` click handler, not
a form submit, and that is load-bearing rather than stylistic: `NOA_SIGN_IN_URL` points here, one way
an operator arrives is a click on the embed's 401 card link-out, and R32 measured that the tab such a
click opens inherits the frame's sandbox — where `allow-forms` is absent, so a submit-driven
login would be refused with nothing the operator can see (V94, and V80's failure shape one origin
over). The `<form>` stays and routes to the same handler, for the operator who copies the address
into a fresh tab instead.

## Checks

```bash
pnpm lint
pnpm typecheck
pnpm test
pnpm test:server   # boots `next dev` on a free port; minutes, not milliseconds
pnpm build
```

`tests/` holds the package-level guards rather than feature tests: `pins.test.ts` (C2 — exact
versions, no caret ranges), `npmrc.test.ts` (the `@gio` scope resolves from the internal registry),
`hygiene.test.ts` (C14/V65 — `.ts` ≤ 300 lines, `.tsx` ≤ 450) and `import-firewall.test.ts`.

`tests/**/*.server.test.ts` is the one lane that runs a real server, so it has its own config
(`vitest.server.config.ts`) and is excluded from `pnpm test`.

## Framing

Every response carries `Content-Security-Policy: frame-ancestors 'none'` (§T.49, V41): this app is
never framed, by anyone. The rule is `config/framing.ts` and `next.config.ts` returns it from
`headers()` as one entry on `/(.*)`, so the pages, `/login`, `/healthz`, the 404 and the `/api/*`
proxy are covered without each new route remembering a guard for itself.

Nothing about it is configurable. `apps/web-embed` has one legitimate parent and reads its origin
from `NOA_LIBRECHAT_ORIGIN` (§T.45); this app has none, so `buildFramingHeaders()` takes no argument
and no variable in the shared repo-root `.env` can widen it — `tests/next-config-headers.test.ts`
asserts that the embed's variable means nothing here.

No `X-Frame-Options`: `frame-ancestors` supersedes it wherever both are read, and one added later
would be honoured *instead* by a client that reads XFO first. Its absence is asserted, not assumed.
`tests/framing-live.server.test.ts` proves both against a running server rather than against the
config object — a `headers()` entry Next never applies looks the same from inside the process.

## Status

Scaffold is `SPEC.md` §T.47; the ported admin pages are §T.48; the framing header is §T.49; the
`/api/*` proxy, the `noa_session` plumbing and the login route are §T.50.

Still open: the admin verticals' own endpoints (§T.51–§T.55 — the pages are ported, the API routes
they call are not all built), and a readiness probe. `/healthz` here is liveness only — it touches no
dependency, so a backend outage is never reported as this app's. A `/readyz` that reads `NOA_API_URL`
would have to define readiness across two deployables, which is a deployment decision and belongs
with §T.60, not with the session plumbing.
