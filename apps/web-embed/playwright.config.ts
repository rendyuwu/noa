import { defineConfig, devices } from '@playwright/test'

// Same-origin browser checks against the embed's own dev server. The embed runs
// on 3001 because that is the origin the API builds approval URLs from
// (`NOA_EMBED_BASE_URL` in the repo-root `.env.example`), and the cookie the card
// depends on is scoped `Domain=.noa.internal` (V40) — so the port here is part of
// the contract, not a local preference.
//
// `webServer` boots the app itself: a config that assumes a server someone else
// started passes by accident on a developer's machine and fails in CI.
const BASE_URL = process.env.EMBED_BASE_URL ?? 'http://localhost:3001'

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
  webServer: {
    command: 'pnpm dev',
    // Readiness is the open socket, deliberately not `/healthz`. Waiting on the
    // route under test turns a broken `/healthz` into a two-minute startup
    // timeout instead of the assertion failure that names what broke.
    port: 3001,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
