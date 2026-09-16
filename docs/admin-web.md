# apps/admin-web — BIGSU admin panel

Rock for admin panel: what it be, where it come from, what build or deploy must do. Why-thinking live in `DECISIONS.md` — this file say none of it again. Landed as scaffold, port from `noa-old`, framing header, session plumbing.

## What it is

Next.js 16 + BIGSU. Users, roles, tokens, WHM/Proxmox/PMG servers, audit (action requests, tool runs, receipts). One of three deployables, own `package.json`, lockfile, CI job, deploy artifact. Share no source with `apps/web-embed` — `eslint.config.mjs` hold rule, `tests/import-firewall.test.ts` hit rule with club.

Never framed: send `Content-Security-Policy: frame-ancestors 'none'` — rule be `config/framing.ts`, put on by `next.config.ts` `headers()`. See "Framing" below.

## Ported, not written

Panel come from `noa-old` branch `MCP`, `apps/web-bigsu/`, copied not rewrote. Old app admin verticals come with own test suites, tests come with code — 50 files, 371 tests.

Four deviations on purpose from source:

1. **Chat gone.** `lib/assistant`, `components/assistant`, `@assistant-ui/*` deps no come. NOA chat live in outside LLM UI now. Nav first entry go too.
2. **Audit receipt keep helpers.** `audit-receipt-page.tsx` be one admin file reach into chat lib, for receipt parse/redact, PNG/clipboard export, evidence disclosure. Three modules move to `lib/admin/audit/` and `components/admin/audit/`, two evidence types they need drop `Assistant` prefix (`receipt-evidence.ts`). Two coercers they use now in `lib/admin/shared/coerce.ts` next to ones server verticals already share.
3. **Role-denied render, no redirect.** Old app send verified non-admin to chat landing. Every route here admin-only, so redirect land on other admin-only route and loop forever. `useVerifiedAuth` give `forbidden` status, route render `ForbiddenView`. FastAPI RBAC still true chief of authorization — this only paint.
4. **`/` and "home" point at `/admin/users`.** `/admin` have no page of own, so nav parent and every home link aim at first vertical, not 404.

## Registry access and CI

BIGSU publish `@gio/bigsu-{tokens,icons,ui,app-shell}` on `https://bigsu.biznetgio.pt/registry/`. `.npmrc` map scope; map be scoped on purpose, so public deps keep come from public registry.

Install need no login — **inside network be the wall**, so no token committed and none should be added. What this mean for CI, measured in old repo and same here: `bigsu.biznetgio.pt` point to inside-only address, so runner on open internet cannot install `@gio/*` at all. Build must run on runner inside Biznet Gio network.

One thing for whoever wire embed image build: its framing origin come in at build time under two names (`NOA_LIBRECHAT_ORIGIN` and `NEXT_PUBLIC_NOA_LIBRECHAT_ORIGIN`, both baked into output), so embed image tied to environment it built for and cannot move staging→production by retag — each environment build own. Detail in `docs/embed-frame.md`.

CI for this repo live on company GitLab (`gitlab.biznetgio.pt:simondayce/noa`, branches `master` and `staging`) outside this file; this file say the need, it add no workflow. GitHub remote (`origin`, `rendyuwu/noa`) be work and pull-request ground, not CI ground, so `.github/workflows/*` no place where CI need get met — GitLab pipeline must meet it as spec. Runner need: **Node 22**, pnpm by Corepack (`packageManager` field pin version), fresh workspace each job, no registry credential in pipeline, inside-runner jobs only on protected branches — runner with road to inside registry that also run untrusted merge requests be poison target.

Node 22 and not "Node 20 LTS" this line say before: two clauses fight each other. `engines.node` allow `>=20.9.0` and `next@16.3.0` agree, so 20 run built app — but pinned pnpm 11.x declare `engines.node: >=22.13`, and on Node 20 `pnpm install` die with `ERR_UNKNOWN_BUILTIN_MODULE: No such built-in module: node:sqlite` before resolve one package. Measured while build this app image; `apps/api/tests/test_deployment.py` now hold floor, and hold it to pnpm 11 only, so pnpm major bump must re-measure, not inherit.

Credential-free metadata read, from inside-capable environment:

```console
$ npm view @gio/bigsu-ui version --registry=https://bigsu.biznetgio.pt/registry/
1.0.3
```

## Config and health

Config come from repo-root `.env` by `config/root-env.ts`; no per-app `.env.local`, because two env files for one deployment be two places for `NOA_API_URL` to fight. Anything already set in real environment beat file.

