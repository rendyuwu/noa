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
`noa_session` cookie the deployment's registrable parent put here. `Authorization` is dropped
outbound: bearer tokens are MCP-only and LibreChat's to send, so `/api/mcp/` is reachable on
this origin and inert.

It is a pass-through, unlike the embed's four-entry allowlist. The embed needs one because
it is the single NOA origin LibreChat may frame; this app answers `frame-ancestors 'none'`, and
the surface it needs is the admin API's contract in full — an allowlist would have to be edited by every later
admin task and a stale entry there fails as a 404 the panel cannot explain.

`/login` is the LDAP sign-in form. Its primary action is a `type="button"` click handler, not
a form submit, and that is load-bearing rather than stylistic: `NOA_SIGN_IN_URL` points here, one way
an operator arrives is a click on the embed's 401 card link-out, and a measured run showed the tab such a
click opens inherits the frame's sandbox — where `allow-forms` is absent, so a submit-driven
login would be refused with nothing the operator can see, which is why an escape hatch ships the
plain address and never a link alone. The `<form>` stays and routes to the same handler for
whatever still dispatches a submit, such as a password manager calling `requestSubmit()`.

That rule covers the whole app rather than the login page alone. The sandbox survives same-origin
navigation, so every page reached from that tab inherits it and a submit-driven Save in any dialog
is refused the same way — which is how it reached the WHM "add server" dialog. Every primary action
is a `type="button"` with an `onClick` that calls the same handler the `<form onSubmit>` calls, and
`src/primary-action-not-submit.test.ts` scans the source for the two attributes that would bring a
submit control back.

Enter is put back by hand, in JS, by `src/lib/forms/submit-on-enter.ts`. It has to be: a form with
no submit button associated with it gets nothing from the browser's implicit submission unless it
carries exactly one field that blocks it, and these forms carry more. This page claimed for a while
that Enter worked in a freshly opened tab; measured in Chromium 151 against the rendered login
page, it did not, in any tab — Enter in the email field and Enter in the password field each
produced zero submits and zero requests, against one request from the button. The helper fires the
same handler on Enter in a text field, leaving textareas and every Radix trigger alone. A hidden
submit button would not have done: measured inside a sandbox without `allow-forms`, Enter on that
shape produces one "Blocked form submission" log and nothing else, while the keydown handler runs
normally and logs nothing.

## Checks

```bash
pnpm lint
pnpm typecheck
pnpm test
pnpm test:server   # boots `next dev` on a free port; minutes, not milliseconds
pnpm build
```

`tests/` holds the package-level guards rather than feature tests: `pins.test.ts` (exact
versions, no caret ranges), `npmrc.test.ts` (the `@gio` scope resolves from the internal registry),
`hygiene.test.ts` (file-size caps — `.ts` 300 lines, `.tsx` 450) and `import-firewall.test.ts`.

`tests/**/*.server.test.ts` is the one lane that runs a real server, so it has its own config
(`vitest.server.config.ts`) and is excluded from `pnpm test`.

## Framing

Every response carries `Content-Security-Policy: frame-ancestors 'none'`, one entry on every
response: this app is
never framed, by anyone. The rule is `config/framing.ts` and `next.config.ts` returns it from
`headers()` as one entry on `/(.*)`, so the pages, `/login`, `/healthz`, the 404 and the `/api/*`
proxy are covered without each new route remembering a guard for itself.

Nothing about it is configurable. `apps/web-embed` has one legitimate parent and reads its origin
from `NOA_LIBRECHAT_ORIGIN`; this app has none, so `buildFramingHeaders()` takes no argument
and no variable in the shared repo-root `.env` can widen it — `tests/next-config-headers.test.ts`
asserts that the embed's variable means nothing here.

No `X-Frame-Options`: `frame-ancestors` supersedes it wherever both are read, and one added later
would be honoured *instead* by a client that reads XFO first. Its absence is asserted, not assumed.
`tests/framing-live.server.test.ts` proves both against a running server rather than against the
config object — a `headers()` entry Next never applies looks the same from inside the process.

## Status

Scaffold, ported admin pages, framing header, the `/api/*` proxy, the `noa_session` plumbing and
the login route: all built.

Still open: the admin verticals' own endpoints (pages ported, not all API routes built), and a readiness probe. `/healthz` here is liveness only — it touches no
dependency, so a backend outage is never reported as this app's. A `/readyz` that reads `NOA_API_URL`
would have to define readiness across two deployables, which is a deployment decision and belongs
with the images and compose setup, not with the session plumbing.
