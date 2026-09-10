/**
 * The height this document asks its host for, and every bound on that number (the card page and the self-sizing).
 *
 * **Pure arithmetic, no DOM.** `components/frame-sizer.tsx` owns the measuring and the posting;
 * everything that can be got wrong about the *number* lives here, where it can be tested without a
 * layout engine. jsdom computes no layout at all — no `max-height`, no flex shrink, no `dvh`, and
 * `getBoundingClientRect()` returns zeros — so a rule that only existed inside the component could
 * be asserted nowhere but the browser lane.
 *
 * **Why this exists.** LibreChat renders both of NOA's embed surfaces through `@mcp-ui/client`,
 * which starts the frame at a height of its own and then applies whatever a framed document asks
 * for in a `ui-size-change` message. Measured against the pinned client at `45cc53c4`
 * (`spikes/librechat-embed-render-gate/resize_probe.mjs`, evidence under that directory's
 * `evidence/`): the number is applied **verbatim**, and 20000px was honoured. There is no
 * host-side clamp, so every bound below is ours to hold. A bad number is this app's bug, and the
 * host will not save us from it.
 *
 * **Degrading is the design, not the fallback.** Both surfaces still scroll themselves — the
 * link-out and the printed address stay put either way. If a LibreChat bump
 * stops honouring the message, or the target origin cannot be trusted, nothing here posts and the
 * surface is exactly what it is today — never a dead box.
 */

/** The message type `@mcp-ui/client` listens for. Its name, not ours: renaming it is a no-op. */
export const FRAME_SIZE_MESSAGE_TYPE = 'ui-size-change'

export type FrameSizeMessage = {
  type: typeof FRAME_SIZE_MESSAGE_TYPE
  payload: { height: number }
}

/**
 * **Height only, and no `width` key at all.**
 *
 * A payload that omits `width` leaves the host's own `width: 100%` untouched — measured at the same
 * pin as everything else here. One that carries a width would fight the host for the layout of its
 * message column, which is the host's to decide and not a framed document's.
 */
export function frameSizeMessage(height: number): FrameSizeMessage {
  return { type: FRAME_SIZE_MESSAGE_TYPE, payload: { height } }
}

/** How tall a surface may ask to be, and what counts as a change worth a message. */
export type FrameSizePolicy = {
  /** A measurement below this posts *this* instead. */
  floor: number
  /** A measurement above this posts *this* instead — never nothing. See `nextFrameHeight`. */
  ceiling: number
  /** A change smaller than this is not worth a message. */
  epsilon: number
}

/**
 * The smallest frame either surface ever asks for.
 *
 * Two states share it. One is a real page that is genuinely short — a two-row table, a decided
 * card. The other is a measurement taken before the layout it describes exists, which reads as a
 * height near zero; the floor is what keeps that from being posted as one. 160px is the order of
 * the box `@mcp-ui/client` opens with, so the worst this can do is ask for what the host would have
 * given anyway, and the next measurement grows it.
 */
const FRAME_HEIGHT_FLOOR = 160

/**
 * The change below which nothing is posted.
 *
 * Deliberately smaller than one line box (about 20px at 0.8125rem on this stylesheet's 1.5
 * line-height): an epsilon large enough to damp a real oscillation is large enough to leave a card
 * systematically short of its own content, which is the requirement this module exists to serve.
 * So this filters device-pixel rounding and nothing else. What actually kills the loop is stated on
 * `nextFrameHeight` and in the two stylesheets' `scrollbar-gutter` lines.
 */
const FRAME_HEIGHT_EPSILON = 8

/**
 * The approval card asks for its content height; the ceiling is a rail, not an operating point.
 *
 * This is the surface that has to be worth one screenshot — one shot, no scrolling, legible at the
 * frame's width — so the normal answer is "as tall as the card is" and nothing shorter. What the
 * rail stops is a pathological payload: a CHANGE whose `arguments` or before-state carries hundreds
 * of keys would otherwise render a frame taller than the conversation holding it. 4800px is around
 * four screens; past it the card's own `max-height: 100dvh` and `overflow-y: auto` resume scrolling,
 * which is where this whole module degrades to anyway.
 */
