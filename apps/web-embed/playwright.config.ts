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
      env: { NOA_API_URL: `http://127.0.0.1:${UPSTREAM_PORT}` },
    },
  ],
})
