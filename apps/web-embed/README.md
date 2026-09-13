# apps/web-embed

Next.js 16 app on NOA origin. Hold approval card and big-result table surface.

Scaffold, session proxy, framing header, approval card, poll loop and receipt, 401 link-out and big-result table: all built. Render gate that block them all break open 2026-08-08: LibreChat put frame `src` on this app origin, `noa_session` cookie ride in, in-frame `fetch` POST authenticate.

| Route | Purpose |
|---|---|
| `/healthz` | Liveness. Touch nothing else. |
| `/api/[...path]` | Same-origin proxy to NOA API. Allowlisted, see below. |
| `/tables/[token]` | Large READ result table |
| `/approvals/[id]` | Approval card. Reason input live here, nowhere else. |

## The card

`/approvals/[id]` read incoming `Cookie` header, fetch card from API **server-side** (`src/app/approvals/[id]/page.tsx` → `src/lib/approvals/detail.ts`), so HTML that reach frame already be authenticated card — no moment where operator stare at empty box, and CSRF token come with render, not through second trip page could make before being allowed to read request.

Token come from card own `GET /action-requests/{id}` — no minting route — and API send `null` for anything already decided or expired — so card with no live token draw no reason box, no buttons.

That first read seed `src/app/approvals/[id]/card-view.tsx`, which draw everything then keep asking. Reason `<textarea>` and two `<button type="button">` POST decision by `fetch` (`decision-controls.tsx`). **No `<form>` anywhere in that tree** — rule, not taste: sandbox LibreChat put on this frame leave out `allow-forms` (measured live), so native submit do nothing at all. Browser lane prove both ways: `e2e/approvals.browser.e2e.ts` click Approve inside frame carrying that exact sandbox string and read upstream hit counter, and second document in *same* sandbox show `fetch` arrive where form submit do not.

## Following the run

Approve give back 202 and change run somewhere else entirely; state live in database, never in that connection. So card re-read own row through `/api/*` proxy (`src/lib/approvals/poll.ts`) until nothing left to wait for, and outcome land on URL that ask question.

| State | What happens |
|---|---|
| `PENDING` | re-read every 15s — only change available be expiry sweep, and expired card must stop offering decision door would refuse |
| `APPROVED` + run `STARTED` | re-read every 2s — somebody click Approve and watch |
| `APPROVED` + run `COMPLETED`/`FAILED`, `DENIED`, `EXPIRED` | stop |
| unknown status | stop — build that cannot say what status mean cannot say what would end it |

401 or 404 found mid-poll swap card for same explicit state first read would draw: session that die under open frame must not leave live Approve button standing. Transient failure be the one answer that change nothing — card stay, loop keep going, because "NOA could not be reached just now" not same as "nothing more to wait for".

## When NOA does not know who you are

Two mechanisms authenticate two different principals. LibreChat server reach `/mcp` with per-user bearer token; this card be document in **operator browser** and authenticate with `noa_session` cookie. Browser never hold MCP token, so 401 in here mean operator have working LibreChat token and no NOA browser session.

Remedy live outside this app — no login page, no LDAP form, no credential handling — and *no LDAP redirect inside iframe*. So `sign-in-notice.tsx` draw three things and navigate nothing: "Sign in to NOA" link that open **new top-level tab**, same address printed as text, and **Try again** that re-read card in place through same reader poll use (`lib/approvals/poll.ts`). Session picked up in other tab turn notice into card without frame going anywhere.

**Address printed as well as linked, and that measured, not belt-and-braces.** LibreChat frame this card at two render sites and only one grant `allow-popups` (measured: `ToolCallInfo` = `allow-scripts allow-same-origin`, `MCPUIResource` = that plus `allow-popups`). Where it missing, `target="_blank"` click get refused with nothing operator can see — same silent failure form submit hit, one mechanism over — so printed address be what make state answerable there. `e2e/approvals.browser.e2e.ts` assert both: no tab open under first string, tab do open under second, which be control that keep first from passing against broken link.