export const CARD_FRAME_POLICY: FrameSizePolicy = {
  floor: FRAME_HEIGHT_FLOOR,
  ceiling: 4800,
  epsilon: FRAME_HEIGHT_EPSILON,
}

/**
 * The table asks for `min(content, 720px)` and keeps scrolling internally below it.
 *
 * **Why a bound at all, when the card gets a rail it rarely meets.** The complaint this work
 * answers arrived on a 438-row listing. Rows in `app/tables/[token]/table.module.css` are 0.8125rem
 * type on a 1.5 line-height with `--noa-space-2` (0.5rem) of vertical padding and a 1px rule —
 * about 36px each — so 438 of them is on the order of 15,800px, and the host applies that verbatim.
 * A 15,800px iframe inside a chat conversation swaps one unusable surface for another, and it
 * defeats `.scroller`, whose reason for existing is stated at the top of that stylesheet.
 *
 * **Why 720.** At ~36px a row that is roughly eighteen rows visible at once, plus the tool name and
 * the bound line — about one laptop screen inside a chat column, with the sticky column
 * headings staying in view while the rest scrolls.
 *
 * A bounded request also unhooks the number from flex resolution in the common case: any listing
 * long enough for the scroller to matter measures past the ceiling, so what is posted is the
 * ceiling however the flex layout resolved on the way there.
 */
export const TABLE_FRAME_POLICY: FrameSizePolicy = {
  floor: FRAME_HEIGHT_FLOOR,
  ceiling: 720,
  epsilon: FRAME_HEIGHT_EPSILON,
}

/**
 * How many messages one mount may send, ever.
 *
 * A backstop behind the two controls that actually prevent a loop — the reserved scrollbar gutter
 * in both stylesheets, and the monotonic rule in `nextFrameHeight` — because those two are
 * arguments and this is a number. The legitimate causes of a new height inside one mount are
 * countable: the first measurement, each poll answer that adds a section (the Execution block, the
 * receipt, the stalled note), a status transition, and the swap to the 401 state. Under a dozen —
 * measured on a full polling-card lifecycle, STARTED through COMPLETED with its receipt over three
 * polls, which spends two. Twelve leaves headroom over that and still stops a feedback loop inside a
 * second.
 *
 * No web font is on that list, because this app ships none: `app/globals.css` is system stacks in
 * both families, so there is no font swap to reflow anything.
 *
 * Exhausting it is an **observable state** and never a silent stall: `frame-sizer.tsx` renders it,
 * because a frame that quietly stopped asking is indistinguishable from a host that quietly stopped
 * listening, and those two send an operator to different people.
 */
export const FRAME_POST_BUDGET = 12

/** What `frame-sizer.tsx` is doing, rendered as an attribute so both test lanes can read it. */
export type FrameSizeState = 'measuring' | 'budget-exhausted' | 'no-target-origin'

/** The `getComputedStyle` fields `boxEdgeHeight` reads. A `CSSStyleDeclaration` satisfies it. */
export type BoxEdges = {
  borderTopWidth: string
  borderBottomWidth: string
  marginTop: string
  marginBottom: string
}

/** `"12px"` → `12`. Anything unparseable — `""`, `"auto"`, a keyword — is 0, never `NaN`. */
export function cssPixels(value: string | null | undefined): number {
  const parsed = Number.parseFloat(value ?? '')
  return Number.isFinite(parsed) ? parsed : 0
}

