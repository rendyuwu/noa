import { defineConfig, devices } from '@playwright/test'

// Same-origin browser checks against the embed's own dev server. The embed runs
// on 3001 because that is the origin the API builds approval URLs from
// (`NOA_EMBED_BASE_URL` in the repo-root `.env.example`), and the cookie the card
// depends on is scoped `Domain=.noa.internal` (V40) — so the port here is part of
// the contract, not a local preference.
//
// `webServer` boots both processes itself: a config that assumes a server someone
// else started passes by accident on a developer's machine and fails in CI.
const BASE_URL = process.env.EMBED_BASE_URL ?? 'http://localhost:3001'

// The proxy's upstream for e2e (§T.44). A stub, not the real API: what these specs
// ask about is the hop, and a real API would make Postgres or LDAP being down read
// as a broken proxy.
const UPSTREAM_PORT = 8099

// The stand-in for LibreChat: a page that frames the embed (§T.45). Reached under two hostnames
// that both resolve here, so the parent origin is the only variable between the allowed case and
// the refused one.
const PARENT_PORT = 8110

// The origin the framing specs treat as LibreChat's (§T.45, V41). `http`, not the deployed
// `https://chat.noa.internal`: this harness serves plain HTTP, and an https parent framing an http
// child is blocked as mixed content before CSP is ever consulted — the refusal spec would then
// pass for a reason that has nothing to do with the header. The shipped default value is pinned
// unit-side in `config/framing.test.ts`; what belongs here is that a browser enforces whatever
// origin the app was configured with.
export const CHAT_ORIGIN = `http://chat.noa.internal:${PARENT_PORT}`
export const OTHER_ORIGIN = `http://not-chat.noa.internal:${PARENT_PORT}`

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.ts',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: 'list',
  use: {
    ...devices['Desktop Chrome'],
    baseURL: BASE_URL,
  },
  webServer: [
    {
      command: 'node e2e/support/upstream-stub.mjs',
      // The open socket again, one layer below the thing under test (V90).
      port: UPSTREAM_PORT,
      reuseExistingServer: false,
      timeout: 30_000,
      env: { UPSTREAM_STUB_PORT: String(UPSTREAM_PORT) },
    },
    {
      command: 'node e2e/support/framing-parent.mjs',
      port: PARENT_PORT,
      reuseExistingServer: false,
      timeout: 30_000,
      env: { FRAMING_PARENT_PORT: String(PARENT_PORT) },
    },
    {
      command: 'pnpm dev',
      // Readiness is the open socket, deliberately not `/healthz`. Waiting on the
      // route under test turns a broken `/healthz` into a two-minute startup
      // timeout instead of the assertion failure that names what broke (V90).
      port: 3001,
      // Not reused, deliberately. The proxy specs depend on `NOA_API_URL` pointing
      // at the stub above, and a server someone else started was given a different
      // one — reusing it would make these specs pass or fail for reasons that have
      // nothing to do with the proxy. Same family as V90: a setup step that quietly
      // decides the outcome.
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        NOA_API_URL: `http://127.0.0.1:${UPSTREAM_PORT}`,
        NOA_LIBRECHAT_ORIGIN: CHAT_ORIGIN,
      },
    },
  ],
})
