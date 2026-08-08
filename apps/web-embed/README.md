# apps/web-embed

Next.js 16 app served on the NOA origin. Hosts the approval card and the large-result table surface.

Scaffolded at `SPEC.md` §T.40; the session proxy landed at §T.44, the framing header at §T.45 and
the approval card at §T.41. Still to come: polling and the receipt (§T.42), the 401 link-out (§T.43)
and the table surface (§T.56).
The §T.59 render gate that used to block all of them cleared 2026-08-08 (R29): LibreChat puts the
frame's `src` on this app's origin, the `noa_session` cookie rides in, and an in-frame `fetch` POST
authenticates.

| Route | Purpose | Task |
|---|---|---|
| `/healthz` | Liveness. Touches nothing else. | §T.40 |
| `/api/[...path]` | Same-origin proxy to the NOA API. Allowlisted, see below. | §T.44 |
| `/approvals/[id]` | Approval card. The reason input lives here and nowhere else (C8, V15). | §T.41 |
| `/tables/[token]` | Large READ result table (V64) | §T.56 |

## The card

`/approvals/[id]` is a **server** component. It reads the incoming `Cookie` header and fetches the
card from the API server-side (`src/lib/approvals/detail.ts`), so the HTML that reaches the frame is
already the authenticated card — no moment where an operator watches an empty box, and the CSRF
token arrives as part of the render rather than through a second round trip a page could make
without having been allowed to read the request first.

The token comes from the card's own `GET /action-requests/{id}` (§T.46: there is no minting route),
and the API sends `null` for anything already decided or expired — so a card with no live token
renders no reason box and no buttons.

The one client component is `src/app/approvals/[id]/decision-controls.tsx`: a reason `<textarea>` and
two `<button type="button">` elements whose handlers POST via `fetch`. **There is no `<form>` in that
tree**, and that is V80 rather than taste — the sandbox LibreChat applies to this frame omits
`allow-forms` (R13, measured live at R29), so a native submit would do nothing at all. The browser
lane proves it both ways: `e2e/approvals.browser.e2e.ts` clicks Approve inside a frame carrying that
exact sandbox string and reads the upstream's hit counter, and a second document in the *same*
sandbox shows a `fetch` arriving where a form submit does not.

Blankness of the reason is not judged here. V15 puts that gate on the endpoint (409
`change_reason_required`, checked under the row lock against the same rule the database CHECK holds),
so the card submits what was typed and renders the refusal.

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
| `GET` | `/api/action-requests/{id}` | polling the run to terminal (§T.42) |
| `POST` | `/api/action-requests/{id}/approve` | the decision (§T.41, V22) |
| `POST` | `/api/action-requests/{id}/deny` | the decision (§T.41, V22) |

Anything else answers `404 route_not_proxied` and is never forwarded. This is the only NOA origin
LibreChat may frame (V41 — the admin app answers `frame-ancestors 'none'`), so a pass-through proxy
would put `POST /auth/login` on an origin V42 says has no credential handling, and `/admin/*` inside
the frame with the operator's cookie. Adding a route means editing `routes.ts` and the pinned list in
`routes.test.ts` — on purpose, not by accident. `Authorization` is stripped on the way out for the
same reason: MCP bearer tokens are LibreChat's to send (C5), never a browser's.

## Who may frame it

Every response carries `Content-Security-Policy: frame-ancestors <chat origin>` (§T.45, V41), set by
`headers()` in `next.config.ts` from `config/framing.ts`. One entry on `/(.*)`, so the pages, the
`/api/*` proxy, `/healthz` and the 404 are all covered — a route added later inherits the guard
instead of having to remember it.

No `X-Frame-Options`: `frame-ancestors` supersedes it, and `ALLOW-FROM` — the only form that could
name a single origin — is dead. Adding one back would break framing in a client that honours XFO
over CSP, so its absence is asserted.

The origin comes from `NOA_LIBRECHAT_ORIGIN` (repo-root `.env`), defaulting to
`https://chat.noa.internal` — the same value `.env.example` and `core/config.py` carry. Absent or
blank falls back to that default, so the header is never omitted; a value that would widen the
allowlist (a wildcard, a second origin, a path, a bare hostname) **throws**, which under
`next build`/`next dev` is a build or boot failure rather than a header nobody meant.

**It is baked at build time.** `output: 'standalone'` never executes `next.config.ts` at runtime, so
a runtime variable cannot widen the allowlist — and cannot change it either. Moving LibreChat to a
different origin means rebuilding with the new value (a build arg for §T.60).

The browser-level check is `e2e/framing.browser.e2e.ts`: one parent server
(`e2e/support/framing-parent.mjs`) reached under two hostnames, so the parent origin is the only
variable between the frame that loads and the frame Chromium refuses. Both names are pointed at
loopback with a `--host-resolver-rules` flag; serving the parent from an intercepted response
instead made *both* frames fail with `ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS` before any
response, which would have made the refusal pass with no CSP header in existence (V90).

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

The e2e lane boots three processes itself: the dev server, a stub upstream on `127.0.0.1:8099`
(`e2e/support/upstream-stub.mjs`, which `NOA_API_URL` points at) and a framing parent on
`127.0.0.1:8110` (`e2e/support/framing-parent.mjs`). It does not reuse a dev server someone else
started — a reused one was given a different upstream and a different `NOA_LIBRECHAT_ORIGIN`, and
the specs would then measure something else.

Port 3001 is part of the contract, not a preference: it is the origin the API builds approval URLs
from (`NOA_EMBED_BASE_URL` in the repo-root `.env.example`), and the session cookie is scoped
`Domain=.noa.internal` so it reaches both apps (V40).

Playwright needs a browser once: `pnpm exec playwright install chromium`.

## Still to come

Polling the run to a terminal state and rendering the receipt (§T.42), the "Sign in to NOA" link-out
beside the 401 state (§T.43), and the large-result table surface (§T.56).

This app has no login page, no LDAP form and no credential handling. A 401 already renders an explicit
"cannot authenticate here" state with no reason box and no buttons (V38, V42); what §T.43 adds is the
new-tab link-out beside it, never a form in the frame.

`AGENTS.md` and `CLAUDE.md` in this directory are written by `next dev` itself and committed so the
tree stays clean; see the note inside them.