/**
 * The one named term for what a `scrollHeight` measurement leaves out.
 *
 * `scrollHeight` includes the element's padding and its overflowing content, and excludes its
 * border and its margin; `ResizeObserver`'s default observation box is `content-box`, which
 * excludes the padding too. Neither number is the height a frame needs, and the difference is
 * exactly this: the border and the margin of the element being measured.
 *
 * Today the term is zero — `.card` and `.page` carry padding but no border, and `body` has no
 * margin (`app/globals.css`). It is computed rather than assumed because a stylesheet edit adding a
 * 1px border would otherwise clip the bottom of the card by two pixels, which reads as a rendering
 * bug rather than an arithmetic one and is the kind of defect nobody finds for a month.
 */
export function boxEdgeHeight(edges: BoxEdges): number {
  return (
    cssPixels(edges.borderTopWidth) +
    cssPixels(edges.borderBottomWidth) +
    cssPixels(edges.marginTop) +
    cssPixels(edges.marginBottom)
  )
}

/**
 * Content a nested scroller is holding beyond its own box.
 *
 * The table surface needs this and the card does not. `.page` *is* the flex container, so there is
 * no wrapper to observe, its children are shrunk rather than clipped, and `.scroller` answers
 * `overflow: auto` — so `page.scrollHeight` counts the scroller's *box* at whatever height flex
 * resolution gave it, not the rows inside it. Adding back exactly what the scroller is hiding is
 * what turns that into the page's natural total.
 *
 * Never negative: a scroller with nothing hidden contributes nothing.
 */
export function nestedOverflow(scrollHeight: number, clientHeight: number): number {
  const hidden = scrollHeight - clientHeight
  return Number.isFinite(hidden) && hidden > 0 ? hidden : 0
}

export type FrameSizeInput = {
  /** The scroll container's `scrollHeight`, plus any `nestedOverflow` beneath it. */
  measured: number
  /** `boxEdgeHeight` of the measured element. */
  edges: number
  /** The height this mount last posted, or `null` before it has posted anything. */
  lastPosted: number | null
  policy: FrameSizePolicy
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(Math.max(value, low), high)
}

/**
 * The height to post, or `null` for "no change worth reporting".
 *
 * **One bound rule, stated once.** Below the epsilon, post nothing. Below the floor, post the
 * floor. Above the ceiling, post **the ceiling** — never `null`. That last branch is the one worth
 * spelling out: returning `null` for an over-ceiling measurement would leave a pathological payload
 * sitting in the host's opening box, which is the defect this module was written to fix. `null`
 * means "nothing to say", and it never means "the number was large".
 *
 * **Monotonic within a mount, and that is one of the two things that kill the oscillation.** The
 * loop is real and it is scrollbar-mediated: a short frame overflows, the scrollbar appears, the
 * content box narrows by ~15px, text wraps onto more lines, the measurement grows, the frame grows,
 * the overflow goes away, *the scrollbar is removed*, the box widens, wrapped lines fit again, the
 * measurement drops, and the overflow returns. The amplitude is at least a line box and more when a
 * long monospace value un-wraps — `.factValue` is exactly that content. The primary fix is a
 * reserved scrollbar gutter in both stylesheets, which removes the width term from that chain
 * instead of damping it. This is the second: a decrease is never posted. It falls out of the same
 * comparison the epsilon uses, because a decrease is never a change of `+epsilon` or more.
 *
 * Two instruments rather than one because `scrollbar-gutter` is not universal — a browser that
 * ignores the declaration keeps the width term, and this rule is what holds there.
 *
 * A content change that genuinely shortens the page (a new poll result, a status transition) is
 * left to the frame it already has: too tall a frame shows whitespace, and too short a one hides
 * the Approve button.
 *
 * A non-finite measurement posts nothing. That is not a bound decision — it is the absence of a
 * measurement, and the floor is for heights that were measured.
 */
export function nextFrameHeight({
  measured,
  edges,
  lastPosted,
  policy,
}: FrameSizeInput): number | null {
  if (!Number.isFinite(measured) || !Number.isFinite(edges)) return null

  const wanted = Math.round(clamp(measured + edges, policy.floor, policy.ceiling))
  if (lastPosted !== null && wanted - lastPosted < policy.epsilon) return null

  return wanted
}
