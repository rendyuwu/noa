import { type Frame, expect, test } from '@playwright/test'

import { STUB_TABLE_LONG_ROWS, TABLE_TOKENS } from '../playwright.config'
import { TABLE_FRAME_POLICY } from '../src/lib/embed/frame-size'
import {
  HOST_OPENING_HEIGHT,
  HOST_RESOLVER_ARGS,
  frameBox,
  frameTable,
  framedDocument,
  signIn,
  sizePosts,
} from './support/frame'

/**
 * The frame the table surface asks for, and the floor under the rows (§T.56 — V64, V85).
 *
 * The approval card's own frame, and the notice states', are `frame-size-card.browser.e2e.ts`: same
 * harness and same parent, two subjects whose measurements have nothing to say to each other.
 *
 * **Every claim in this file is a layout claim, which is why none of them is in the vitest lane.**
 * jsdom computes no layout: no `max-height`, no flex shrink, no `dvh`, and `getBoundingClientRect()`
 * returns zeros. "At a small frame the table renders at least one data row" is green in jsdom *with
 * the defect live*, because the defect is a flex resolution and jsdom resolves no flex. So the
 * arithmetic lives in `src/lib/embed/frame-size.test.ts`, the message and the bookkeeping live in
 * `src/components/frame-sizer.test.tsx`, and what is measured here is a browser doing layout.
 *
 * **The frame is the one LibreChat serves** (`support/frame.ts`): the parent origin §T.45 allows,
 * the sandbox string R13/R29 measured with `allow-forms` absent, and a parent that applies a
 * `ui-size-change` to the iframe's inline style the way `@mcp-ui/client` was measured doing —
 * verbatim, gated on the message coming from the frame's own `contentWindow`, with no clamp of its
 * own. A harness more forgiving than the host would prove nothing.
 *
 * V90: no readiness wait of its own; the servers are gated on their open sockets by
 * `playwright.config.ts`, and every wait below is on the frame's own content or on the parent's
 * record of what it received.
 */

test.use({ launchOptions: { args: HOST_RESOLVER_ARGS } })

test.beforeEach(async ({ context }) => {
  await signIn(context)
})

/** A frame deliberately smaller than the host's default and far under the table's ceiling. */
const SMALL_FRAME_HEIGHT = 320

/** `min-height: 7rem` in `table.module.css`, at the 16px root this app never overrides. */
const SCROLLER_FLOOR_PX = 112

/** Geometry, by structural selectors: a CSS module's generated class names are not an interface. */
async function tableGeometry(framed: Frame) {
  return await framed.evaluate(() => {
    function rect(element: Element | null | undefined) {
      if (!element) return null
      const box = element.getBoundingClientRect()
      return { top: box.top, bottom: box.bottom, height: box.height }
    }

    const page = document.querySelector('main')
    const rows = document.querySelector('table')

    return {
      viewport: document.documentElement.clientHeight,
      /** The reserved gutter, which no height this page posts can be made to depend on. */
      gutter: page === null ? null : getComputedStyle(page).scrollbarGutter,
      /** What the page is holding beyond its own box: zero means nothing is out of reach. */
      pageHidden: page === null ? 0 : page.scrollHeight - page.clientHeight,
      /** The rows' scroller is the table's parent. */
      scroller: rect(rows?.parentElement),
      scrollerHidden:
        rows?.parentElement === undefined || rows?.parentElement === null
          ? 0
          : rows.parentElement.scrollHeight - rows.parentElement.clientHeight,
      bound: rect(document.querySelector('main header p')),
      truncationFlag: rect(document.querySelector('main header p span')),
      firstRow: rect(document.querySelector('tbody tr')),
      // The expiry line is the only direct `<p>` child of `main` on a table that has rows.
      footer: rect(document.querySelector('main > p')),
    }
  })
}

