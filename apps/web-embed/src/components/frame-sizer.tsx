'use client'

import { type RefObject, useEffect, useRef, useState } from 'react'

import {
  FRAME_POST_BUDGET,
  type FrameSizePolicy,
  type FrameSizeState,
  boxEdgeHeight,
  frameSizeMessage,
  nestedOverflow,
  nextFrameHeight,
} from '@/lib/embed/frame-size'

/**
 * Asks the host to make the frame as tall as the document inside it.
 *
 * One component for both surfaces, because both ask the same question of the same host and the
 * difference between them is a policy object (`lib/embed/frame-size.ts`). Two copies would be two
 * places for the loop-prevention rules to stop matching each other.
 *
 * **When to measure and what to measure are different questions.**
 *
 * *When.* A `ResizeObserver` on the scroll container alone would **never fire** in the state that
 * matters: `.card` and `.page` are pinned at `max-height: 100dvh`, so once the content is taller
 * than the frame it can grow without the container's box changing at all — no box change, no
 * callback. So the observer goes on the container *and* on each of its element children, whose
 * boxes do track their content (their flex `min-height: auto` resolves to their content minimum,
 * because their own `overflow` is `visible`, so they do not shrink). The subscription is rebuilt on
 * every render, which is also how a child that appears later — the Execution block, the receipt —
 * comes to be watched: React re-renders when the content changes, and this effect has no dependency
 * array for exactly that reason.
 *
 * *What.* The browser's own layout, not arithmetic this app maintains: `scrollHeight` off the scroll
 * container. On the clamped card that number is already correct, for the same reason the children do
 * not shrink. The table needs one addition, and it is named and tested rather than inlined —
 * see `nestedOverflow`.
 *
 * **Nothing is posted without a resolved target origin** (`lib/embed/frame-origin.ts`). `null` is a
 * supported state, not a failure: both surfaces still scroll themselves, so what an operator loses
 * is a taller frame and not the surface.
 *
 * **Renders a `hidden` element and nothing else.** `hidden` resolves to `display: none`, which keeps
 * it out of flex layout entirely — as a visible child of `.card` it would collect a `gap` and add
 * space to the very measurement it exists to report. The attribute is diagnostic, and it is on the
 * page rather than in the console because exhausting the post budget has to be findable.
 */
export function FrameSizer({
  container,
  nested,
  policy,
  targetOrigin,
}: {
  /** The scroll container: `.card` on the approval surface, `.page` on the table. */
  container: RefObject<HTMLElement | null>
  /** A scroller nested inside it, whose hidden rows the container's own height leaves out. */
  nested?: RefObject<HTMLElement | null>
  policy: FrameSizePolicy
  /** Resolved by the page from the server's environment. `null` disables sizing entirely. */
  targetOrigin: string | null
}) {
  /*
   * The last height posted, and how many posts this mount has spent. Refs rather than state on
   * purpose: state would re-render on every post, the effect below has no dependency array, and
   * re-arming the observer from inside its own callback is the loop shape this component avoids.
   */
  const lastPosted = useRef<number | null>(null)
  const posts = useRef(0)
  const [exhausted, setExhausted] = useState(false)

  useEffect(() => {
    const measured = container.current
    if (targetOrigin === null || measured === null) return

    // Re-bound as non-nullable locals: the callback below outlives the narrowing above, and a
    // `!` inside it would be an assertion about a ref another render is free to have cleared.
    const element = measured
    const origin = targetOrigin

    function measure(): void {
      if (posts.current >= FRAME_POST_BUDGET) {
        setExhausted(true)
        return
      }

      const scroller = nested?.current ?? null
      const measured =
        element.scrollHeight +
        (scroller === null ? 0 : nestedOverflow(scroller.scrollHeight, scroller.clientHeight))

      const height = nextFrameHeight({
        measured,
        edges: boxEdgeHeight(window.getComputedStyle(element)),
        lastPosted: lastPosted.current,
        policy,
      })
      if (height === null) return

      lastPosted.current = height
      posts.current += 1
      window.parent.postMessage(frameSizeMessage(height), origin)
    }

    measure()

    // Absent under jsdom, so the component lane asserts the arithmetic and the message and leaves
    // every layout claim to `e2e/`. A guard rather than a polyfill: a fake observer here would be a
    // second layout engine to keep true.
    if (typeof ResizeObserver === 'undefined') return

    const observer = new ResizeObserver(measure)
    observer.observe(element)
    for (const child of Array.from(element.children)) observer.observe(child)

    return () => observer.disconnect()
  })

  const state: FrameSizeState =
    targetOrigin === null ? 'no-target-origin' : exhausted ? 'budget-exhausted' : 'measuring'

  return <span hidden data-noa-frame-size={state} />
}
