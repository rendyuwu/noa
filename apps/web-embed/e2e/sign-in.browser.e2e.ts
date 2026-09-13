import { expect, test } from '@playwright/test'

import { APPROVAL_IDS, MEASURED_SANDBOX, POPUP_SANDBOX, SIGN_IN_URL } from '../playwright.config'
import {
  EMBED_ORIGIN,
  HOST_RESOLVER_ARGS,
  cardBody,
  frameCard,
  framedDocument,
  hits,
  signIn,
} from './support/frame'

/**
 * The way out of a 401, in the frame that has to offer it.
 *
 * **This lane exists because the failure it guards against is silent.** LibreChat frames the card at
 * two render sites and only one grants `allow-popups` (measured live: `ToolCallInfo` =
 * `allow-scripts allow-same-origin`, `MCPUIResource` = that plus `allow-popups`). Where it is absent
 * a `target="_blank"` click is refused with nothing an operator can see — the in-frame `fetch` rule's
 * failure shape, one
 * mechanism over — so the card prints the address as well as linking it, and the specs below measure
 * both strings rather than trusting either.
 *
 * **The allow-popups case is the negative control.** Without it, "no tab opened" would pass just as
 * well against a link that is simply broken.
 *
 * **And the retry is asserted as a non-navigation.** The 401 state forbids an LDAP redirect inside the
 * iframe, which is an absence: the assertion is that the framed document is still on the card's URL
 * after the state has changed under it.
 *
 * The frame, the cookie and the hit counter come from `support/frame.ts` — the same ones
 * `approvals.browser.e2e.ts` uses, because a 401 rendered in a different frame is not this claim.
 */

test.use({ launchOptions: { args: HOST_RESOLVER_ARGS } })

test.beforeEach(async ({ context }) => {
  await signIn(context)
})

test('a 401 renders “cannot authenticate here” and no decision', async ({ page }) => {
  // Never a blank card and never a live Approve: one an operator cannot use reads as an action that
  // was refused rather than one that was never available. What the state *does* carry is the 401 state's way
  // out — a link-out and a retry, neither of which is a decision.
  const card = await frameCard(page, APPROVAL_IDS.unauthorized)

  await expect(card.locator('h1')).toContainText('Cannot authenticate here')
  await expect(cardBody(card).getByRole('button', { name: /approve|deny/i })).toHaveCount(0)
  await expect(card.getByLabel(/why is this change/i)).toHaveCount(0)

  // No login page and no credential handling — asserted as the absence it is, inside the frame
  // LibreChat actually serves.
  await expect(cardBody(card).locator('form')).toHaveCount(0)
  await expect(cardBody(card).locator('input')).toHaveCount(0)

  const link = cardBody(card).getByRole('link', { name: 'Sign in to NOA' })
  await expect(link).toHaveAttribute('href', SIGN_IN_URL)
  await expect(link).toHaveAttribute('target', '_blank')
  await expect(link).toHaveAttribute('rel', /noopener/)
  await expect(cardBody(card).getByRole('button', { name: 'Try again' })).toBeVisible()
})

test('the sign-in link opens nothing where the sandbox omits allow-popups', async ({
  page,
  context,
}) => {
  // The measurement the 401 state's second door exists for. `ToolCallInfo` frames this card under
  // `allow-scripts allow-same-origin`, and a `target="_blank"` click in there is refused with
  // nothing the operator can see — the in-frame `fetch` rule's failure shape, one mechanism over. So the address is on the
  // card as text as well, and *that* is what makes the state answerable at this render site.
  const card = await frameCard(page, APPROVAL_IDS.unauthorized)
  expect(MEASURED_SANDBOX).not.toContain('allow-popups')

  const before = context.pages().length
  await cardBody(card).getByRole('link', { name: 'Sign in to NOA' }).click()

  // Asserted on the page count after a settle, not on an exception: the refusal is silent, which is
  // the whole reason the printed address ships beside the link.
  await page.waitForTimeout(500)
  expect(context.pages()).toHaveLength(before)

  // And the door that does not depend on a popup: the address, on the card, as text.
  await expect(cardBody(card)).toContainText('copy this address')
  await expect(cardBody(card)).toContainText(SIGN_IN_URL)
})