/*
 * The next two specs are one criterion split in two, and the split is arithmetic rather than
 * caution. Read them as a pair.
 *
 * "Everything on screen at once" is asked of a 320px frame and not of the 150px one the host opens
 * with, because at 150px it is impossible. What the surface owes before a single row is drawn, at
 * this stylesheet's metrics:
 *
 *   `.page` padding, `--noa-space-4` top and bottom              32px
 *   two `--noa-space-3` gaps between its three children          24px
 *   the header: 1rem tool name, `--noa-space-1`, the bound line  48-67px  (the bound wraps to a
 *                                                                         second line on a capped
 *                                                                         page at this width)
 *   the expiry line, 0.75rem                                     18px
 *                                                                ------
 *                                                                122-141px
 *
 * That leaves under 30px of a 150px frame, and one sticky column-heading row is about 34px on its
 * own — so at 150px the choice is not between a good layout and a bad one, it is between which
 * element is off screen. At 320px the same terms leave about 179px, which is the heading row plus
 * four data rows, and the whole surface fits with nothing hidden.
 *
 * So the first spec is the strong claim at the smallest frame that can carry it, and the second is
 * deliberately the weaker of the two: at 150px it asserts what is true there — the rows are
 * rendered at their floor rather than collapsed, and every element the frame cannot show is
 * REACHABLE. That second half is the one the change to `.page` bought: `overflow: hidden` clips,
 * and what it clipped was the expiry line. Neither spec lets the frame grow, because a frame that
 * grew would hide the collapse both of them are about.
 */

test('a small frame still shows rows, the bound, the flag and the expiry line', async ({ page }) => {
  // The whole surface at a frame under half the ceiling, with the host's autosizing switched off —
  // so what is measured is the stylesheet alone, not the stylesheet plus a frame that grew to hide
  // a collapse. The capped fixture, because the truncation flag is one of the four things that has
  // to be on screen.
  const table = await frameTable(page, TABLE_TOKENS.truncated, undefined, {
    height: SMALL_FRAME_HEIGHT,
    autosize: false,
  })
  await expect(table.locator('tbody tr').first()).toBeVisible()

  const geometry = await tableGeometry(await framedDocument(page))

  // The rows did not collapse. `flex-shrink: 1` is the default and is what permitted it; the floor
  // is what refuses it, and a `toBeVisible()` assertion would not notice the difference — a clipped
  // row still has a non-empty box of its own.
  expect(geometry.scroller!.height).toBeGreaterThanOrEqual(SCROLLER_FLOOR_PX)
  // All four inside the visible box, the expiry line included. It is last in flow and therefore the
  // one at risk: answering "no rows" with "no expiry" is not a fix.
  expect(geometry.firstRow!.bottom).toBeLessThanOrEqual(geometry.viewport)
  expect(geometry.bound!.bottom).toBeLessThanOrEqual(geometry.viewport)
  // By the same standard as its neighbours, not by having a box of its own. The flag is a `<span>`
  // inside the bound paragraph asserted above, so a non-empty box would hold transitively — but an
  // assertion that leans on its container's assertion is one edit away from holding nothing.
  expect(geometry.truncationFlag!.bottom).toBeLessThanOrEqual(geometry.viewport)
  expect(geometry.footer!.bottom).toBeLessThanOrEqual(geometry.viewport)
  expect(geometry.pageHidden).toBe(0)
  // The reserved gutter on `.page`, asserted as computed style because no posted height depends on
  // it: the wrapping text here is `.tool`, `.bound` and `.footer` rather than the nowrap cells, and
  // the 15px does cross a line boundary at some bound lengths — but at none the lane serves, so the
  // capped page measures 271px whether the gutter is reserved or released. That stylesheet's own
  // comment carries the sweep. Without this line the declaration could be dropped silently.
  expect(geometry.gutter).toBe('stable')
})

