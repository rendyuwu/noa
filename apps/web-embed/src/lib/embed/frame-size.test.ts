import { describe, expect, it } from 'vitest'

import {
  CARD_FRAME_POLICY,
  FRAME_POST_BUDGET,
  FRAME_SIZE_MESSAGE_TYPE,
  type FrameSizePolicy,
  TABLE_FRAME_POLICY,
  boxEdgeHeight,
  cssPixels,
  nestedOverflow,
  nextFrameHeight,
} from './frame-size'

/**
 * The number this app asks its host for (the approval card and the table surface).
 *
 * Every claim here is arithmetic, which is why it is here: jsdom computes no layout, so a rule that
 * lived inside the component could only be asserted in a browser. What the browser lane owns is the
 * other half — that a `max-height` clamps, that flex shrinks, that a posted height is applied — and
 * `e2e/frame-size.browser.e2e.ts` and `e2e/frame-size-card.browser.e2e.ts` hold it, split by
 * surface.
 */

/** A policy with round numbers, so a failure names the rule rather than the constant. */
const POLICY: FrameSizePolicy = { floor: 100, ceiling: 1000, epsilon: 10 }

const NO_EDGES = { borderTopWidth: '', borderBottomWidth: '', marginTop: '', marginBottom: '' }

describe('nextFrameHeight — one bound rule, four branches', () => {
  it('posts what was measured, between the floor and the ceiling', () => {
    expect(nextFrameHeight({ measured: 480, edges: 0, lastPosted: null, policy: POLICY })).toBe(480)
  })

  it('posts the floor for a measurement below it', () => {
    // The state this covers is a measurement taken before the layout it describes exists, which
    // reads as a height near zero. Posting that would collapse the frame to nothing.
    expect(nextFrameHeight({ measured: 12, edges: 0, lastPosted: null, policy: POLICY })).toBe(100)
  })

  it('posts the CEILING for a measurement above it, and never null', () => {
    // The load-bearing branch. `null` here would leave a pathological payload sitting in the box
    // the host opened with, which is the defect the whole module exists to fix — so "too tall" has
    // to answer with a height, not with silence.
    expect(nextFrameHeight({ measured: 40_000, edges: 0, lastPosted: null, policy: POLICY })).toBe(
      1000,
    )
  })

  it('says nothing more once it is sitting on the ceiling', () => {
    // The other half of the ceiling branch: a page that keeps growing past the rail must not keep
    // re-posting the same number until the budget is gone.
    expect(
      nextFrameHeight({ measured: 40_000, edges: 0, lastPosted: 1000, policy: POLICY }),
    ).toBeNull()
  })
})

describe('nextFrameHeight — monotonic within a mount', () => {
  it('an alternating measurement terminates inside two posts, budget untouched', () => {
    // The fixture the loop would have produced: 400, 440, 400, 440, ... with an amplitude well
    // above the epsilon. Monotonicity alone silences it, and the post budget is never reached —
    // which is the point of the budget being a backstop rather than the control.
    let lastPosted: number | null = null
    const posted: number[] = []

    for (let tick = 0; tick < 20; tick += 1) {
      const height = nextFrameHeight({
        measured: tick % 2 === 0 ? 400 : 440,
        edges: 0,
        lastPosted,
        policy: POLICY,
      })
      if (height !== null) {
        posted.push(height)
        lastPosted = height
      }
    }

    expect(posted).toEqual([400, 440])
    expect(posted.length).toBeLessThan(FRAME_POST_BUDGET)
  })

  it('posts nothing at all for a measurement that is not a number', () => {
    // Not a bound decision: the absence of a measurement. The floor is for heights that were
    // measured, and a `NaN` frame height would be applied verbatim by a host that does not clamp.
    expect(
      nextFrameHeight({ measured: Number.NaN, edges: 0, lastPosted: null, policy: POLICY }),
    ).toBeNull()
    expect(
      nextFrameHeight({ measured: 480, edges: Number.NaN, lastPosted: null, policy: POLICY }),
    ).toBeNull()
  })
})

