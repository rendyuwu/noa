import { type Frame, type Page, expect, test } from '@playwright/test'

import { APPROVAL_IDS } from '../playwright.config'
import { CARD_FRAME_POLICY } from '../src/lib/embed/frame-size'
import {
  HOST_OPENING_HEIGHT,
  HOST_RESOLVER_ARGS,
  frameBox,
  frameCard,
  framedDocument,
  signIn,
  sizePosts,
} from './support/frame'

/**
 * The receipt's delta section, measured in the frame LibreChat serves.
 *
 * **Both claims here are layout claims, which is why neither is in the component lane.** jsdom
 * computes no layout at all — no widths, no wrapping, no `max-height`, and
 * `getBoundingClientRect()` returns zeros — so "the card fits" and "the lines do not break" are
 * assertions it cannot make and would silently pass. The component lane holds what the renderer
 * says (`src/app/approvals/[id]/outcome-view.test.tsx`); this file holds what it looks like.
 *
 * Same harness as the other card lanes: the parent origin the app allows framing from, the sandbox
 * string the host was measured applying, and the session cookie. Everything the header of
 * `frame-size-card.browser.e2e.ts` states about the lane applies here, including that there is no
 * readiness wait of this file's own.
 */

test.use({ launchOptions: { args: HOST_RESOLVER_ARGS } })

test.beforeEach(async ({ context }) => {
  await signIn(context)
})

/**
 * A chat column narrower than anything this work was measured at.
 *
 * The host measurement was taken with the message column fixed at 768px and this harness frames the
 * card at 480px, and both are more room than an operator often has: a real conversation column
 * loses width to the sidebar, and the card is also read in a split pane and on a phone. 360px is
 * the CSS width of a common phone viewport, so it is a floor rather than a guess — if the delta
 * lines hold here they hold at every width above it.
 */
const NARROW_FRAME_WIDTH = 360

/** The card's own box: what it holds, and what it is showing. */
async function cardMetrics(framed: Frame) {
  return await framed.evaluate(() => {
    const card = document.querySelector('main')
    if (card === null) throw new Error('no card in the frame')
    return {
      scrollHeight: card.scrollHeight,
      clientHeight: card.clientHeight,
      scrollWidth: card.scrollWidth,
      clientWidth: card.clientWidth,
    }
  })
}

/**
 * Every delta line, with the numbers a wrap claim needs.
 *
 * Selected by the stylesheet's own class rather than by `main li`, because the copy-summary block
 * puts a second list in this document — the rich-text flavour it hands to the clipboard — and a
 * spec that counted both would be measuring markup that is never laid out for reading.
 */
async function deltaLines(framed: Frame) {
  return await framed.evaluate(() =>
    Array.from(document.querySelectorAll('[class*="deltaLines"] li')).map((line) => {
      const style = getComputedStyle(line)
      return {
        text: line.textContent ?? '',
        scrollWidth: line.scrollWidth,
        clientWidth: line.clientWidth,
        height: line.getBoundingClientRect().height,
        fontSize: Number.parseFloat(style.fontSize),
        lineHeight: Number.parseFloat(style.lineHeight),
      }
    }),
  )
}

/**
 * The host's box, narrowed from the parent.
 *
 * The parent gives the frame a fixed width, so shrinking the page's viewport would move nothing.
 * Driving the host's own attribute is the same lever the sizing lane uses when it releases the
 * scrollbar gutter from inside the frame: the spec stages the condition rather than waiting for a
 * browser to produce it.
 */
async function narrowFrame(page: Page, width: number): Promise<void> {
  await page.evaluate((value) => {
    const frame = document.getElementById('card')
    if (frame instanceof HTMLIFrameElement) {
      frame.width = String(value)
      frame.style.width = `${value}px`
    }
  }, width)
}

/** Waits until the host has applied the last height the document asked for. */
async function settledFrame(page: Page): Promise<number> {
  await expect
    .poll(async () => {
      const posts = await sizePosts(page)
      if (posts.length === 0) return false
      return (await frameBox(page)).applied === posts.at(-1)
    })
    .toBe(true)

  return (await frameBox(page)).applied
}