test('at the box the host opens with, the expiry line is reachable rather than clipped', async ({
  page,
}) => {
  // The weaker half of the pair, at the box the host opens with — see the arithmetic above for why
  // it is the weaker one. Rendered rather than collapsed, and reachable rather than clipped.
  const table = await frameTable(page, TABLE_TOKENS.truncated, undefined, {
    height: HOST_OPENING_HEIGHT,
    autosize: false,
  })
  await expect(table.locator('tbody tr').first()).toBeVisible()

  const framed = await framedDocument(page)
  const before = await tableGeometry(framed)

  expect(before.scroller!.height).toBeGreaterThanOrEqual(SCROLLER_FLOOR_PX)
  // Part of the rows is on screen, and the rest of the page is out of reach of nothing.
  expect(before.firstRow!.top).toBeLessThan(before.viewport)
  expect(before.pageHidden).toBeGreaterThan(0)

  // Scrolled by the WHEEL, not by assigning `scrollTop`. An `overflow: hidden` box is still
  // programmatically scrollable, so a scripted scroll is satisfied by the very stylesheet this spec
  // exists to refuse — measured: with `.page` reverted to `hidden`, the `scrollTop` version and the
  // whole of this file stayed green. The claim is that an OPERATOR reaches the expiry line, and the
  // only input that makes that claim is an operator's.
  //
  // The cursor goes near the top of the frame, over the header rather than over the rows: the rows'
  // own scroller would take the wheel first and only chain outwards once it hit its end, which would
  // make this spec depend on how many rows the capped fixture happens to hold.
  const framePosition = (await page.locator('#card').boundingBox())!
  await page.mouse.move(framePosition.x + framePosition.width / 2, framePosition.y + 24)
  await page.mouse.wheel(0, 600)

  // One pixel of tolerance: a fractional viewport rounds, and the claim is "on screen", not "on
  // screen to the pixel" (V87's rule about what a compare may eat). Polled because a wheel is
  // delivered asynchronously, and bounded so a box that refuses to scroll fails as an assertion
  // rather than as the suite's own timeout.
  await expect
    .poll(
      async () => {
        const after = await tableGeometry(framed)
        return after.footer!.bottom - after.viewport
      },
      { timeout: 5000 },
    )
    .toBeLessThanOrEqual(1)
})

test('the height the document asks for is the height the frame gets', async ({ page }) => {
  // The round trip, not a well-shaped message: the parent applies what it receives, and this is the
  // assertion that the two ends agree. If the target origin were wrong the browser would drop every
  // message silently, and this is the spec that would go red for it.
  await frameTable(page, TABLE_TOKENS.truncated, undefined, { height: HOST_OPENING_HEIGHT })

  await expect.poll(() => sizePosts(page)).not.toEqual([])

  const posts = await sizePosts(page)
  const asked = posts.at(-1)!

  expect(asked).toBeGreaterThan(HOST_OPENING_HEIGHT)

  const box = await frameBox(page)
  expect(box.applied).toBe(asked)
  // The laid-out box is 4px larger, and that is the UA's `border: 2px inset` on an iframe rather
  // than anything this app computed. Asserted as a floor so this cannot pass against a collapsed
  // frame, and named so a later reader does not go hunting for four pixels of arithmetic.
  expect(box.rendered).toBeGreaterThanOrEqual(asked)
})

test(`a ${STUB_TABLE_LONG_ROWS}-row listing asks for a bounded frame and keeps scrolling`, async ({
  page,
}) => {
  // The listing the complaint arrived on. Its natural height is on the order of 15,000px and the
  // host applies whatever it is sent, so an unbounded request would trade one unusable surface for
  // another — and it would defeat `.scroller`, which is the thing that makes a long listing
  // readable at all.
  const table = await frameTable(page, TABLE_TOKENS.long, undefined, { height: HOST_OPENING_HEIGHT })
  await expect(table.locator('tbody tr').first()).toBeVisible()

  await expect.poll(() => sizePosts(page)).not.toEqual([])

  const posts = await sizePosts(page)
  expect(posts.at(-1)).toBe(TABLE_FRAME_POLICY.ceiling)
  expect((await frameBox(page)).applied).toBe(TABLE_FRAME_POLICY.ceiling)

  const framed = await framedDocument(page)
  const geometry = await tableGeometry(framed)

  // Bounded frame, so the rows are still scrolling inside it — and at the height the page asked
  // for, the expiry line is on screen without a scroll.
  expect(geometry.scrollerHidden).toBeGreaterThan(1000)
  expect(geometry.footer!.bottom).toBeLessThanOrEqual(geometry.viewport)
  expect(geometry.pageHidden).toBe(0)

  const scrolled = await framed.evaluate(() => {
    const scroller = document.querySelector('table')?.parentElement
    if (!scroller) return 0
    scroller.scrollTop = 400
    return scroller.scrollTop
  })
  expect(scrolled).toBe(400)
})