describe('the border and margin term', () => {
  it('parses pixel strings and treats anything else as nothing', () => {
    expect(cssPixels('12px')).toBe(12)
    expect(cssPixels('0.5px')).toBe(0.5)
    expect(cssPixels('')).toBe(0)
    expect(cssPixels('auto')).toBe(0)
    expect(cssPixels(null)).toBe(0)
    expect(cssPixels(undefined)).toBe(0)
  })

  it('adds the four edges a scrollHeight measurement leaves out', () => {
    expect(
      boxEdgeHeight({
        borderTopWidth: '1px',
        borderBottomWidth: '1px',
        marginTop: '4px',
        marginBottom: '6px',
      }),
    ).toBe(12)
  })

  it('is zero for the box the stylesheets actually produce today', () => {
    // `.card` and `.page` carry padding and no border, and `body` has no margin. Computed rather
    // than assumed: a stylesheet edit adding a 1px border would otherwise clip the bottom of the
    // card by two pixels, which reads as a rendering bug and not as an arithmetic one.
    expect(boxEdgeHeight(NO_EDGES)).toBe(0)
  })

  it('rounds, because a frame height is applied verbatim', () => {
    expect(nextFrameHeight({ measured: 480.4, edges: 0.2, lastPosted: null, policy: POLICY })).toBe(
      481,
    )
  })
})

describe('nestedOverflow — what a nested scroller is hiding', () => {
  it('is the content the scroller holds beyond its own box', () => {
    expect(nestedOverflow(15_800, 320)).toBe(15_480)
  })

  it('is nothing when the scroller is showing everything', () => {
    expect(nestedOverflow(320, 320)).toBe(0)
  })

  it('is never negative, and never NaN', () => {
    // `clientHeight` above `scrollHeight` is not a state that should subtract from a frame height.
    expect(nestedOverflow(320, 400)).toBe(0)
    expect(nestedOverflow(Number.NaN, 320)).toBe(0)
  })
})

describe('the message', () => {
  it('carries the type the host listens for', () => {
    // The host's name, not this app's. Renaming it makes every message a no-op, silently.
    expect(FRAME_SIZE_MESSAGE_TYPE).toBe('ui-size-change')
  })
})

describe('the per-surface policies', () => {
  it('are the numbers the module states, and they separate', () => {
    // Pinned so that widening a ceiling is an edit to this line rather than a quiet drift, and so
    // that the difference between the two surfaces cannot be erased by accident: the card asks for
    // its content height and the table asks for at most a screen of rows.
    expect(CARD_FRAME_POLICY).toEqual({ floor: 160, ceiling: 4800, epsilon: 8 })
    expect(TABLE_FRAME_POLICY).toEqual({ floor: 160, ceiling: 720, epsilon: 8 })
    expect(TABLE_FRAME_POLICY.ceiling).toBeLessThan(CARD_FRAME_POLICY.ceiling)
    expect(FRAME_POST_BUDGET).toBe(12)
  })

  it('bound the listing that started this work', () => {
    // 438 rows at about 36px each, which is what the stylesheet produces. The natural height is on
    // the order of 15,000px and the host applies whatever it is sent, so the request has to be the
    // ceiling — a 15,000px iframe in a chat conversation is as unusable as a 150px one.
    const natural = 438 * 36

    expect(
      nextFrameHeight({ measured: natural, edges: 0, lastPosted: null, policy: TABLE_FRAME_POLICY }),
    ).toBe(720)
  })

  it('let a short listing stay short', () => {
    // The separating case: without it, "the table is bounded" would pass against a surface that
    // always asks for 720px and leaves a two-row table sitting in an empty box.
    expect(
      nextFrameHeight({ measured: 240, edges: 0, lastPosted: null, policy: TABLE_FRAME_POLICY }),
    ).toBe(240)
  })

  it('let the card ask for its whole content, which is what the table may not do', () => {
    // The card is the surface that has to be worth one screenshot, so its ceiling is a rail it does
    // not normally meet. Same measurement, two answers — that is the policy argument earning itself.
    expect(
      nextFrameHeight({ measured: 2400, edges: 0, lastPosted: null, policy: CARD_FRAME_POLICY }),
    ).toBe(2400)
    expect(
      nextFrameHeight({ measured: 2400, edges: 0, lastPosted: null, policy: TABLE_FRAME_POLICY }),
    ).toBe(720)
  })
})
