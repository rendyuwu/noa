# apps/admin-web — BIGSU admin panel

Reference for the admin panel: what it is, where it came from, and what a build or deploy has to
satisfy. Design rationale lives in `DECISIONS.md` — this file restates none of it. Landed as the
scaffold, the port from `noa-old`, the framing header and the session plumbing.

## What it is

Next.js 16 + BIGSU. Users, roles, tokens, WHM/Proxmox/PMG servers, audit (action requests, tool
runs, receipts). One of three deployables, with its own `package.json`, lockfile, CI job and
deploy artifact. It shares no source with `apps/web-embed` — `eslint.config.mjs` holds that as a
rule and `tests/import-firewall.test.ts` exercises the rule.

Never framed: it sends `Content-Security-Policy: frame-ancestors 'none'` — the rule is
`config/framing.ts`, applied by `next.config.ts`'s `headers()`. See "Framing" below.

## Ported, not written

The panel came from `noa-old` branch `MCP`, `apps/web-bigsu/`, copied rather than rewritten. The
old app's admin verticals shipped with their own test suites, and those came across with the code —
50 files, 371 tests.

Four deliberate deviations from the source:

1. **Chat is gone.** `lib/assistant`, `components/assistant` and the `@assistant-ui/*` dependencies
   did not come across; NOA's chat surface lives in an external LLM UI now. The nav's first entry
   went with it.
2. **The audit receipt kept its helpers.** `audit-receipt-page.tsx` was the one admin file reaching
   into the chat lib, for receipt parsing/redaction, the PNG/clipboard export and the evidence
   disclosure. Those three modules moved to `lib/admin/audit/` and `components/admin/audit/`, and
   the two evidence types they need dropped their `Assistant` prefix (`receipt-evidence.ts`). The
   two coercers they used are now in `lib/admin/shared/coerce.ts` beside the ones the server
   verticals already shared.
3. **Role-denied renders instead of redirecting.** The old app sent a verified non-admin to the
   chat landing. Every route here is admin-only, so that redirect would land on another admin-only
   route and loop. `useVerifiedAuth` resolves to a `forbidden` status and the route renders
   `ForbiddenView`. FastAPI RBAC is still the authorization source of truth — this is presentation
   only.
4. **`/` and "home" point at `/admin/users`.** `/admin` has no page of its own, so the nav parent
   and every home link target the first vertical rather than a 404.

## Registry access and CI

BIGSU publishes `@gio/bigsu-{tokens,icons,ui,app-shell}` on `https://bigsu.biznetgio.pt/registry/`.
`.npmrc` maps the scope; the mapping is scoped deliberately, so the public dependencies keep
resolving from the public registry.

Installs need no login — **the internal network is the access boundary**, which is why no token is
committed and none should be added. The consequence for CI, measured in the old repo and unchanged
here: `bigsu.biznetgio.pt` resolves to an internal-only address, so a runner on the public internet
cannot install `@gio/*` at all. The build must run on a runner inside the Biznet Gio network.

CI for this repo is maintained on company GitLab (`gitlab.biznetgio.pt:simondayce/noa`, branches
`master` and `staging`) out of band; this file states the requirement, it does not add a workflow.
The GitHub remote (`origin`, `rendyuwu/noa`) is the working and pull-request surface, not the CI
surface, so `.github/workflows/*` is not where a CI requirement gets satisfied — the GitLab pipeline
must meet it as a specification. Runner expectations: **Node 22**, pnpm via Corepack
(the `packageManager` field pins the version), an ephemeral workspace per job, no registry
credential in the pipeline, and internal-runner jobs restricted to protected branches — a runner
with routable access to the internal registry that also runs untrusted merge requests is a
poisoning target.

Node 22 and not the "Node 20 LTS" this line used to say: those two clauses contradicted each
other. `engines.node` allows `>=20.9.0` and `next@16.3.0` agrees, so 20 runs the built app — but
the pinned pnpm 11.x declares `engines.node: >=22.13`, and on Node 20 `pnpm install` dies with
`ERR_UNKNOWN_BUILTIN_MODULE: No such built-in module: node:sqlite` before resolving a single
package. Measured while building this app's image; `apps/api/tests/test_deployment.py`
now holds the floor, and holds it to pnpm 11 specifically, so a pnpm major bump has to re-measure
rather than inherit.

