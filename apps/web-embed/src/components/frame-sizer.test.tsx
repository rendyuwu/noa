import { cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LIBRECHAT_ORIGIN_ENV_VAR } from '../../config/framing'
import { resolveFrameTargetOrigin } from '@/lib/embed/frame-origin'
import { CARD_FRAME_POLICY, FRAME_POST_BUDGET, TABLE_FRAME_POLICY } from '@/lib/embed/frame-size'

import { FrameSizer } from './frame-sizer'

/**
 * What the sizer sends, and when it stops (the approval card, the large-result table surface).
 *
 * **What this lane can prove is the message and the bookkeeping**, and that is deliberate: jsdom
 * computes no layout, so `scrollHeight` here is a number this file chose rather than one a layout
 * engine produced. Every claim that depends on real layout — that `max-height` clamps, that flex
 * shrinks a scroller, that a posted height is actually applied to the frame — is a browser claim
 * and lives in `e2e/frame-size.browser.e2e.ts` and `e2e/frame-size-card.browser.e2e.ts`, split by
 * surface. `ResizeObserver` does not exist in jsdom either, so
 * the re-measurement here is driven by re-rendering, which is one of the two ways it happens for
 * real.
 *
 * The measurements are stubbed on a real element rather than on a component double, so the code
 * under test is the code that ships: it reads `scrollHeight` and `getComputedStyle` off a node.
 */

const ORIGIN = 'https://chat.noa.internal'

/**
 * A stand-in for `.card` / `.page`: a real node whose measured height this file controls.
 *
 * The four edge properties are zeroed explicitly, and that is not tidiness. jsdom resolves an
 * unset `border-top-width` to `16px` — the `medium` keyword — *even under `border-style: none`*,
 * where every browser computes `0px`. So a fixture that left them alone would be measuring a box
 * 32px taller than the one this stylesheet actually produces, and the border term below would be
 * asserted against a jsdom quirk instead of against CSS. The real number is the browser lane's to
 * measure; what this lane owns is that the term is added at all.
 */
function scrollContainer(height: () => number): HTMLElement {
  const element = document.createElement('div')
  element.style.borderTopWidth = '0px'
  element.style.borderBottomWidth = '0px'
  element.style.marginTop = '0px'
  element.style.marginBottom = '0px'
  Object.defineProperty(element, 'scrollHeight', { get: height, configurable: true })
  document.body.appendChild(element)
  return element
}

/** A stand-in for `.scroller`: fixed numbers, because what is asserted is that they are added. */
function nestedScroller(scrollHeight: number, clientHeight: number): HTMLElement {
  const element = document.createElement('div')
  Object.defineProperty(element, 'scrollHeight', { value: scrollHeight, configurable: true })
  Object.defineProperty(element, 'clientHeight', { value: clientHeight, configurable: true })
  document.body.appendChild(element)
  return element
}

/**
 * A fresh element every time, which `rerender` requires.
 *
 * React bails out of re-rendering a subtree handed the *same* element reference, so a stored JSX
 * value would make every re-render a no-op — and a re-measurement that never happened looks exactly
 * like one the epsilon suppressed. Measured: with a stored element, the epsilon spec below passed
 * while asserting nothing.
 */
function sizer(container: HTMLElement, targetOrigin: string | null = ORIGIN) {
  return (
    <FrameSizer
      container={{ current: container }}
      policy={CARD_FRAME_POLICY}
      targetOrigin={targetOrigin}
    />
  )
}

function sizerState(): string | null {
  return document.querySelector('[data-noa-frame-size]')?.getAttribute('data-noa-frame-size') ?? null
}

function postedHeights(spy: ReturnType<typeof vi.spyOn>): number[] {
  return spy.mock.calls.map(
    (call: unknown[]) => (call[0] as { payload: { height: number } }).payload.height,
  )
}

let post: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  // The host's own window in production. Spied rather than scanned for: a source scan passes just
  // as well against a component that never posts at all.
  post = vi.spyOn(window.parent, 'postMessage')
})

