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
 * The evidence block and the runner's sentence, measured in the frame LibreChat serves.
 *
 * **Every claim here is a layout claim, which is why none of them is in the component lane.** jsdom
 * computes no layout and no styles at all — no widths, no wrapping, no `max-height`,
 * `getBoundingClientRect()` returns zeros, and a CSS module resolves to a bare class name — so "the
 * card fits", "the lines do not break" and "the newline is a break" are assertions it cannot make
 * and would silently pass. The component lane holds what the renderer says
 * (`src/app/approvals/[id]/outcome-view.test.tsx`); this file holds what it looks like.
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
 * Every evidence line, with the numbers a wrap claim needs.
 *
 * Selected by the stylesheet's own class rather than by `main li`, because the copy-summary block
 * puts a second list in this document — the rich-text flavour it hands to the clipboard, carrying
 * the very same lines — and a spec that counted both would be measuring markup that is never laid
 * out for reading.
 */
async function evidenceLines(framed: Frame) {
  return await framed.evaluate(() =>
    Array.from(document.querySelectorAll('[class*="evidenceLines"] li')).map((line) => {
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

test('the whole card fits the frame it asks for, evidence block included', async ({ page }) => {
  // The surface has to be worth one screenshot: one shot, no scrolling, at the frame the host
  // opened with plus whatever the document asked for. The evidence block is the part most able to
  // break that, because it is an unbounded list of the target system's own lines.
  const card = await frameCard(page, APPROVAL_IDS.deltaFirewall, undefined, {
    height: HOST_OPENING_HEIGHT,
  })
  await expect(card.locator('main')).toBeVisible()

  // The block really rendered. Without these the fit below would be a claim about whatever card
  // happened to load, and it would pass most convincingly against one that rendered nothing.
  //
  // Scoped for the reason `evidenceLines` states: the copy-summary's off-screen block carries the
  // very same lines, and an unscoped match would resolve to both.
  await expect(card.locator('[data-noa-statement]')).toHaveCount(1)
  await expect(card.locator('[data-noa-evidence] h2')).toHaveText('Why it was blocked')
  await expect(card.locator('[class*="evidenceLines"] li')).toHaveCount(2)

  // The control for that scoping, and it is the whole point of the shared body: the block really
  // does carry the same bytes. If it ever stopped, the selectors above would silently start
  // matching once instead of twice — and the card and the ticket would be two statements of one
  // measurement, which the parity spec in the component lane exists to refuse.
  await expect(
    card
      .locator('[data-noa-copy-block]')
      .filter({ hasText: 'this is the first 2 of 34 lines' }),
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

test('the evidence lines stay legible and unbroken in a narrow frame', async ({ page }) => {
  // The widest reading any gate produces — a firewall's own deny lines, carrying an internal FQDN
  // in their comment — in the narrowest frame an operator plausibly reads it in. One line per row
  // is the layout decision this measures: nothing here is a column, so a long line wraps inside its
  // own row instead of pushing a neighbour off the card.
  const card = await frameCard(page, APPROVAL_IDS.deltaFirewall, undefined, {
    height: HOST_OPENING_HEIGHT,
  })
  await expect(card.locator('main')).toBeVisible()
  await expect(card.locator('[data-noa-statement]')).toHaveCount(1)

  await narrowFrame(page, NARROW_FRAME_WIDTH)
  await settledFrame(page)

  const framed = await framedDocument(page)
  const metrics = await cardMetrics(framed)
  const lines = await evidenceLines(framed)

  // The frame really is narrow, and the card really is holding the wide fixture.
  expect(metrics.clientWidth).toBeLessThanOrEqual(NARROW_FRAME_WIDTH)
  expect(lines.length).toBeGreaterThanOrEqual(2)
  expect(
    lines.some((line) => line.text.includes('csf-beta.storage-11.jakarta-dc2.internal.acme.example')),
  ).toBe(true)
  // And verbatim really is verbatim: the double space csf printed between the verdict and the
  // address is still there in the laid-out node, not collapsed by a renderer on the way.
  expect(lines.some((line) => line.text.includes('DENY  203.0.113.24'))).toBe(true)

  // Nothing scrolls sideways: not the card, and not a single line inside it. A line wider than its
  // box is the failure this spec exists for — the end of it is simply not on the card, and an
  // operator reading a hostname that stops halfway cannot tell that from a backend whose row was
  // cut short by NOA.
  //
  // **What this binds.** Forbidding these lines to wrap — `white-space: nowrap` on the evidence
  // list — makes it fail, at a measured width of hidden line. `word-break: break-word` is bound by
  // the same fixture now that the lines are the firewall's own: a deny line carrying a 55-character
  // internal FQDN inside a comment is exactly the string a browser has to be allowed to break
  // somewhere other than a space.

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

test('a newline in the runner’s sentence is a line break on the card', async ({ page }) => {
  // **The half jsdom cannot stand in for.** A `\n` inside `message` is the contract — four of the
  // five composing families spell one that way — and a plain `<p>` collapses it to a space, so the
  // break the runner wrote never appears. The component lane proves the byte reaches the DOM; only
  // a real computed style can prove it is rendered.
  //
  // The fixture's two sentences fit on one line at this frame's width, so the second line box is
  // the newline and not a wrap — and switching the declaration off from inside the frame is the
  // negative control that says so. Without it, "two line boxes" would pass against a paragraph that
  // was merely too long.
  const card = await frameCard(page, APPROVAL_IDS.deltaAccount, undefined, {
    height: HOST_OPENING_HEIGHT,
  })
  await expect(card.locator('[data-noa-statement]')).toHaveCount(1)

  const framed = await framedDocument(page)
  const measured = await framed.evaluate(() => {
    const statement = document.querySelector('[data-noa-statement]')
    if (!(statement instanceof HTMLElement)) throw new Error('no statement on the card')

    // A Range over the contents, not the element's own box: a block-level `<p>` has exactly one
    // border box however many lines it holds, so counting its rects would answer 1 for both halves
    // of this measurement and the spec would fail for a reason that has nothing to do with the rule.
    //
    // Counted by distinct vertical position rather than by rect, because a range spanning a
    // preserved newline yields more rects than there are lines — the break itself gets one.
    const lineBoxes = (): number => {
      const range = document.createRange()
      range.selectNodeContents(statement)
      const tops = Array.from(range.getClientRects()).map((rect) => Math.round(rect.top))
      return new Set(tops).size
    }

    const whiteSpace = getComputedStyle(statement).whiteSpace
    const broken = lineBoxes()
    statement.style.whiteSpace = 'normal'
    const collapsed = lineBoxes()
    return { whiteSpace, broken, collapsed, text: statement.textContent ?? '' }
  })

  expect(measured.text).toContain('\n')
  expect(measured.whiteSpace).toBe('pre-line')
  expect(measured.broken).toBe(2)
  // The control: with the declaration off, the same bytes are one line. So the break above is the
  // stylesheet doing the work, on a sentence that had room for both halves.
  expect(measured.collapsed).toBe(1)
})
