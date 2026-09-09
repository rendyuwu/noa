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

/**
 * A port for one of the two servers this harness stands up itself.
 *
 * Overridable, unlike 3001 above. That one is the contract — the API builds approval URLs from it
 * and the cookie is domain-scoped (V40) — while these two are private to this file: the stub
 * upstream and the stand-in parent are addressed only through the constants exported below, so a
 * different number changes nothing any spec asserts. They are overridable because `webServer`
 * refuses to start on a port something else holds, and a machine that has something else on 8099
 * would otherwise be a machine where this whole lane is skipped rather than run — which is the
 * shape V102 refuses one layer up: a suite reporting its own absence as success. Measured: a
 * developer box here had an unrelated static server parked on 8099 for days.
 *
 * **The defaults are the run that matters, and nothing in this repository sets either variable.**
 * So CI is a default-port run by construction, and this is an escape hatch for one developer box
 * rather than a configuration surface: a green run with an override set is evidence about the
 * specs, and no evidence at all that the two default numbers are free on the machine that runs
 * them next.
 * Anyone who needed the override on their own box should say so, because the collision is a fact
 * about that box and not about this file.
 */
function harnessPort(variable: string, fallback: number): number {
  const value = Number(process.env[variable])
  return Number.isInteger(value) && value > 0 && value < 65_536 ? value : fallback
}

// The proxy's upstream for e2e (§T.44). A stub, not the real API: what these specs
// ask about is the hop, and a real API would make Postgres or LDAP being down read
// as a broken proxy.
const UPSTREAM_PORT = harnessPort('NOA_E2E_UPSTREAM_PORT', 8099)

// The stand-in for LibreChat: a page that frames the embed (§T.45). Reached under two hostnames
// that both resolve here, so the parent origin is the only variable between the allowed case and
// the refused one.
const PARENT_PORT = harnessPort('NOA_E2E_PARENT_PORT', 8110)

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
  /**
   * The card whose long monospace values re-wrap when the frame's width changes.
   *
   * The fixture for the oscillation self-sizing could have shipped: a scrollbar appearing and
   * disappearing moves the content box by its own width, and `.factValue` gains or loses a whole
   * line on that. Its own id because every other card here is short enough that the loop never
   * starts, which would make a convergence spec pass without measuring anything.
   */
  reflow: '9f1c2b7e-0000-4000-8000-000000000005',
} as const

/**
 * One token per outcome the table surface renders (§T.56), shared with the stub that serves them
 * for `APPROVAL_IDS`' reason (V66).
 *
 * Not UUID-shaped, deliberately: the real token is `secrets.token_urlsafe(32)` and the route takes
 * a plain string, so a UUID here would exercise a shape the surface never sees.
 */
export const TABLE_TOKENS = {
  /** A whole listing: every matched row is on the page. */
  whole: 'e2e-table-whole-000000000000000000000001',
  /** A capped one — the state V85 exists for, where the counts must not agree. */
  truncated: 'e2e-table-capped-000000000000000000000002',
  /** 401 forever: the state V38 renders and a retry cannot escape. */
  unauthorized: 'e2e-table-unauthorized-00000000000000401',
  /** The one answer for unknown, foreign, orphaned and expired alike (V27). */
  notFound: 'e2e-table-missing-00000000000000000000404',
  /**
   * A table the stub serves **only** to a request carrying a cookie, and 401s otherwise.
   *
   * The separator for "the operator's session reached the API through the page's own server-side
   * read" (V22, V40): against a stub that answered 200 regardless, that spec would pass with the
   * cookie dropped, which is a test that cannot fail for the reason it was written (V87).
   */
  needsCookie: 'e2e-table-cookie-000000000000000000000003',
  /**
   * The whole listing whose size started the self-sizing work: 438 rows, none dropped.
   *
   * Not the capped token above, deliberately — a capped page holds two rows and would ask for a
   * short frame, so it cannot say anything about the bound on a long one.
   */
  long: 'e2e-table-long-00000000000000000000000438',
} as const

/** What a capped table reports (§T.56, V85). Asserted, so the numbers live in one place. */
export const STUB_TABLE_TOTAL_ROWS = 1240
export const STUB_TABLE_STORED_ROWS = 2

/**
 * How many rows the long listing carries (§T.56).
 *
 * The count from the complaint the frame-sizing work answers, not a round number: at the ~36px row
 * height `table.module.css` produces this is on the order of 15,000px of natural page height, and
 * the host applies whatever height it is sent verbatim.
 */
export const STUB_TABLE_LONG_ROWS = 438

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
        STUB_REFLOW_ID: APPROVAL_IDS.reflow,
        STUB_RUN_RESULT,
        STUB_RECEIPT_AFTER,
        STUB_CSRF,
        STUB_TABLE_TRUNCATED_TOKEN: TABLE_TOKENS.truncated,
        STUB_TABLE_UNAUTHORIZED_TOKEN: TABLE_TOKENS.unauthorized,
        STUB_TABLE_NOT_FOUND_TOKEN: TABLE_TOKENS.notFound,
        STUB_TABLE_NEEDS_COOKIE_TOKEN: TABLE_TOKENS.needsCookie,
        STUB_TABLE_LONG_TOKEN: TABLE_TOKENS.long,
        STUB_TABLE_LONG_ROWS: String(STUB_TABLE_LONG_ROWS),
        STUB_TABLE_TOTAL_ROWS: String(STUB_TABLE_TOTAL_ROWS),
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
