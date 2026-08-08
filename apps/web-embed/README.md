# apps/web-embed

Next.js 16 app served on the NOA origin. Hosts the approval card and the large-result table surface.

Scaffolded at `SPEC.md` §T.40. The routes it exists for are still to come — §T.41–§T.46 (card,
decision POST, 401 state, session proxy, framing headers, CSRF carry) and §T.56 (table surface).
The §T.59 render gate that used to block all of them cleared 2026-08-08 (R29): LibreChat puts the
frame's `src` on this app's origin, the `noa_session` cookie rides in, and an in-frame `fetch` POST
authenticates.

| Route | Purpose | Task |
|---|---|---|
| `/healthz` | Liveness. Touches nothing else. | §T.40 |
| `/approvals/[id]` | Approval card. The reason input lives here and nowhere else (C8, V15). | §T.41 |
| `/action-requests/{id}` | Confirmation detail + execution status polling | §T.42 |
| `/tables/[token]` | Large READ result table (V64) | §T.56 |

## Stack

Next.js 16, React 19, TypeScript, hand-written CSS. **No design system**: §T.47 puts BIGSU in
`apps/admin-web`; this app renders a form and two buttons inside a small iframe, and a full design
system there is cost without return. `eslint.config.mjs` holds that decision as a rule — adopting
BIGSU here means deleting the rule on purpose.

Independent package: own `package.json`, own `pnpm-lock.yaml`, own CI, own deploy artifact. Shares
no source, deps or aliases with `apps/admin-web` (C12, AGENTS.md) — also an eslint rule.

Every dependency is pinned exactly. C2 names `next`, `react` and `react-dom` because a bump on any
of them re-opens the render gate against the pinned LibreChat commit (C21). `tests/pins.test.ts`
holds it.

## Commands

```bash
pnpm install
pnpm dev          # http://localhost:3001
pnpm lint
pnpm typecheck
pnpm test         # vitest
pnpm test:e2e     # playwright — boots the dev server itself
pnpm build
```

Port 3001 is part of the contract, not a preference: it is the origin the API builds approval URLs
from (`NOA_EMBED_BASE_URL` in the repo-root `.env.example`), and the session cookie is scoped
`Domain=.noa.internal` so it reaches both apps (V40).

Playwright needs a browser once: `pnpm exec playwright install chromium`.

## Still to come

`Content-Security-Policy: frame-ancestors https://chat.noa.internal` (§T.45, V41) and the
same-origin `/api/[...path]` proxy the browser reaches NOA through (§T.44). This app has no login
page, no LDAP form and no credential handling — a 401 renders an explicit "cannot authenticate
here" state, never a blank card with a live Approve button (§T.43, V38, V42).

`AGENTS.md` and `CLAUDE.md` in this directory are written by `next dev` itself and committed so the
tree stays clean; see the note inside them.
