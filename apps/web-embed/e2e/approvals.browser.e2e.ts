import { expect, test } from '@playwright/test'
import type { Frame, FrameLocator, Page } from '@playwright/test'

import {
  APPROVAL_IDS,
  CHAT_ORIGIN,
  MEASURED_SANDBOX,
  STUB_CSRF,
  UPSTREAM_ORIGIN,
} from '../playwright.config'

/**
 * The approval card in a real browser, inside the frame LibreChat actually gives it (§T.41 — V22,
 * V35, V38, V39, V80).
 *
 * **This is the lane jsdom cannot stand in for.** The unit specs prove the card has no `<form>` and
 * that `submitDecision` builds the right request. What they cannot prove is the thing V80 exists
 * for: that a decision leaves a frame whose sandbox omits `allow-forms`. So the parent here frames
 * the card under `MEASURED_SANDBOX` — the exact string R29 recorded at pin `45cc53c4` — and the
 * stub upstream's hit counter says whether the POST arrived.
 *
 * **The negative control is a separate document in the same sandbox** (`/__sandbox-control` on the
 * stub), which fires both a `fetch` and a native form submit at two paths. If the browser performed
 * both, the sandbox is not withholding forms and V80's premise has changed — which would make the
 * positive assertion above true for a reason that has nothing to do with the card (V87).
 *
 * **Every test that counts hits uses an id of its own.** The stub is one process shared by every
 * spec file and Playwright runs files in parallel, so its counter is cumulative and cannot be reset
 * without racing another file. A per-test id makes each count exact instead of "at least".
 *
 * **Reads happen inside the frame, not through it.** `allow-same-origin` keeps the frame same-origin
 * with *its own* origin, which is not the parent's — so `contentWindow` from the parent is a
 * `SecurityError`, and anything this file needs from in there is evaluated in the frame itself.
 */

// DNS only: `chat.noa.internal` is the origin the dev server was told to allow framing from
// (§T.45), and Chromium's Local Network Access checks are why the parent has to be a resolvable
// real server rather than an intercepted response (V90's family).
test.use({
  launchOptions: {
    args: [
      '--host-resolver-rules=MAP chat.noa.internal 127.0.0.1, MAP not-chat.noa.internal 127.0.0.1',
    ],
  },
})

const EMBED_ORIGIN = 'http://localhost:3001'

/**
 * A PENDING card id unique to one test.
 *
 * The stub serves any id it does not recognise as PENDING, so a suffix is all this needs — and the
 * hit counter then belongs to one test rather than to whichever ran first.
 */
function pendingId(suffix: string): string {
  return `9f1c2b7e-0000-4000-8000-0000000${suffix.padStart(5, '0')}`
}

/** The parent page LibreChat stands in for, framing `src` under the measured sandbox. */
function parentUrl(src: string): string {
  const parent = new URL(CHAT_ORIGIN)
  parent.searchParams.set('src', src)
  parent.searchParams.set('sandbox', MEASURED_SANDBOX)
  return parent.toString()
}

async function frameCard(page: Page, id: string): Promise<FrameLocator> {
  await page.goto(parentUrl(`${EMBED_ORIGIN}/approvals/${id}`))
  return page.frameLocator('#card')
}

/** The framed document itself, for the two reads that have to run inside it. */
async function framedDocument(page: Page): Promise<Frame> {
  const element = await page.waitForSelector('#card')
  const frame = await element.contentFrame()
  if (frame === null) throw new Error('#card has no content frame')
  return frame
}

async function hits(page: Page): Promise<Record<string, number>> {
  const response = await page.request.get(`${UPSTREAM_ORIGIN}/__hits`)
  return (await response.json()) as Record<string, number>
}

/** The card's own DOM. Scoped so Next's dev-mode overlay is not counted as part of the card. */
function cardBody(card: FrameLocator) {
  return card.locator('main')
}

test.beforeEach(async ({ context }) => {
  // The session the card is read with. Scoped to `localhost`, not `.noa.internal`: a browser will
  // not accept the real domain for a localhost document, and asserting it anyway would be a check
  // that cannot fail. The registrable-domain scoping is the API's setting (V40) and is asserted
  // API-side; what belongs here is that the browser's cookie for this origin reaches NOA.
  await context.addCookies([
    { name: 'noa_session', value: 'e2e.session.value', domain: 'localhost', path: '/' },
  ])
})

test('the card renders its provenance and before-state inside the frame', async ({ page }) => {
  // V35 and V17: what an operator is asked to recognise, and the preflight the model never sees.
  const card = await frameCard(page, APPROVAL_IDS.pending)

  await expect(card.locator('h1')).toHaveText('whm_suspend_account')
  await expect(cardBody(card)).toContainText('operator@noa.internal')
  await expect(cardBody(card)).toContainText('librechat-user-1')
  await expect(cardBody(card)).toContainText('acme.example')
  await expect(card.getByRole('button', { name: 'Approve' })).toBeVisible()
})

