# apps/admin-web — BIGSU admin panel

Reference for the admin panel: what it is, where it came from, and what a build or deploy has to
satisfy. Design rationale lives in `DECISIONS.md`; the invariants are `SPEC.md` §V — this file
restates neither. Landed by §T.47 (scaffold) and §T.48 (port).

## What it is

Next.js 16 + BIGSU. Users, roles, tokens, WHM/Proxmox/PMG servers, audit (action requests, tool
runs, receipts). One of three deployables (C12), with its own `package.json`, lockfile, CI job and
deploy artifact. It shares no source with `apps/web-embed` — `eslint.config.mjs` holds that as a
rule and `tests/import-firewall.test.ts` exercises the rule.

Never framed: it sends `Content-Security-Policy: frame-ancestors 'none'` (V41, §T.49).

## Ported, not written

The panel came from `noa-old` branch `MCP`, `apps/web-bigsu/`, copied rather than rewritten (C13,
V69). The old app's admin verticals shipped with their own test suites, and those came across with
the code — 50 files, 371 tests.

Four deliberate deviations from the source:

1. **Chat is gone.** `lib/assistant`, `components/assistant` and the `@assistant-ui/*` dependencies
   did not come across; NOA's chat surface lives in an external LLM UI now. The nav's first entry
   went with it.
2. **The audit receipt kept its helpers.** `audit-receipt-page.tsx` was the one admin file reaching
   into the chat lib, for receipt parsing/redaction, the PNG/clipboard export and the evidence
   disclosure. Those three modules moved to `lib/admin/audit/` and `components/admin/audit/`, and
   the two evidence types they need dropped their `Assistant` prefix (`receipt-evidence.ts`). The
   two coercers they used are now in `lib/admin/shared/coerce.ts` beside the ones the server
   verticals already shared (V66).
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

CI for this repo is maintained on company GitLab (`master`/`staging`) out of band; this file states
the requirement, it does not add a workflow. Runner expectations: Node 20 LTS, pnpm via Corepack
(the `packageManager` field pins the version), an ephemeral workspace per job, no registry
credential in the pipeline, and internal-runner jobs restricted to protected branches — a runner
with routable access to the internal registry that also runs untrusted merge requests is a
poisoning target.

Credential-free metadata read, from an internal-capable environment:

```console
$ npm view @gio/bigsu-ui version --registry=https://bigsu.biznetgio.pt/registry/
1.0.3
```

## Config and health

Config comes from the repo-root `.env` via `config/root-env.ts`; there is no per-app `.env.local`,
because two env files for one deployment is two places for `NOA_API_URL` to disagree. Anything
already set in the real environment wins over the file.

- `NOA_API_URL` — the API the same-origin `/api/*` proxy forwards to (server-side only, §T.50).
- Dev port 3000. `apps/web-embed` owns 3001, and the root `.env.example` already points
  `NOA_SIGN_IN_URL` at `http://localhost:3000/login`.

`/healthz` is liveness only: it touches no dependency, so a backend outage is never reported as
this app's. Readiness (`/readyz`, which does read `NOA_API_URL`) arrives with §T.50.

## Checks

```bash
pnpm install && pnpm lint && pnpm typecheck && pnpm test && pnpm build
```

Package-level guards live in `tests/`: `pins.test.ts` (C2), `npmrc.test.ts` (registry routing),
`hygiene.test.ts` (C14/V65 file-size caps) and `import-firewall.test.ts` (C12/C13 boundaries).

## Not here yet

`frame-ancestors 'none'` (§T.49), and the auth/session plumbing: the `/api/*` proxy route, the
login page and `/readyz` (§T.50). The browser e2e specs that cover the admin verticals stayed in
the old repo until then — they need the proxy and login route to have a target.
