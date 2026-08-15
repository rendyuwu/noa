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
pnpm build
```

`tests/` holds the package-level guards rather than feature tests: `pins.test.ts` (C2 — exact
versions, no caret ranges), `npmrc.test.ts` (the `@gio` scope resolves from the internal registry),
`hygiene.test.ts` (C14/V65 — `.ts` ≤ 300 lines, `.tsx` ≤ 450) and `import-firewall.test.ts`.

## Status

Scaffold is `SPEC.md` §T.47; the ported admin pages are §T.48. Still open: `frame-ancestors 'none'`
(§T.49 — this app is never framed, V41) and the auth/session plumbing, `/api/*` proxy and login
route (§T.50).