test('the frame is on NOA’s own origin, and the operator’s cookie reached the API', async ({
  page,
}) => {
  // V37/C17 from inside: `allow-same-origin` plus a `src` on this origin is what lets the session
  // cookie ride, and the render is server-side, so the cookie has to reach the API through the
  // page's own read — not only through the browser-facing proxy (§T.44).
  const id = pendingId('a1')
  const card = await frameCard(page, id)
  await expect(card.locator('h1')).toBeVisible()

  const frame = await framedDocument(page)
  expect(await frame.evaluate(() => window.location.origin)).toBe(EMBED_ORIGIN)
  // `document.cookie` is empty because the session cookie is httpOnly (V6) — the same reading R29
  // recorded. The cookie's arrival is asserted on the API's side instead.
  expect(await frame.evaluate(() => document.cookie)).toBe('')

  expect((await hits(page))[`GET /action-requests/${id}`]).toBe(1)
  await expect(cardBody(card)).toContainText('operator@noa.internal')
})

test('Approve posts from inside a frame whose sandbox omits allow-forms (V80)', async ({
  page,
}) => {
  const id = pendingId('a2')
  const card = await frameCard(page, id)

  // The sandbox is the one under discussion, read off the element rather than assumed.
  await expect(page.locator('#card')).toHaveAttribute('sandbox', MEASURED_SANDBOX)
  expect(MEASURED_SANDBOX).not.toContain('allow-forms')

  // No form in the tree either: `type="button"` and no `<form>` is the same rule stated twice.
  await expect(card.locator('form')).toHaveCount(0)

  await card.getByLabel(/why is this change/i).fill('Customer confirmed; ticket NOC-4471.')
  await card.getByRole('button', { name: 'Approve' }).click()

  await expect(card.getByRole('status')).toContainText('Approved')
  expect((await hits(page))[`POST /action-requests/${id}/approve`]).toBe(1)
})

test('Deny posts to the other door', async ({ page }) => {
  // The separating case: without it, the spec above passes just as well against a card whose two
  // buttons do the same thing (V87).
  const id = pendingId('a3')
  const card = await frameCard(page, id)

  await card.getByLabel(/why is this change/i).fill('Not a legitimate request.')
  await card.getByRole('button', { name: 'Deny' }).click()

  await expect(card.getByRole('status')).toContainText('Nothing was changed')

  const seen = await hits(page)
  expect(seen[`POST /action-requests/${id}/deny`]).toBe(1)
  expect(seen[`POST /action-requests/${id}/approve`]).toBeUndefined()
})

test('in that same sandbox a fetch reaches NOA and a form submit does not', async ({ page }) => {
  // The negative control for V80, and the reason the assertions above mean anything: if a form
  // submit worked in here, "the fetch worked" would be a claim about nothing. Both probes are
  // same-origin with the document firing them, so neither is decided by CORS.
  await page.goto(parentUrl(`${UPSTREAM_ORIGIN}/__sandbox-control`))
  await expect(page.frameLocator('#card').locator('#viaForm')).toBeAttached()

  const frame = await framedDocument(page)
  const result = await frame.evaluate(async () => {
    const probe = (window as unknown as { probe: () => Promise<{ fetched: string }> }).probe
    return probe()
  })

  expect(result.fetched).toBe('ok')
  await expect.poll(async () => (await hits(page))['POST /__probe/fetch']).toBeGreaterThan(0)

  // The form never left the frame. Asserted on the counter, not on an exception: the sandbox
  // blocks the submission silently, which is exactly why V80 forbids relying on one.
  expect((await hits(page))['POST /__probe/form']).toBeUndefined()
})

test('a 401 renders “cannot authenticate here” and no Approve button (V38)', async ({ page }) => {
  // Never a blank card and never a live button: an Approve an operator cannot use reads as an
  // action that was refused rather than one that was never available.
  const card = await frameCard(page, APPROVAL_IDS.unauthorized)

  await expect(card.locator('h1')).toContainText('Cannot authenticate here')
  await expect(cardBody(card).getByRole('button')).toHaveCount(0)
  await expect(card.getByLabel(/why is this change/i)).toHaveCount(0)
})

test('a 404 renders one not-available state and no Approve button (V27)', async ({ page }) => {
  // Absent, another operator's, and one whose requester was deleted all answer alike — the API
  // gives one body for the three and the card gives one sentence.
  const card = await frameCard(page, APPROVAL_IDS.notFound)

  await expect(card.locator('h1')).toContainText('Request not available')
  await expect(cardBody(card).getByRole('button')).toHaveCount(0)
})

test('a decided card shows the outcome and no live buttons (V34, V39)', async ({ page }) => {
  // One URL through the whole lifecycle: the second click reads the answer. And with no token on
  // the body (the API sends `null` once nothing may be decided), no reason box and no buttons.
  const card = await frameCard(page, APPROVAL_IDS.decided)

  await expect(cardBody(card)).toContainText('Approved')
  await expect(cardBody(card)).toContainText('no longer awaiting a decision')
  await expect(cardBody(card).getByRole('button')).toHaveCount(0)
  await expect(cardBody(card)).not.toContainText(STUB_CSRF)
})

test('the card never renders the CSRF token as text (V26, V39)', async ({ page }) => {
  // It rides in the POST body, not in the document an operator (or a screenshot) can read.
  const card = await frameCard(page, pendingId('a4'))

  await expect(card.locator('h1')).toBeVisible()
  await expect(cardBody(card)).not.toContainText(STUB_CSRF)
})
