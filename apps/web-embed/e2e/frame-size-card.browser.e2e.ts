import { type Frame, expect, test } from '@playwright/test'

import { APPROVAL_IDS, SIGN_IN_URL, TABLE_TOKENS } from '../playwright.config'
import { CARD_FRAME_POLICY, FRAME_POST_BUDGET } from '../src/lib/embed/frame-size'
import {
  HOST_OPENING_HEIGHT,
  HOST_RESOLVER_ARGS,
  frameBox,
  frameCard,
  frameTable,
  framedDocument,
  signIn,
  sizePosts,
} from './support/frame'

/**
 * The frame the approval card and the notice states ask for.
 *
 * Split from `frame-size.browser.e2e.ts`, which keeps the table surface: same harness, same parent,
 * two subjects whose measurements have nothing to say to each other. Everything the header of that
 * file states about the lane applies here — every claim is a layout claim, which is why none of them
 * is in the vitest lane; the frame is the one LibreChat serves; and there is no readiness wait of
 * this file's own.
 *
 * **What is measured here is the two controls that stop the sizing oscillating, and the one state
 * whose way out the sizing was leaving off screen.**
 */

test.use({ launchOptions: { args: HOST_RESOLVER_ARGS } })

test.beforeEach(async ({ context }) => {
  await signIn(context)
})

/**
 * The card's content box, and the one declaration that moves it.
 *
 * **A scrollbar takes no layout width in this lane's browser.** Measured on the reflow fixture: with
 * the gutter released, an `overflow-y: auto` box whose content is 911px tall inside a 150px frame
 * reports `clientWidth` 480 — its full width — and `window.innerWidth - documentElement.clientWidth`
 * is 0. Neither sizing `::-webkit-scrollbar` nor `scrollbar-width: thin` changes that. So the
 * scrollbar-mediated width change `nextFrameHeight` is written against cannot arise here on its own,
 * and `scrollbar-gutter: stable` is the only thing that moves this document's content box at all:
 * 465px reserved against 480px released, which on this fixture is 950px of card against 911px.
 *
 * Two consequences, and both shape the specs below. Removing the declaration changes no number a
 * post count can see, so it is asserted as computed style or it is not asserted. And releasing it
 * from inside the frame is the one lever that reproduces here what a desktop browser's disappearing
 * scrollbar does by itself.
 */
async function cardMetrics(framed: Frame) {
  return await framed.evaluate(() => {
    const card = document.querySelector('main')
    if (card === null) return null
    return {
      gutter: getComputedStyle(card).scrollbarGutter,
      contentWidth: card.clientWidth,
      naturalHeight: card.scrollHeight,
    }
  })
}

/**
 * The 401 state's printed address, found by its text rather than by a generated class name.
 *
 * This is the escape hatch itself and not a convenience beside the link: the sandbox at one of
 * LibreChat's two render sites omits `allow-popups`, so the "Sign in to NOA" link opens nothing at
 * all there and silently, and what an operator is left with is this string to copy.
 */
async function printedAddress(framed: Frame, address: string) {
  return await framed.evaluate((value) => {
    const span = Array.from(document.querySelectorAll('main span')).find(
      (node) => node.textContent === value,
    )
    if (span === undefined) return null
    const box = span.getBoundingClientRect()
    return {
      top: box.top,
      bottom: box.bottom,
      viewport: document.documentElement.clientHeight,
      gutter: getComputedStyle(document.querySelector('main')!).scrollbarGutter,
    }
  }, address)
}

/** Widens the card's content box by the gutter's width — see `cardMetrics` for why this is the lever. */
async function releaseGutter(framed: Frame): Promise<void> {
  await framed.evaluate(() => {
    const card = document.querySelector<HTMLElement>('main')
    if (card !== null) card.style.scrollbarGutter = 'auto'
  })
}

test('a card whose values re-wrap settles instead of oscillating', async ({ page }) => {
  // The loop this could have shipped is scrollbar-mediated: a frame that grows until the content
  // fits REMOVES the scrollbar, which widens the content box by the scrollbar's width, which lets
  // wrapped lines fit, which shrinks the measurement, which shrinks the frame, which brings the
  // scrollbar back. `.evidenceLines` is monospace with `word-break: break-word` — content that gains or
  // loses a whole line on a 15px width change — and this fixture is a card full of it.
  //
  // Two instruments, and what is asserted here is that neither of them was needed as a *cap*: the
  // reserved scrollbar gutter means the width never changes, and the monotonic rule means a
  // decrease is never posted. The unit lane holds the control that a decrease is reachable input
  // and is refused (`never asks for a shorter frame within one mount`); what a browser adds is that
  // the real fixture stops asking.
  const card = await frameCard(page, APPROVAL_IDS.reflow, undefined, {
    height: HOST_OPENING_HEIGHT,
  })
  await expect(card.locator('main')).toBeVisible()

  await expect.poll(() => sizePosts(page)).not.toEqual([])

  // Long enough for a reflow to have happened several times over if one were going to. The count is
  // read twice with the wait between, because "it converged" is a claim about a run that had time
  // to diverge.
  const settled = await sizePosts(page)
  await page.waitForTimeout(1500)
  const later = await sizePosts(page)

  expect(later).toEqual(settled)
  expect(later.length).toBeLessThan(FRAME_POST_BUDGET)
  // Strictly increasing: the monotonic rule, seen from outside.
  expect([...later].sort((a, b) => a - b)).toEqual(later)
  expect(new Set(later).size).toBe(later.length)
  // The fixture really did overflow the opening box, so the wrapping was in play rather than the
  // card being trivially short.
  expect(later.at(-1)).toBeGreaterThan(400)
  expect((await frameBox(page)).applied).toBe(later.at(-1))

  // The backstop was not what stopped it. A frame that hit the budget would have stopped asking for
  // a reason an operator cannot see, which is why that state is on the page at all.
  await expect(card.locator('[data-noa-frame-size]')).toHaveAttribute(
    'data-noa-frame-size',
    'measuring',
  )

  // The first instrument, asserted directly rather than inferred from the count above. Everything
  // else in this spec is equally true with the declaration deleted, because a scrollbar occupies no
  // layout width here — see `cardMetrics`. Without this line the primary control could be dropped
  // from the stylesheet and no lane would notice.
  expect((await cardMetrics(await framedDocument(page)))!.gutter).toBe('stable')
})