That same lane record one more thing about tab a click *do* open: it **inherit frame sandbox**, so native `<form>` submit inside it reach nothing (measured 2026-08-09 — probe document `fetch` arrive, its form submit do not, tab never navigate). Login page reached by clicking therefore only usable if it post by `fetch`; one reached by copying address into fresh tab be ordinary document. Two doors, and printed one be door that depend on none of this.

Address come from `NOA_SIGN_IN_URL` (repo-root `.env`), read **per request** on server and passed into card — not baked like framing origin, and on purpose not `NEXT_PUBLIC_*`. Absent, blank, or anything not `http`/`https` URL draw **no link at all** (`src/lib/sign-in.ts`): door to host nobody deployed read as action that got refused, and `javascript:` value would be script execution set by environment variable.

**Run poll capped at 150 reads (~5 minutes), pending poll not capped.** `PENDING` request have server-side terminator in sweep, and `STARTED` run have one too — its executor write terminal status, its reaper resolve run whose process die. Both terminators measured in minutes to quarter hour, longer than this cap, so cap still speak first on slow change. Giving up say *"NOA is still running this change. Reload this card to check again."* — never that it fail, because no evidence of that.

**Receipt draw as two halves, never one word** (`DECISIONS.md` section 6.5). Every approved change that reach terminal state write `action_receipts` row state — before-state operator approve against, and what change actually do. Card `GET` carry it under `receipt` and card show pair: before-state where gate preflight evidence already shown, and *What the change did* section beside run with verdict, named cause when there be one, and after-state as data. Change that fail keep its before-state — the case a "done"-only card would drop.

Until receipt exist, section absent, not empty: nothing recorded outcome, and empty outcome block would be claim NOA cannot make.

Blankness of reason not judged here. That gate sit on endpoint (409 `change_reason_required`, checked under row lock against same rule database CHECK hold), so card submit what got typed and draw refusal.

401 state be `src/components/sign-in-notice.tsx`, shared with table surface — two copies of "cannot authenticate here" would be two places for printed address to go missing from one.

## The table

`/tables/[token]` be where big READ rows get read. Tool whose answer be listing — `whm_list_accounts`, `pmg_whitelist_list` — park rows in `tool_result_tables` and answer model with short summary plus this page address, so body cost no tokens and never enter transcript LibreChat administrator can read. Both built: `whm_list_accounts` be this route first producer, `pmg_whitelist_list` be second — different system, different transport, same page, no per-tool branch anywhere in it.

Read same way as card: page be **server component**, it forward incoming `Cookie` header (`src/lib/tables/detail.ts`), and HTML that reach frame already be operator table. Nothing poll — parked table written once — so this surface need **no new proxy entry**, and allowlist below still four.

**Read-only, and absences asserted by name**: no Approve, no Deny, no reason box, no `<form>`, no `input`, no CSRF token. Nothing here to authorise; approval card be surface that decide. Browser lane (`e2e/tables.browser.e2e.ts`) draw table inside measured sandbox and read stub counter to show page issue no POST at all — asserted on counter, not on exception, because request never made throw nothing.

**Bound be on page, always**. Parked table hold at most `RESULT_TABLE_MAX_ROWS` rows and store count *before* that cut, so page say either "1,240 rows, all of them shown" or "1,240 rows matched. This page shows the first 25" — one sentence, two numbers, never warning that only show up when something got dropped. `RESULT_TABLE_TTL_SECONDS` be how long address stay live; past it surface answer exactly as for unknown token, which also be its answer for another operator token and for one whose requester got deleted.

## Reaching the API

Browser never call FastAPI direct (`AGENTS.md`). It call `/api/*` on this origin and proxy forward request server-side to `NOA_API_URL`, carrying `noa_session` cookie registrable domain already put here. That be what let in-frame decision POST be plain same-origin `fetch` with no CORS surface to configure.

`NOA_API_URL` server-only, no `NEXT_PUBLIC_*` fallback. It come from repo-root `.env`, which `next.config.ts` load by `config/root-env.ts` — anything already in real environment win, and on purpose no per-app `.env.local` (two files for one deployment be two places for value to disagree).

**Proxy carry allowlist, not whole API** (`src/lib/proxy/routes.ts`):

