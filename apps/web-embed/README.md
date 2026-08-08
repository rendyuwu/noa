# apps/web-embed

Next.js 16 app served on the NOA origin. Hosts the approval card and the large-result table surface.

Scaffolded at `SPEC.md` §T.40; the session proxy landed at §T.44. The routes it exists for are still
to come — §T.41–§T.43, §T.45, §T.46 (card, decision POST, 401 state, framing headers, CSRF carry) and
§T.56 (table surface).
The §T.59 render gate that used to block all of them cleared 2026-08-08 (R29): LibreChat puts the
frame's `src` on this app's origin, the `noa_session` cookie rides in, and an in-frame `fetch` POST
authenticates.

| Route | Purpose | Task |
|---|---|---|
| `/healthz` | Liveness. Touches nothing else. | §T.40 |
| `/api/[...path]` | Same-origin proxy to the NOA API. Allowlisted, see below. | §T.44 |
| `/approvals/[id]` | Approval card. The reason input lives here and nowhere else (C8, V15). | §T.41 |
| `/action-requests/{id}` | Confirmation detail + execution status polling | §T.42 |
| `/tables/[token]` | Large READ result table (V64) | §T.56 |

## Reaching the API

The browser never calls FastAPI directly (`AGENTS.md`). It calls `/api/*` on this origin and the
proxy forwards the request server-side to `NOA_API_URL`, carrying the `noa_session` cookie the
registrable domain already put here (V40). That is what lets the in-frame decision POST be a plain
same-origin `fetch` (V22, V80) with no CORS surface to configure.

`NOA_API_URL` is server-only and has no `NEXT_PUBLIC_*` fallback. It comes from the repo-root
`.env`, which `next.config.ts` loads via `config/root-env.ts` — anything already in the real
environment wins, and there is deliberately no per-app `.env.local` (two files for one deployment
is two places for the value to disagree).

**The proxy carries an allowlist, not the whole API** (`src/lib/proxy/routes.ts`):

| Method | Path | For |
|---|---|---|
| `GET` | `/api/auth/me` | identity, and the 401 state (V38, V42) |
| `GET` | `/api/action-requests/{id}` | card detail (§T.41) |
| `POST` | `/api/action-requests/{id}/approve` | the decision (§T.42, V22) |
| `POST` | `/api/action-requests/{id}/deny` | the decision (§T.42, V22) |

Anything else answers `404 route_not_proxied` and is never forwarded. This is the only NOA origin
LibreChat may frame (V41 — the admin app answers `frame-ancestors 'none'`), so a pass-through proxy
would put `POST /auth/login` on an origin V42 says has no credential handling, and `/admin/*` inside
the frame with the operator's cookie. Adding a route means editing `routes.ts` and the pinned list in
`routes.test.ts` — on purpose, not by accident. `Authorization` is stripped on the way out for the
same reason: MCP bearer tokens are LibreChat's to send (C5), never a browser's.

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
pnpm test:e2e     # playwright — boots the dev server and a stub upstream itself
pnpm build
```

The e2e lane points `NOA_API_URL` at a stub on `127.0.0.1:8099` (`e2e/support/upstream-stub.mjs`)
and does not reuse a dev server someone else started — a reused one was given a different upstream,
and the proxy specs would then measure something else.

Port 3001 is part of the contract, not a preference: it is the origin the API builds approval URLs
from (`NOA_EMBED_BASE_URL` in the repo-root `.env.example`), and the session cookie is scoped
`Domain=.noa.internal` so it reaches both apps (V40).

Playwright needs a browser once: `pnpm exec playwright install chromium`.

## Still to come

`Content-Security-Policy: frame-ancestors https://chat.noa.internal` (§T.45, V41). This app has no
login page, no LDAP form and no credential handling — a 401 renders an explicit "cannot authenticate
here" state, never a blank card with a live Approve button (§T.43, V38, V42).

`AGENTS.md` and `CLAUDE.md` in this directory are written by `next dev` itself and committed so the
tree stays clean; see the note inside them.