test('the same click does open a top-level tab where allow-popups is granted', async ({
  page,
  context,
}) => {
  // The negative control for the spec above, and the reason it means anything: without it, "no tab
  // opened" passes just as well against a link that is simply broken. `MCPUIResource` — LibreChat's
  // other render site — frames the card under this string.
  const card = await frameCard(page, APPROVAL_IDS.unauthorized, POPUP_SANDBOX)

  const opened = context.waitForEvent('page')
  await cardBody(card).getByRole('link', { name: 'Sign in to NOA' }).click()
  const tab = await opened

  await tab.waitForLoadState('domcontentloaded')
  expect(tab.url()).toBe(SIGN_IN_URL)

  // What that tab can still do, measured rather than reasoned about: a popup inherits its opener's
  // sandbox flags unless `allow-popups-to-escape-sandbox` is granted, and that is measured as absent
  // — so a login form in there may be as inert as the one the card itself forbids. The result is
  // recorded on the stub's own counters; either way the printed address is the door that does not
  // depend on this.
  const probe = await tab.evaluate(async () => {
    const run = (window as unknown as { probe: () => Promise<{ fetched: string }> }).probe
    return run()
  })

  // The fetch first, and polled: it proves the counter for this document is live, so a missing form
  // hit below is an absence rather than a read that was too early.
  expect(probe.fetched).toBe('ok')
  await expect.poll(async () => (await hits(page))['POST /__probe/popup-fetch']).toBeGreaterThan(0)
  await page.waitForTimeout(500)

  const seen = await hits(page)
  const submitted = seen['POST /__probe/popup-form'] !== undefined

  // Two independent signals for the same fact: a submit that went through would also have navigated
  // this tab off the control document.
  expect(tab.url() !== SIGN_IN_URL).toBe(submitted)

  // Logged rather than asserted either way: this is a fact about LibreChat's sandbox, and pinning it
  // here would make a future bump fail in the spec that is about NOA's link instead of in the pin
  // script that watches LibreChat (`spikes/librechat-embed-render-gate/`, which already fails if the
  // shipped bytes ever mention `allow-forms`).
  test.info().annotations.push({
    type: 'measured',
    description: `opened tab: form submit reached the stub = ${submitted}, url = ${tab.url()}`,
  })

  // The in-frame `fetch` control's counters are untouched by all of this — separate paths on purpose.
  expect(seen['POST /__probe/form']).toBeUndefined()
})

test('Try again picks the session up without leaving the card URL', async ({
  page,
}) => {
  // The 401 state's forbidden clause, asserted: no LDAP redirect inside the iframe. The operator signs in somewhere
  // else, comes back, clicks once — and the frame is still the document it was, on the URL that owns
  // this request's lifecycle.
  const id = APPROVAL_IDS.recovers
  const card = await frameCard(page, id)
  const cardUrl = `${EMBED_ORIGIN}/approvals/${id}`

  await expect(card.locator('h1')).toContainText('Cannot authenticate here')
  expect((await framedDocument(page)).url()).toBe(cardUrl)

  await cardBody(card).getByRole('button', { name: 'Try again' }).click()

  // The card the second read answers, rendered in place. Both halves of the heading, for the
  // reason the provenance spec states: the label is what an operator reads and the raw tool name
  // is what they quote to an administrator, so swapping the assertion to the label alone would
  // stop watching the string that has to survive.
  await expect(card.locator('h1')).toHaveText('Suspend an account — acmeco')
  await expect(card.locator('h1')).toHaveAttribute('title', 'whm_suspend_account')
  await expect(card.getByRole('button', { name: 'Approve' })).toBeVisible()

  // Same document, same URL: nothing navigated, and no login form ever entered the frame.
  expect((await framedDocument(page)).url()).toBe(cardUrl)
  await expect(cardBody(card).locator('form')).toHaveCount(0)
  expect((await hits(page))[`GET /action-requests/${id}`]).toBe(2)
})
