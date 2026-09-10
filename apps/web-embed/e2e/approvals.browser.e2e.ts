import { expect, test } from '@playwright/test'

import {
  APPROVAL_IDS,
  MEASURED_SANDBOX,
  STUB_CSRF,
  STUB_RECEIPT_AFTER,
  STUB_RUN_RESULT,
  UPSTREAM_ORIGIN,
} from '../playwright.config'
import {
  EMBED_ORIGIN,
  HOST_RESOLVER_ARGS,
  cardBody,
  frameCard,
  framedDocument,
  hits,
  parentUrl,
  pendingId,
  signIn,
} from './support/frame'

/**
 * The approval card in a real browser, inside the frame LibreChat actually gives it.
 *
 * **This is the lane jsdom cannot stand in for.** The unit specs prove the card has no `<form>` and
 * that `submitDecision` builds the right request. What they cannot prove is the thing the JS-fetch
 * rule exists for: that a decision leaves a frame whose sandbox omits `allow-forms`. So the parent
 * here frames the card under `MEASURED_SANDBOX` — the exact string measured live at pin
 * `45cc53c4` — and the stub upstream's hit counter says whether the POST arrived.
 *
 * **The negative control is a separate document in the same sandbox** (`/__sandbox-control` on the
 * stub), which fires both a `fetch` and a native form submit at two paths. If the browser performed
 * both, the sandbox is not withholding forms and the JS-fetch rule's premise has changed — which
 * would make the positive assertion above true for a reason that has nothing to do with the card.
 *
 * **Every test that counts hits uses an id of its own.** The stub is one process shared by every
 * spec file and Playwright runs files in parallel, so its counter is cumulative and cannot be reset
 * without racing another file. A per-test id makes each count exact instead of "at least".
 *
 * **Reads happen inside the frame, not through it.** `allow-same-origin` keeps the frame same-origin
 * with *its own* origin, which is not the parent's — so `contentWindow` from the parent is a
 * `SecurityError`, and anything this file needs from in there is evaluated in the frame itself.
 */

// The frame LibreChat gives this card, and the session it is read with — both from
// `support/frame.ts`, so the 401 lane (`sign-in.browser.e2e.ts`) measures the same frame this one
// does.
test.use({ launchOptions: { args: HOST_RESOLVER_ARGS } })

test.beforeEach(async ({ context }) => {
  await signIn(context)
})

test('the card renders its provenance and before-state inside the frame', async ({ page }) => {
  // What an operator is asked to recognise, and the preflight the model never sees.
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
  // The on-NOA-origin rule, from inside: `allow-same-origin` plus a `src` on this origin is what
  // lets the session cookie ride, and the render is server-side, so the cookie has to reach the
  // API through the page's own read — not only through the browser-facing proxy.
  const id = pendingId('a1')
  const card = await frameCard(page, id)
  await expect(card.locator('h1')).toBeVisible()

  const frame = await framedDocument(page)
  expect(await frame.evaluate(() => window.location.origin)).toBe(EMBED_ORIGIN)
  // `document.cookie` is empty because the session cookie is httpOnly — the same reading
  // measured live. The cookie's arrival is asserted on the API's side instead.
  expect(await frame.evaluate(() => document.cookie)).toBe('')

  expect((await hits(page))[`GET /action-requests/${id}`]).toBe(1)
  await expect(cardBody(card)).toContainText('operator@noa.internal')
})

test('Approve posts from inside a frame whose sandbox omits allow-forms', async ({
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
  // buttons do the same thing.
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
  // The negative control for the JS-fetch rule, and the reason the assertions above mean
  // anything: if a form submit worked in here, "the fetch worked" would be a claim about
  // nothing. Both probes are same-origin with the document firing them, so neither is decided
  // by CORS.
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
  // blocks the submission silently, which is exactly why the JS-fetch rule forbids relying on one.
  expect((await hits(page))['POST /__probe/form']).toBeUndefined()
})

test('a 404 renders one not-available state and no Approve button', async ({ page }) => {
  // Absent, another operator's, and one whose requester was deleted all answer alike — the API
  // gives one body for the three and the card gives one sentence.
  const card = await frameCard(page, APPROVAL_IDS.notFound)

  await expect(card.locator('h1')).toContainText('Request not available')
  await expect(cardBody(card).getByRole('button')).toHaveCount(0)
})

test('a decided card shows the outcome and no live buttons', async ({ page }) => {
  // One URL through the whole lifecycle: the second click reads the answer. And with no token on
  // the body (the API sends `null` once nothing may be decided), no reason box and no buttons.
  const card = await frameCard(page, APPROVAL_IDS.decided)

  await expect(cardBody(card)).toContainText('Approved')
  await expect(cardBody(card)).toContainText('no longer awaiting a decision')
  await expect(cardBody(card).getByRole('button')).toHaveCount(0)
  await expect(cardBody(card)).not.toContainText(STUB_CSRF)
})

test('the card follows its run to a terminal state, on the same URL', async ({
  page,
}) => {
  // The whole point of state-in-DB, in a real browser: the state lives in the database, so the
  // frame re-reads the row rather than being told the outcome by whoever started it. Nothing
  // here reloads, navigates
  // or clicks — the only thing that happens between the two assertions is time.
  const id = APPROVAL_IDS.polling
  const card = await frameCard(page, id)

  await expect(cardBody(card)).toContainText('STARTED')
  // No receipt while the run is in flight, so what appears below is something the frame fetched.
  await expect(cardBody(card)).not.toContainText('What the change did')

  // The stub answers STARTED twice and COMPLETED after that, so this is a transition the page had
  // to go and fetch — not the first answer it ever saw.
  await expect(cardBody(card)).toContainText('COMPLETED', { timeout: 20_000 })
  await expect(cardBody(card)).toContainText(STUB_RUN_RESULT)

  // The card's receipt render, run-plus-receipt, and DECISIONS section 6.5: the answer this URL
  // owns is the receipt's two halves, rendered in the frame measured live — the before-state
  // the operator authorised against, and what the
  // change did, never one word standing in for both.
  await expect(cardBody(card)).toContainText('What the change did')
  await expect(cardBody(card)).toContainText('Completed')
  await expect(cardBody(card)).toContainText(STUB_RECEIPT_AFTER)
  await expect(cardBody(card)).toContainText('Before state')
  await expect(cardBody(card)).toContainText('acme.example')

  // And the reads came from the browser through the same proxy, not only from the page's own
  // server-side render: that first read is hit 1, so anything past it is the poll.
  expect((await hits(page))[`GET /action-requests/${id}`]).toBeGreaterThan(1)
})

test('the card never renders the CSRF token as text', async ({ page }) => {
  // It rides in the POST body, not in the document an operator (or a screenshot) can read.
  const card = await frameCard(page, pendingId('a4'))

  await expect(card.locator('h1')).toBeVisible()
  await expect(cardBody(card)).not.toContainText(STUB_CSRF)
})