- `NOA_API_URL` — API that same-origin `/api/*` proxy send to (server-side only).
- Dev port 3000. `apps/web-embed` own 3001, and root `.env.example` already point `NOA_SIGN_IN_URL` at `http://localhost:3000/login`.

`/healthz` be liveness only: touch no dependency, so backend outage never blamed on this app. There be **no readiness probe, and there will not be one** — call made, not kicked down road. A `/readyz` that read `NOA_API_URL` make this app readiness depend on other deployable health, so rolling API restart yank both web apps out of rotation same time, and this app need no API to serve error and 401 states operator see in exactly that window. Readiness that mean something get said as ordering instead: `docs/deployment.md`. Older draft of this file promise probe here; promise never match decision.

## Session and the API hop

Browser never call FastAPI straight (AGENTS.md). Every call go to `/api/*` on this origin, and `src/app/api/[...path]/route.ts` pass it server-side to `NOA_API_URL` with httpOnly `noa_session` cookie that registrable domain put here. `Set-Cookie` ride back with `Domain` attribute untouched — whatever deployment parent be (`.noa.internal` in dev, `.simondayce.my.id` deployed) — because rewrite it lock session to whichever origin answer, and embed approval card stop see same login.

`Authorization` dropped outbound. Bearer tokens be MCP-only and LibreChat server send them, never browser, so `/api/mcp/` reachable on this origin be reachable-and-dead, not relay. Primitives (`src/lib/proxy/http.ts`) be `noa-old` layer copied with its tests, not imported from `apps/web-embed`, which carry same file: two apps be separate packages and same-file twice across that line be boundary working right.

**Pass-through, not embed allowlist, and difference argued not inherited.** Embed allowlist four routes because it be the one NOA origin LibreChat may frame — pass-through there put `/auth/login` and `/admin/*` inside that frame with operator cookie. This app answer `frame-ancestors 'none'`, so no foreign document can drive it, and what it need be admin API contract whole: users, roles, tokens, three server verticals, audit, `/auth/*`, `/me/mcp-tokens`. Allowlist must get edited on every new vertical to stay right, and stale entry there die as 404 panel cannot explain.

`/login` be LDAP sign-in form. It sit outside `(protected)` — that layout run `/auth/me` gate, and login page behind it 401 its way back to itself. `session.ts::clearAuth` be what send operator here, with `?reason=` and cleaned `?returnTo=` (`return-to.ts`, reused).

**Main action be `type="button"` click handler, not form submit.** Not style choice. `NOA_SIGN_IN_URL` point at this route, and one of two ways operator arrive be click on embed 401 card link-out. Measured: that tab be top-level, but it inherit frame sandbox, and `allow-forms` missing at both LibreChat render sites. Sandboxed document turn back at sandbox check *before* `submit` event fire, so submit-driven login refused with nothing operator can see — same missing-`allow-forms` death shape, one origin over. `<form>` stay and route to same handler for whatever still dispatch submit, e.g. password manager that call `requestSubmit()`. It carry no `action`: no non-JS path exist that would post credential anywhere.

**Rule cover whole app, not login page only.** Sandbox survive same-origin navigation, so every page operator reach from that tab inherit it, and submit-driven Save in any dialog die same silent death (seen live: WHM "add server" print `Blocked form submission to ''`, nothing else). Every primary action be `type="button"` with `onClick` that call same react-hook-form handler as `<form onSubmit>`. `src/primary-action-not-submit.test.ts` scan `src/**/*.tsx` and ban 2 attribute: `type="submit"`, and `form="..."` that associate button with form it not live inside (`<button>` with no `type` inside form default to submit, so ban sit on association, not on word). Mutation watched red: `type="submit"` back on WHM Save button redden that scan with path in message. Scan bind SHAPE, not wiring — it render nothing, and Proxmox and PMG dialog have no click test at all. **Enter put back by hand, in JS** (`src/lib/forms/submit-on-enter.ts`), because rule take it away. Form with no submit button associated get nothing from browser implicit submission unless it carry exactly 1 field that block submission, and these form carry more. Doc here and comment in `login-form.tsx` used to claim Enter work in fresh copied-address tab. It not, in any tab. Measured, Chromium 151, rendered login page: Enter in email field and Enter in password field each give 0 `submit` event and 0 request; click on button give 1 request. After helper: Enter in each field give 1 request, still 0 `submit` event. Helper fire on Enter in text-type `<input>` only — same set browser use for implicit submission — so `<textarea>` keep newline (WHM private key) and every Radix trigger (Select, MultiSelect, Checkbox, RadioGroup = `<button>`) keep own Enter.