Credential-free metadata read, from an internal-capable environment:

```console
$ npm view @gio/bigsu-ui version --registry=https://bigsu.biznetgio.pt/registry/
1.0.3
```

## Config and health

Config comes from the repo-root `.env` via `config/root-env.ts`; there is no per-app `.env.local`,
because two env files for one deployment is two places for `NOA_API_URL` to disagree. Anything
already set in the real environment wins over the file.

- `NOA_API_URL` — the API the same-origin `/api/*` proxy forwards to (server-side only).
- Dev port 3000. `apps/web-embed` owns 3001, and the root `.env.example` already points
  `NOA_SIGN_IN_URL` at `http://localhost:3000/login`.

`/healthz` is liveness only: it touches no dependency, so a backend outage is never reported as
this app's. There is **no readiness probe, and there will not be one** — that call was made rather
than deferred further. A `/readyz` that reads `NOA_API_URL` would make this app's readiness a
function of another deployable's health, so a rolling API restart pulls both web apps out of
rotation at once, and this app does not need the API to serve the error and 401 states an operator
sees during exactly that window. Readiness that means something is expressed as ordering instead:
`docs/deployment.md`. An earlier draft of this file promised the probe here; that promise never
matched the decision.

## Session and the API hop

The browser never calls FastAPI directly (AGENTS.md). Every call goes to `/api/*` on this origin,
and `src/app/api/[...path]/route.ts` forwards it server-side to `NOA_API_URL` with the httpOnly
`noa_session` cookie the registrable domain put here. `Set-Cookie` rides back with its
`Domain` attribute untouched — whatever the deployment's parent is (`.noa.internal` in development,
`.simondayce.my.id` deployed) — because rewriting it would confine the session to whichever origin
answered, and the embed's approval card would stop seeing the same login.

`Authorization` is dropped outbound. Bearer tokens are MCP-only and LibreChat's server sends them,
never a browser, so `/api/mcp/` being reachable on this origin is reachable-and-inert rather
than a relay. The primitives (`src/lib/proxy/http.ts`) are the `noa-old` layer copied with its
tests, not imported from `apps/web-embed`, which carries the same file: the two apps are independent
packages and duplication across that line is the boundary working.

**Pass-through, not the embed's allowlist, and the difference is argued rather than inherited.** The
embed allowlists four routes because it is the one NOA origin LibreChat may frame — a
pass-through there would put `/auth/login` and `/admin/*` inside that frame with the operator's
cookie. This app answers `frame-ancestors 'none'`, so no foreign document can drive it, and what it
needs is the admin API's contract in full: users, roles, tokens, three server verticals, audit,
`/auth/*`, `/me/mcp-tokens`. An allowlist would have to be edited on every new vertical to stay
correct, and a stale entry there fails as a 404 the panel cannot explain.

`/login` is the LDAP sign-in form. It sits outside `(protected)` — that layout runs the `/auth/me`
gate, and a login page behind it would 401 its way back to itself. `session.ts::clearAuth` is what
sends an operator here, with `?reason=` and a sanitized `?returnTo=` (`return-to.ts`, reused).

**The primary action is a `type="button"` click handler, not a form submit.** This is not
a style choice. `NOA_SIGN_IN_URL` points at this route, and one of the two ways an operator arrives
is a click on the embed 401 card's link-out. Measured: that tab is top-level, but it inherits the
frame's sandbox, and `allow-forms` is absent at both of LibreChat's render sites. A sandboxed
document returns at the sandbox check *before* the `submit` event fires, so a submit-driven login is
refused with nothing an operator can see — the same absent-`allow-forms` failure shape, one origin
over. The `<form>` stays
and routes to the same handler, because an operator who *copies* the address into a fresh tab
has no opener to inherit from and Enter should work there. It carries no `action`: there is no
non-JS path that would post a credential anywhere.

