import { expect, test } from '@playwright/test'

import { CHAT_ORIGIN, OTHER_ORIGIN } from '../playwright.config'

/**
 * Who may frame this app, decided by a real browser.
 *
 * The unit specs prove what header the app emits. Whether a browser then refuses the frame is a
 * different question, and it is the one the control exists to answer — a CSP that is present but
 * malformed emits the same kind of string and stops nobody.
 *
 * Both parent documents come from one server (`e2e/support/framing-parent.mjs`) reached under two
 * hostnames, so the parent *origin* is the only variable between the allowed case and the refused
 * one. The dev server under test was started with `NOA_LIBRECHAT_ORIGIN` set to the first of them.
 */

// DNS only: both names point at the loopback address the parent server listens on. Chromium's
// Local Network Access checks block a request to loopback from an origin it cannot place in an
// address space, so an intercepted parent page made *both* frames fail before any response — the
// refusal spec would then have passed with no CSP header in existence — the readiness gate
// sits one layer below the subject, never pointed at it.
test.use({
  launchOptions: {
    args: [
      '--host-resolver-rules=MAP chat.noa.internal 127.0.0.1, MAP not-chat.noa.internal 127.0.0.1',
    ],
  },
})

const FRAMED_PATH = '/healthz'

test('every response carries the framing header, whatever serves it', async ({ page }) => {
  // One `headers()` entry on `/(.*)`, so the pages, the `/api/*` proxy and a path that
  // routes to nothing are all covered. A page-shaped pattern would leave each new route to
  // remember the guard for itself.
  for (const path of [FRAMED_PATH, '/api/auth/me', '/this-route-does-not-exist']) {
    const headers = (await page.request.get(path)).headers()

    expect(headers['content-security-policy'], `no CSP on ${path}`).toBe(
      `frame-ancestors ${CHAT_ORIGIN}`,
    )
    // None is needed, and one added later would break framing in any client that honours
    // it over CSP.
    expect(headers['x-frame-options'], `X-Frame-Options on ${path}`).toBeUndefined()
  }
})

test('a document on the LibreChat origin may frame the embed', async ({ page }) => {
  // The allowed case, guarded. The render path measured live has LibreChat put the frame `src`
  // on this
  // app's origin; a framing header is exactly the kind of change that can kill that silently, so
  // the allowed case is asserted, not assumed.
  await page.goto(`${CHAT_ORIGIN}/parent`)

  await expect(page.frameLocator('#card').locator('body')).toContainText('"status":"ok"')
})

test('a document on any other origin is refused by the browser', async ({ page }) => {
  // The separating case. Without it the spec above passes just as well with no header at all
  // — and the frame that does *not* load is the whole point of the header.
  const refusals: string[] = []
  page.on('console', (message) => {
    if (message.text().includes('frame-ancestors')) refusals.push(message.text())
  })

  await page.goto(`${OTHER_ORIGIN}/parent`)

  await expect
    .poll(() => refusals.length, { message: 'browser reported no frame-ancestors refusal' })
    .toBeGreaterThan(0)

  // Named, so a refusal for some other reason (a broken parent, a blocked request) cannot pass as
  // this one.
  expect(refusals[0]).toContain(`frame-ancestors ${CHAT_ORIGIN}`)

  // And the embed's document never committed inside the framing page.
  const framed = page.frames().find((frame) => frame.url().includes(FRAMED_PATH))
  expect(framed, 'the blocked frame loaded the embed document anyway').toBeUndefined()
})
