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

/** The stub upstream, for the specs that read its hit counter. */
export const UPSTREAM_ORIGIN = `http://127.0.0.1:${UPSTREAM_PORT}`

/**
 * The sandbox LibreChat was measured applying to this frame at pin `45cc53c4` (R13, R29).
 *
 * `allow-forms` is **absent**, and that absence is the premise V80 rests on: a native form submit
 * dies silently in here, so the card's buttons are `fetch` handlers. Pinned as a constant because
 * a spec that quietly widened it would be testing a frame LibreChat does not serve.
 */
export const MEASURED_SANDBOX = 'allow-scripts allow-same-origin'

/**
 * The other sandbox R13/R29 recorded, at LibreChat's second render site (`MCPUIResource`).
 *
 * Same string plus `allow-popups`, and that difference is the whole of §T.43's problem: the 401
 * state's "Sign in to NOA" link opens a top-level tab under this one and **nothing at all** under
 * `MEASURED_SANDBOX`, silently. Pinned here beside its sibling so the pair is one edit to widen.
 */
export const POPUP_SANDBOX = 'allow-popups allow-scripts allow-same-origin'

/**
 * Where the 401 card's link-out points during a browser run (§T.43, `NOA_SIGN_IN_URL`).
 *
 * Deliberately a document on the stub upstream rather than a dead address: it is the *opened tab*
 * that the popup specs then measure — a tab opened from a sandboxed frame inherits the opener's
 * sandbox flags unless `allow-popups-to-escape-sandbox` is granted, and this document reports what
 * it is still allowed to do.
 */
export const SIGN_IN_URL = `${UPSTREAM_ORIGIN}/__popup-control`

/**
 * One card id per outcome the approval page renders (§T.41), shared with the stub that serves them
 * (`env` below) so a spec cannot ask about a state the stub does not have (V66). Valid UUIDs: the
 * real route's path parameter is UUID-typed, and an id shaped unlike a real one would exercise a
 * 422 the specs are not about.
 */
export const APPROVAL_IDS = {
  pending: '9f1c2b7e-0000-4000-8000-000000000001',
  decided: '9f1c2b7e-0000-4000-8000-000000000002',
  unauthorized: '9f1c2b7e-0000-4000-8000-000000000401',
  notFound: '9f1c2b7e-0000-4000-8000-000000000404',
  /** The one card that moves between reads: an approved change whose run finishes (§T.42, V29). */
  polling: '9f1c2b7e-0000-4000-8000-000000000003',
  /**
   * The card whose session comes back (§T.43): 401 on the first read, a PENDING card afterwards.
   *
   * Its own id because `unauthorized` above answers 401 forever, which is what the V38 state needs
   * and what a retry can never escape. This one is the operator who went and signed in.
   */
  recovers: '9f1c2b7e-0000-4000-8000-000000000402',
} as const

/** The token the stub puts on a PENDING card. The real one is HMAC-signed (V39, T37). */
export const STUB_CSRF = 'v1.1786000000.stub-signature'

/** What the polling card's run reports once it finishes. Asserted, so it lives in one place. */
export const STUB_RUN_RESULT = 'Account acmeco suspended on alpha.'

/**
 * The after-state on the receipt that lands with the finished run (§T.42(b), V46).
 *
 * Its own value, sharing nothing with the before-state the stub's cards carry (`suspended: false`,
 * `domain: acme.example`): the browser assertion is that *both* halves render, and a value present
 * in both could not tell that from one of them rendered twice (V87).
 */
export const STUB_RECEIPT_AFTER = '2026-08-08T09:30:12+00:00'

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
      env: {
        UPSTREAM_STUB_PORT: String(UPSTREAM_PORT),
        STUB_PENDING_ID: APPROVAL_IDS.pending,
        STUB_DECIDED_ID: APPROVAL_IDS.decided,
        STUB_UNAUTHORIZED_ID: APPROVAL_IDS.unauthorized,
        STUB_NOT_FOUND_ID: APPROVAL_IDS.notFound,
        STUB_POLLING_ID: APPROVAL_IDS.polling,
        STUB_RECOVERS_ID: APPROVAL_IDS.recovers,
        STUB_RUN_RESULT,
        STUB_RECEIPT_AFTER,
        STUB_CSRF,
      },
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
        // §T.43's link-out target. Handed in for the same reason as the upstream above: a value from
        // a developer's `.env` would make the popup specs measure whatever they had configured.
        NOA_SIGN_IN_URL: SIGN_IN_URL,
      },
    },
  ],
})