What that lane can claim is bounded on purpose. `src/app/login/login-form.test.tsx` proves the POST
happens with **zero** `submit` events, and pairs it with a submit-driven fixture that records one —
otherwise "zero" passes against a button that does nothing. It does not re-measure LibreChat's
sandbox: jsdom cannot enforce one, and that measurement lives in
`apps/web-embed/e2e/sign-in.browser.e2e.ts`, taken at both pinned sandbox strings.

Refusal copy is `login-messages.ts`. Every refused credential answers one vague pair whatever the
cause, so the page cannot be used to learn which half was wrong; the states that are *not* a wrong
credential (pending approval, rate limited, LDAP unreachable) stay distinguishable, because
retrying the password is not the remedy for any of them. The backend `detail` is never echoed.

`tests/proxy-live.server.test.ts` proves the hop on the wire against a recording stub upstream —
what arrived, not what a mocked `fetch` was handed. Mutations proven red before it landed: the
`Authorization` drop removed (the stub then sees the bearer), and the sign-in button changed to
`type="submit"` (the escape-hatch specs then see one `submit` event and the wrong `type`
attribute).

## Framing

`Content-Security-Policy: frame-ancestors 'none'` on every response. The rule is
`config/framing.ts`; `next.config.ts` returns it from `headers()` as ONE entry on `/(.*)`, so the
pages, `/login`, `/healthz`, the 404 and the `/api/*` proxy are covered by the mechanism rather than
by each route remembering a guard.

It has no configuration, which is the difference from the embed's copy: `apps/web-embed` names one
legitimate parent through `NOA_LIBRECHAT_ORIGIN`, and this app has none, so
`buildFramingHeaders()` takes no argument and no variable in the shared repo-root `.env` reaches it.
`tests/next-config-headers.test.ts` asserts the embed's variable means nothing here.

No `X-Frame-Options`: `frame-ancestors` supersedes it wherever both are read, and one added later
would be honoured *instead* of this header by a client that reads XFO first. Absence asserted, not
assumed.

`tests/framing-live.server.test.ts` boots `next dev` on an OS-assigned free port and reads the
header off `/healthz`, `/` (a 307), `/admin/users`, `/login`, `/api/auth/me` and a 404 — the last two
added with the session work, and they are the two this app most needs covered: the login route is
the address `NOA_SIGN_IN_URL` sends an operator to from inside a LibreChat frame, so it is the one
page anyone has a reason to try to frame, and the proxy is the surface that would carry a cookie if
they succeeded. The mechanism the embed's framing headers measured is the same one, but a sibling
package's measurement is not evidence about this one. Its readiness gate is a TCP connect, not a
request to a route under test: a gate pointed at
the subject turns the subject's failure into a timeout. Mutations proven red before it landed:
the value changed to `'self'`, the source narrowed to `/admin/:path*` (which leaves `/healthz`, `/`
and the 404 bare), the header key renamed to `X-Frame-Options`, `headers()` dropped from the config,
and the server itself never started — that last one fails the lane instead of skipping quietly.

## Checks

```bash
pnpm install && pnpm lint && pnpm typecheck && pnpm test && pnpm build
pnpm test:server   # the lane that boots a server; own config, excluded from `pnpm test`
```

Package-level guards live in `tests/`: `pins.test.ts`, `npmrc.test.ts` (registry routing),
`hygiene.test.ts` (file-size caps) and `import-firewall.test.ts` (deploy-boundary and
port-not-import rules).

## Not here yet

- **The admin API routes the ported pages call.** The pages, hooks and their tests are
  here and the proxy now carries them; several of the admin API's contract endpoints behind them
  are not built yet.
- **A readiness probe.** Not coming. Ruled out and recorded why — see "Config and
  health" above and `docs/deployment.md`.
- **Browser e2e for the admin verticals.** They stayed in the old repo. The session plumbing gives
  them a target (the proxy and the login route), but this package has no Playwright lane:
  `apps/web-embed` owns
  the browser lane today, and adding one here is its own decision with its own dependency and CI
  cost. Until then the jsdom specs are what run, and the one place that matters — the sign-in
  control's independence from form submission — is bounded as described above rather than implied.