test('a width change that shortens the card does not shorten the frame', async ({ page }) => {
  // The second instrument on its own, with the first one switched off from inside the frame.
  //
  // The spec above cannot separate the two: with the gutter reserved the width never moves, so one
  // measurement is taken and one height is posted, and the monotonic rule is never asked anything.
  // This spec asks it. Releasing the gutter widens the content box by 15px, which on this fixture is
  // a card 39px shorter than the frame it is sitting in — the same event a desktop browser produces
  // for free when a frame grows past its content and the scrollbar goes away, and the first step of
  // the loop `nextFrameHeight`'s comment describes.
  //
  // What is asserted is that nothing is posted for it. The two jobs — "too small to bother with" and
  // "never shorter" — are one comparison in that function, so the refactor that would look most
  // harmless is wrapping it in `Math.abs`. That refactor turns the posts below into an ascent
  // followed by a descent, which is what this spec exists to catch. The control that a decrease is
  // reachable *input* and is refused lives in the unit lane (`lib/embed/frame-size.test.ts`).
  const card = await frameCard(page, APPROVAL_IDS.reflow, undefined, {
    height: HOST_OPENING_HEIGHT,
  })
  await expect(card.locator('main')).toBeVisible()
  await expect.poll(() => sizePosts(page)).not.toEqual([])

  const framed = await framedDocument(page)
  const settled = await sizePosts(page)
  const reserved = await cardMetrics(framed)
  expect(reserved!.gutter).toBe('stable')

  await releaseGutter(framed)
  const released = await cardMetrics(framed)

  // The reflow really happened, and the drop really is large enough to be worth a message under a
  // symmetric rule. Without these two, "nothing was posted" would be a claim about a page that never
  // changed — a spec that cannot fail for the reason it was written.
  expect(released!.contentWidth).toBeGreaterThan(reserved!.contentWidth)
  expect(settled.at(-1)! - released!.naturalHeight).toBeGreaterThan(CARD_FRAME_POLICY.epsilon)

  // Long enough for the observer to have fired and a post to have gone out if one were going to.
  await page.waitForTimeout(1000)

  expect(await sizePosts(page)).toEqual(settled)
  // And the host's box is where it was: the frame never followed the content down.
  expect((await frameBox(page)).applied).toBe(settled.at(-1))
})

/*
 * The 401 state, on both surfaces, at the box the host opens with.
 *
 * This is the state with the strongest claim on the sizing and the last one to get it. What it hands
 * an operator is a printed address, and that address is the door the frame's sandbox cannot withhold
 * — so a frame too short to show it is a way out left off screen. Measured before the sizer
 * was mounted here, at 150px: the address began 4.5px below the fold and ended 30px past it. Never
 * unreachable — `.notice` is `max-height: 100dvh; overflow-y: auto` and scrolls — and off screen all
 * the same.
 *
 * Both surfaces, because both render this state through the same component and the whole reason it
 * is one component is that the way out must not go missing from one of them. The 401 fixtures
 * answer 401 forever, which is the state the never-a-blank-card rule renders and a retry
 * cannot escape.
 *
 * `tables.browser.e2e.ts` already asserts that the address is *present* and that no form is; it runs
 * at the harness's default 640px frame, where the fold is nowhere near. These two are about the fold.
 */

for (const surface of ['card', 'table'] as const) {
  test(`the 401 state's printed address is on screen at the host's opening box (${surface})`, async ({
    page,
  }) => {
    const framed =
      surface === 'card'
        ? await frameCard(page, APPROVAL_IDS.unauthorized, undefined, {
            height: HOST_OPENING_HEIGHT,
          })
        : await frameTable(page, TABLE_TOKENS.unauthorized, undefined, {
            height: HOST_OPENING_HEIGHT,
          })
    await expect(framed.locator('main h1')).toContainText('Cannot authenticate here')

    // Polled on the address rather than on the post: what matters is where the string ended up once
    // the host had applied the height, and those are two events.
    await expect
      .poll(
        async () => {
          const seen = await printedAddress(await framedDocument(page), SIGN_IN_URL)
          return seen === null ? Number.POSITIVE_INFINITY : seen.bottom - seen.viewport
        },
        { timeout: 5000 },
      )
      .toBeLessThanOrEqual(0)

    const address = await printedAddress(await framedDocument(page), SIGN_IN_URL)
    // Both edges: a frame that grew past the ceiling would put the top of the address above the fold
    // rather than the bottom below it, and "on screen" is a claim about the whole string.
    expect(address!.top).toBeGreaterThanOrEqual(0)
    // The frame really did grow — otherwise this would be a claim about a 150px box that happened to
    // fit, which it does not.
    expect((await frameBox(page)).applied).toBeGreaterThan(HOST_OPENING_HEIGHT)
    // And this surface reserves its gutter like the other two, for the reason `notice.module.css`
    // states: `.noticeAddress` is `word-break: break-all`, which is the content a 15px width change
    // re-wraps.
    expect(address!.gutter).toBe('stable')
  })
}