test('the whole card fits the frame it asks for, receipt and delta included', async ({ page }) => {
  // The surface has to be worth one screenshot: one shot, no scrolling, at the frame the host
  // opened with plus whatever the document asked for. The delta section is the newest thing on the
  // card and the one most able to break that, because it is several blocks that appear at once
  // when a receipt lands.
  const card = await frameCard(page, APPROVAL_IDS.deltaAccount, undefined, {
    height: HOST_OPENING_HEIGHT,
  })
  await expect(card.locator('main')).toBeVisible()

  // The delta really rendered. Without these two the fit below would be a claim about whatever card
  // happened to load, and it would pass most convincingly against one that rendered nothing.
  //
  // Scoped to the delta list for the reason `deltaLines` states: the same sentence also appears in
  // the copy-summary's off-screen block, which is markup for the clipboard rather than for the
  // screen, and an unscoped match would resolve to both.
  await expect(card.locator('[data-noa-verification]')).toHaveCount(1)
  await expect(
    card.locator('[class*="deltaLines"] li').filter({ hasText: 'suspended: false → true' }),
  ).toHaveCount(1)

  // The control for that scoping: the block carries the same field diff, in its own vocabulary.
  // `renderValue` prints a boolean as `yes`/`no` (`lib/approvals/summary.ts`), so the block says
  // `no → yes` where the card's list says `false → true` — a line about a boolean rather than
  // about an account, once it is out of the frame and into a ticket. Asserting the block's own
  // spelling is what keeps the scoping above honest: the two surfaces really do both carry this
  // diff, and if the block ever printed the card's spelling again the selector above would
  // silently start matching twice.
  await expect(
    card.locator('[data-noa-copy-block]').filter({ hasText: 'suspended: no → yes' }),
  ).toHaveCount(1)

  const applied = await settledFrame(page)
  const metrics = await cardMetrics(await framedDocument(page))

  // The claim: nothing is behind a scrollbar. `.card` scrolls itself by design, so a card that did
  // not fit would still be readable — and the whole point of the sizing is that an operator should
  // not have to.
  expect(metrics.scrollHeight - metrics.clientHeight).toBeLessThanOrEqual(1)

  // Two guards against a spec that cannot fail. The frame really grew past the box the host opens
  // with, so the fit is the sizing working rather than a card that was trivially short; and it
  // stopped well below the rail, so this is not a card that hit the ceiling and had its tail
  // clipped — which is the one way "it fits" could be true and worthless.
  expect(applied).toBeGreaterThan(HOST_OPENING_HEIGHT)
  expect(applied).toBeLessThan(CARD_FRAME_POLICY.ceiling)
})

test('the delta lines stay legible and unbroken in a narrow frame', async ({ page }) => {
  // The widest answer any runner produces — two backend rows, a silent source named in full, a
  // resolved expiry and a capped reading — in the narrowest frame an operator plausibly reads it
  // in. One line per row is the layout decision this measures: nothing here is a column, so a
  // long value wraps inside its own line instead of pushing a neighbour off the card.
  const card = await frameCard(page, APPROVAL_IDS.deltaFirewall, undefined, {
    height: HOST_OPENING_HEIGHT,
  })
  await expect(card.locator('main')).toBeVisible()
  await expect(card.locator('[data-noa-verification]')).toHaveCount(1)

  await narrowFrame(page, NARROW_FRAME_WIDTH)
  await settledFrame(page)

  const framed = await framedDocument(page)
  const metrics = await cardMetrics(framed)
  const lines = await deltaLines(framed)

  // The frame really is narrow, and the card really is holding the wide fixture.
  expect(metrics.clientWidth).toBeLessThanOrEqual(NARROW_FRAME_WIDTH)
  expect(lines.length).toBeGreaterThanOrEqual(2)
  expect(
    lines.some((line) => line.text.includes('csf-beta.storage-11.jakarta-dc2.internal.acme.example')),
  ).toBe(true)

  // Nothing scrolls sideways: not the card, and not a single line inside it. A line wider than its
  // box is the failure this spec exists for — the end of it is simply not on the card, and an
  // operator reading a hostname that stops halfway cannot tell that from a backend whose row was
  // cut short by NOA.
  //
  // **What this binds, and what it does not.** Forbidding these lines to wrap — `white-space:
  // nowrap` on the delta list — makes it fail, at 169px of hidden line. Removing
  // `word-break: break-word` from the same rule does **not**, and that is a fact about the content
  // rather than a weak assertion: the fixture's backend names are full internal FQDNs, 55
  // characters against a 287px line box at 7.8px per character, and they still fit because a
  // browser already breaks at their hyphens. Every value this section renders in production is
  // that shape — hostnames, addresses, field names, short verdicts — so the declaration is
  // defensive against a long opaque value no runner sends today, and no fixture here can bind it
  // without being invented. It is bound one class over, on `.factValue`, by the reflow lane.

  expect(metrics.scrollWidth - metrics.clientWidth).toBeLessThanOrEqual(1)
  for (const line of lines) {
    expect(line.scrollWidth - line.clientWidth).toBeLessThanOrEqual(1)
    // Still the stylesheet's own size: nothing shrank the type to make it fit, which is the other
    // way a narrow frame can be made to pass a width assertion.
    expect(line.fontSize).toBeGreaterThanOrEqual(13)
  }

  // The anti-tautology guard. At a wide enough frame every line fits on one row and the loop above
  // asserts nothing; this says the width was genuinely tight and at least one line had to wrap —
  // which is the case where a `word-break` regression would put text off the edge instead.
  expect(lines.some((line) => line.height > line.lineHeight * 1.5)).toBe(true)

  // And it still fits vertically at this width, where every wrapped line has made the card taller.
  expect(metrics.scrollHeight - metrics.clientHeight).toBeLessThanOrEqual(1)
})