afterEach(() => {
  cleanup()
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('FrameSizer — the message', () => {
  it('posts once at mount, height only, to the resolved origin', () => {
    const container = scrollContainer(() => 480)

    render(
      <FrameSizer
        container={{ current: container }}
        policy={CARD_FRAME_POLICY}
        targetOrigin={ORIGIN}
      />,
    )

    expect(post).toHaveBeenCalledTimes(1)
    const [message, target] = post.mock.calls[0]!
    expect(message).toEqual({ type: 'ui-size-change', payload: { height: 480 } })
    // The origin, asserted on the call. Never `"*"`: the message is a fact about this operator's
    // card, and a wildcard hands it to whatever document happens to be framing us.
    expect(target).toBe(ORIGIN)
  })

  it('sends no width key, so the host keeps its own column width', () => {
    const container = scrollContainer(() => 480)

    render(
      <FrameSizer
        container={{ current: container }}
        policy={CARD_FRAME_POLICY}
        targetOrigin={ORIGIN}
      />,
    )

    const payload = (post.mock.calls[0]![0] as { payload: Record<string, unknown> }).payload
    expect(Object.keys(payload)).toEqual(['height'])
  })

  it('adds the border the measurement leaves out', () => {
    // Two pixels, and the reason they are worth a test: a card clipped by two pixels at the bottom
    // reads as a rendering bug rather than an arithmetic one.
    const container = scrollContainer(() => 480)
    container.style.borderTopWidth = '1px'
    container.style.borderBottomWidth = '1px'

    render(
      <FrameSizer
        container={{ current: container }}
        policy={CARD_FRAME_POLICY}
        targetOrigin={ORIGIN}
      />,
    )

    expect(postedHeights(post)).toEqual([482])
  })
})

describe('FrameSizer — when it speaks again', () => {
  it('stays quiet under the epsilon and speaks over it', () => {
    // The reachability control matters more than the gate: without the second half, this passes
    // against an implementation that posts once and never again for any reason at all.
    let height = 480
    const container = scrollContainer(() => height)

    const view = render(sizer(container))
    expect(postedHeights(post)).toEqual([480])

    height = 484
    view.rerender(sizer(container))
    expect(postedHeights(post)).toEqual([480])

    height = 500
    view.rerender(sizer(container))
    expect(postedHeights(post)).toEqual([480, 500])
  })

  it('never asks for a shorter frame within one mount', () => {
    // The oscillation's downstroke. The reserved scrollbar gutter in both stylesheets is what stops
    // the width from changing at all; this is the instrument that needs no tuning.
    let height = 480
    const container = scrollContainer(() => height)

    const view = render(sizer(container))
    height = 300
    view.rerender(sizer(container))
    height = 480
    view.rerender(sizer(container))

    expect(postedHeights(post)).toEqual([480])
  })

  it('spends at most the budget, and exhausting it is on the page', () => {
    // A frame that quietly stops asking is indistinguishable from a host that quietly stopped
    // listening, and those two send an operator to different people.
    let height = 200
    const container = scrollContainer(() => height)

    const view = render(sizer(container))
    expect(sizerState()).toBe('measuring')

    for (let tick = 0; tick < FRAME_POST_BUDGET + 4; tick += 1) {
      height += 50
      view.rerender(sizer(container))
    }

    expect(post.mock.calls).toHaveLength(FRAME_POST_BUDGET)
    expect(sizerState()).toBe('budget-exhausted')
  })
})

describe('FrameSizer — the table shape', () => {
  it('adds what a nested scroller is hiding, and bounds the result', () => {
    // 320px of page plus 1000px of rows the scroller is holding back: over the table's ceiling, so
    // what is asked for is the ceiling. This is the 438-row listing in miniature.
    const container = scrollContainer(() => 320)

    render(
      <FrameSizer
        container={{ current: container }}
        nested={{ current: nestedScroller(1200, 200) }}
        policy={TABLE_FRAME_POLICY}
        targetOrigin={ORIGIN}
      />,
    )

    expect(postedHeights(post)).toEqual([720])
  })

  it('asks for the page alone when there is no scroller to account for', () => {
    // The separating case for the one above: without it, "the hidden rows are added" would pass
    // against an implementation that always asked for the ceiling.
    const container = scrollContainer(() => 320)

    render(
      <FrameSizer
        container={{ current: container }}
        policy={TABLE_FRAME_POLICY}
        targetOrigin={ORIGIN}
      />,
    )

    expect(postedHeights(post)).toEqual([320])
  })
})

describe('FrameSizer — degrading', () => {
  it('posts nothing without a target origin, and names that state', () => {
    const container = scrollContainer(() => 480)

    render(
      <FrameSizer container={{ current: container }} policy={CARD_FRAME_POLICY} targetOrigin={null} />,
    )

    expect(post).not.toHaveBeenCalled()
    expect(sizerState()).toBe('no-target-origin')
  })

  it('a malformed origin variable ends in silence, not in a throw', () => {
    // End to end for the wrapper's whole reason: the resolver throws on this value on purpose, and
    // what an operator must not lose over it is the card.
    const targetOrigin = resolveFrameTargetOrigin({ [LIBRECHAT_ORIGIN_ENV_VAR]: '*' })
    expect(targetOrigin).toBeNull()

    const container = scrollContainer(() => 480)
    expect(() =>
      render(
        <FrameSizer
          container={{ current: container }}
          policy={CARD_FRAME_POLICY}
          targetOrigin={targetOrigin}
        />,
      ),
    ).not.toThrow()

    expect(post).not.toHaveBeenCalled()
  })

  it('posts nothing when there is no container to measure', () => {
    render(
      <FrameSizer container={{ current: null }} policy={CARD_FRAME_POLICY} targetOrigin={ORIGIN} />,
    )

    expect(post).not.toHaveBeenCalled()
  })

  it('renders one hidden element and nothing that can take part in layout', () => {
    // As a visible child of `.card` it would collect a flex `gap` and add space to the very
    // measurement it exists to report.
    const container = scrollContainer(() => 480)
    const { container: mounted } = render(
      <FrameSizer
        container={{ current: container }}
        policy={CARD_FRAME_POLICY}
        targetOrigin={ORIGIN}
      />,
    )

    const marker = mounted.querySelector('[data-noa-frame-size]')
    expect(marker?.tagName).toBe('SPAN')
    expect(marker?.hasAttribute('hidden')).toBe(true)
    expect(marker?.textContent).toBe('')
  })
})