Hidden submit button would NOT have worked, measured: inside sandbox without `allow-forms`, Enter on that shape give `handler=0 submits=0`, 1 `Blocked form submission` log; JS keydown path on same sandbox give `handler=1 submits=0`, 0 log. That is why Enter go through keydown, not through hidden control.

CI cannot repeat those 3 measurement. jsdom not implement implicit submission at all, and `apps/web-embed` own only Playwright lane — 2 package share nothing, so no browser runner here. `src/lib/forms/submit-on-enter.test.ts` bind helper decision (which key, which modifier, which element), `src/app/login/login-form.test.tsx` bind that Enter reach handler with 0 submit event, and `src/primary-action-not-submit.test.ts` count 1 `submitOnEnter(` per `<form>`. Native half stay hand-measured. Stated, not dressed as coverage.

What that lane can claim be fenced on purpose. `src/app/login/login-form.test.tsx` prove POST happen with **zero** `submit` events, and pair it with submit-driven fixture that record one — else "zero" pass against button that do nothing. It no re-measure LibreChat sandbox: jsdom cannot enforce one, and that measure live in `apps/web-embed/e2e/sign-in.browser.e2e.ts`, took at both pinned sandbox strings.

Refusal words be `login-messages.ts`. Every refused credential get one foggy pair whatever cause, so page cannot be used to learn which half wrong; states that be *not* wrong credential (pending approval, rate limited, LDAP unreachable) stay apart, because retry password fix none of them. Backend `detail` never echoed.

`tests/proxy-live.server.test.ts` prove hop on wire against recording stub upstream — what land, not what mocked `fetch` got handed. Mutations proven red before it land: `Authorization` drop removed (stub then see bearer), and sign-in button changed to `type="submit"` (escape-hatch specs then see one `submit` event and wrong `type` attribute).

## Framing

`Content-Security-Policy: frame-ancestors 'none'` on every response. Rule be `config/framing.ts`; `next.config.ts` return it from `headers()` as ONE entry on `/(.*)`, so pages, `/login`, `/healthz`, 404 and `/api/*` proxy covered by machine, not by each route remember a guard.

It have no config, which be difference from embed copy: `apps/web-embed` name one lawful parent by `NOA_LIBRECHAT_ORIGIN`, and this app have none, so `buildFramingHeaders()` take no argument and no variable in shared repo-root `.env` reach it. `tests/next-config-headers.test.ts` say embed variable mean nothing here.

No `X-Frame-Options`: `frame-ancestors` beat it wherever both read, and one added later get honoured *instead* of this header by client that read XFO first. Absence asserted, not assumed.

`tests/framing-live.server.test.ts` boot `next dev` on OS-picked free port and read header off `/healthz`, `/` (a 307), `/admin/users`, `/login`, `/api/auth/me` and a 404 — last two added with session work, and they be two this app most need covered: login route be address `NOA_SIGN_IN_URL` send operator to from inside LibreChat frame, so it be the one page anyone have reason to try to frame, and proxy be surface that carry cookie if they win. Machine that embed framing headers measured be same one, but sibling package measure be no proof about this one. Its readiness gate be TCP connect, not request to route under test: gate pointed at subject turn subject failure into timeout. Mutations proven red before it land: value changed to `'self'`, source narrowed to `/admin/:path*` (which leave `/healthz`, `/` and 404 naked), header key renamed to `X-Frame-Options`, `headers()` dropped from config, and server itself never started — that last one fail lane, not skip quiet.

## Checks

```bash
pnpm install && pnpm lint && pnpm typecheck && pnpm test && pnpm build
pnpm test:server   # the lane that boots a server; own config, excluded from `pnpm test`
```

Package-level guards live in `tests/`: `pins.test.ts`, `npmrc.test.ts` (registry routing), `hygiene.test.ts` (file-size caps) and `import-firewall.test.ts` (deploy-boundary and port-not-import rules).

## Not here yet

- **Admin API routes the ported pages call.** Pages, hooks, their tests be here and proxy now carry them; several admin API contract endpoints behind them not built yet.
- **Readiness probe.** Not coming. Ruled out and why written down — see "Config and health" above and `docs/deployment.md`.
- **Browser e2e for admin verticals.** They stay in old repo. Session plumbing give them target (proxy and login route), but this package have no Playwright lane: `apps/web-embed` own browser lane today, and add one here be own decision with own dependency and CI cost. Till then jsdom specs be what run, and one place that matter — sign-in control standing free of form submit — be fenced as said above, not hinted.