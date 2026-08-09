# apps/web-embed

Next.js 16 app served on the NOA origin. Hosts the approval card and the large-result table surface.

Scaffolded at `SPEC.md` §T.40; the session proxy landed at §T.44, the framing header at §T.45, the
approval card at §T.41, its polling loop and receipt at §T.42, and the 401 link-out at §T.43. Still
to come: the table surface (§T.56).
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

`/approvals/[id]` reads the incoming `Cookie` header and fetches the card from the API **server-side**
(`src/app/approvals/[id]/page.tsx` → `src/lib/approvals/detail.ts`), so the HTML that reaches the
frame is already the authenticated card — no moment where an operator watches an empty box, and the
CSRF token arrives as part of the render rather than through a second round trip a page could make
without having been allowed to read the request first.

The token comes from the card's own `GET /action-requests/{id}` (§T.46: there is no minting route),
and the API sends `null` for anything already decided or expired — so a card with no live token
renders no reason box and no buttons.

That first read seeds `src/app/approvals/[id]/card-view.tsx`, which renders everything and then
keeps asking. A reason `<textarea>` and two `<button type="button">` elements POST the decision via
`fetch` (`decision-controls.tsx`). **There is no `<form>` anywhere in that tree**, and that is V80
rather than taste — the sandbox LibreChat applies to this frame omits `allow-forms` (R13, measured
live at R29), so a native submit would do nothing at all. The browser lane proves it both ways:
`e2e/approvals.browser.e2e.ts` clicks Approve inside a frame carrying that exact sandbox string and
reads the upstream's hit counter, and a second document in the *same* sandbox shows a `fetch`
arriving where a form submit does not.

## Following the run (§T.42, V29)

Approve returns 202 and the change runs somewhere else entirely; the state lives in the database,
never in that connection. So the card re-reads its own row through the `/api/*` proxy
(`src/lib/approvals/poll.ts`) until there is nothing left to wait for, and the outcome lands on the
URL that asked the question (V34).

| State | What happens |
|---|---|
| `PENDING` | re-read every 15s — the only change available is the expiry sweep (V32), and an expired card must stop offering a decision the door would refuse |
| `APPROVED` + run `STARTED` | re-read every 2s — somebody clicked Approve and is watching |
| `APPROVED` + run `COMPLETED`/`FAILED`, `DENIED`, `EXPIRED` | stop |
| an unrecognised status | stop — a build that cannot say what a status means cannot say what would end it |

A 401 or 404 discovered mid-poll replaces the card with the same explicit state the first read would
have rendered (V38, V27): a session that expires under an open frame must not leave a live Approve
button standing. A transient failure is the one answer that changes nothing — the card stays and the
loop keeps going, because "NOA could not be reached just now" is not "there is nothing more to wait
for".

## When NOA does not know who you are (§T.43, V38, V42)

Two mechanisms authenticate two different principals. LibreChat's server reaches `/mcp` with a
per-user bearer token (C5); this card is a document in the **operator's browser** and authenticates
with the `noa_session` cookie (V22, V40). The browser never holds the MCP token, so a 401 in here
means the operator has a working LibreChat token and no NOA browser session.

V42 puts the remedy outside this app — no login page, no LDAP form, no credential handling — and
§T.43 adds *no LDAP redirect inside the iframe*. So `sign-in-notice.tsx` renders three things and
navigates nothing: a "Sign in to NOA" link that opens a **new top-level tab**, the same address
printed as text, and a **Try again** that re-reads the card in place through the same reader the poll
uses (`lib/approvals/poll.ts`). A session picked up in the other tab turns the notice into the card
without the frame going anywhere.

**The address is printed as well as linked, and that is measured rather than belt-and-braces.**
LibreChat frames this card at two render sites and only one grants `allow-popups` (R13, R29:
`ToolCallInfo` = `allow-scripts allow-same-origin`, `MCPUIResource` = that plus `allow-popups`).
Where it is absent a `target="_blank"` click is refused with nothing the operator can see — V80's
failure shape, one mechanism over — so the printed address is what makes the state answerable there.
`e2e/approvals.browser.e2e.ts` asserts both: no tab opens under the first string, and a tab does open
under the second, which is the control that keeps the first from passing against a broken link (V87).

That same lane records one more thing about the tab a click *does* open: it **inherits the frame's
sandbox**, so a native `<form>` submit inside it reaches nothing (measured 2026-08-09 — the probe
document's `fetch` arrived, its form submit did not, and the tab never navigated). A login page
reached by clicking is therefore only usable if it posts by `fetch`; one reached by copying the
address into a fresh tab is an ordinary document. Two doors, and the printed one is the door that
does not depend on any of this.

The address comes from `NOA_SIGN_IN_URL` (repo-root `.env`), read **per request** on the server and
passed into the card — not baked like the framing origin, and deliberately not `NEXT_PUBLIC_*`.
Absent, blank, or anything that is not an `http`/`https` URL renders **no link at all**
(`src/lib/sign-in.ts`): a door to a host nobody deployed reads as an action that was refused, and a
`javascript:` value would be script execution configured by environment variable.

**The run poll is capped at 150 reads (~5 minutes) and the pending poll is not.** A `PENDING` request
has a server-side terminator in the sweep, and since §T.38 a `STARTED` run has one too — its executor
writes a terminal status, and its reaper resolves a run whose process died. Both terminators are
measured in minutes to a quarter of an hour, which is longer than this cap, so the cap still speaks
first on a slow change. Giving up says *"NOA is still running this change. Reload this card to check
again."* — never that it failed, because there is no evidence of that.

**The receipt renders as two halves, never as one word** (§T.42(b), V46, DECISIONS §6.5). §T.36
built `action_receipts` and §T.38 writes a row for every approved change that reaches a terminal
state — the before-state the operator approved against, and what the change actually did. The card's
`GET` carries it under `receipt` and the card shows the pair: the before-state where the gate's
preflight evidence was already displayed, and a *What the change did* section beside the run with the
verdict, the named cause when there is one, and the after-state as data. A change that failed keeps
its before-state, which is the case a "done"-only card would drop.

Until a receipt exists the section is absent rather than empty: nothing has recorded an outcome, and
an empty outcome block would be a claim NOA cannot make.

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

The large-result table surface (§T.56).

This app has no login page, no LDAP form and no credential handling, and nothing in it navigates the
frame (V38, V42, §T.43) — see *When NOA does not know who you are* above.

`AGENTS.md` and `CLAUDE.md` in this directory are written by `next dev` itself and committed so the
tree stays clean; see the note inside them.
