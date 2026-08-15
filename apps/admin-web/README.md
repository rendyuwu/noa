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
`headers()` as one entry on `/(.*)`, so the pages, `/healthz`, the 404 and the `/api/*` proxy §T.50
adds are covered without each new route remembering a guard for itself.

Nothing about it is configurable. `apps/web-embed` has one legitimate parent and reads its origin
from `NOA_LIBRECHAT_ORIGIN` (§T.45); this app has none, so `buildFramingHeaders()` takes no argument
and no variable in the shared repo-root `.env` can widen it — `tests/next-config-headers.test.ts`
asserts that the embed's variable means nothing here.

No `X-Frame-Options`: `frame-ancestors` supersedes it wherever both are read, and one added later
would be honoured *instead* by a client that reads XFO first. Its absence is asserted, not assumed.
`tests/framing-live.server.test.ts` proves both against a running server rather than against the
config object — a `headers()` entry Next never applies looks the same from inside the process.

## Status

Scaffold is `SPEC.md` §T.47; the ported admin pages are §T.48; the framing header is §T.49. Still
open: the auth/session plumbing, `/api/*` proxy and login route (§T.50).