| Method | Path | For |
|---|---|---|
| `GET` | `/api/auth/me` | identity, and 401 state |
| `GET` | `/api/action-requests/{id}` | poll run to terminal |
| `POST` | `/api/action-requests/{id}/approve` | the decision |
| `POST` | `/api/action-requests/{id}/deny` | the decision |

Anything else answer `404 route_not_proxied` and never get forwarded. This be only NOA origin LibreChat may frame — admin app answer `frame-ancestors 'none'` — so pass-through proxy would put `POST /auth/login` on origin with no credential handling, and `/admin/*` inside frame with operator cookie. Adding route mean editing `routes.ts` and pinned list in `routes.test.ts` — on purpose, not by accident. `Authorization` stripped on way out for same reason: MCP bearer tokens be LibreChat to send, never browser.

## Who may frame it

Every response carry `Content-Security-Policy: frame-ancestors <chat origin>`, set by `headers()` in `next.config.ts` from `config/framing.ts`. One entry on `/(.*)`, so pages, `/api/*` proxy, `/healthz` and 404 all covered — route added later inherit guard instead of having to remember it.

No `X-Frame-Options`: `frame-ancestors` beat it, and `ALLOW-FROM` — only form that could name single origin — be dead. Adding one back would break framing in client that honour XFO over CSP, so its absence asserted.

Origin come from `NOA_LIBRECHAT_ORIGIN` (repo-root `.env`), default `https://chat.noa.internal` — same value `.env.example` and `core/config.py` carry. Absent or blank fall back to that default, so header never missing; value that would widen allowlist (wildcard, second origin, path, bare hostname) **throws**, which under `next build`/`next dev` be build or boot failure, not header nobody meant.

**It baked at build time.** `output: 'standalone'` never run `next.config.ts` at runtime, so runtime variable cannot widen allowlist — and cannot change it either. Moving LibreChat to different origin mean rebuild with new value (build arg).

Browser-level check be `e2e/framing.browser.e2e.ts`: one parent server (`e2e/support/framing-parent.mjs`) reached under two hostnames, so parent origin be only variable between frame that load and frame Chromium refuse. Both names point at loopback with `--host-resolver-rules` flag; serving parent from intercepted response instead make *both* frames fail with `ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS` before any response — which would make refusal pass with no CSP header in existence.

## Stack

Next.js 16, React 19, TypeScript, hand-written CSS. **No design system**: BIGSU live in `apps/admin-web`; this app draw form and two buttons inside small iframe, and full design system there be cost with no return. `eslint.config.mjs` hold that decision as rule — adopting BIGSU here mean deleting rule on purpose.

Independent package: own `package.json`, own `pnpm-lock.yaml`, own CI, own deploy artifact. Share no source, deps or aliases with `apps/admin-web` — also eslint rule.

Every dependency pinned exact — `next`, `react` and `react-dom` by name — because bump on any of them re-open render gate against pinned LibreChat commit. `tests/pins.test.ts` hold it.

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

E2e lane boot three processes itself: dev server, stub upstream on `127.0.0.1:8099` (`e2e/support/upstream-stub.mjs`, which `NOA_API_URL` point at) and framing parent on `127.0.0.1:8110` (`e2e/support/framing-parent.mjs`). It do not reuse dev server someone else start — reused one got different upstream and different `NOA_LIBRECHAT_ORIGIN`, and specs would then measure something else.

Port 3001 be part of contract, not preference: it be origin API build approval URLs from (`NOA_EMBED_BASE_URL` in repo-root `.env.example`), and session cookie scoped `Domain=.noa.internal` so it reach both apps.

Playwright need browser once: `pnpm exec playwright install chromium`.

## Still to come

Nothing of this app own. `/tables/[token]` have two producers in production: `whm_list_accounts` and `pmg_whitelist_list` — both API-side, neither need change here.

This app have no login page, no LDAP form, no credential handling, and nothing in it navigate frame — see *When NOA does not know who you are* above.

`AGENTS.md` and `CLAUDE.md` in this directory written by `next dev` itself and committed so tree stay clean; see note inside them.