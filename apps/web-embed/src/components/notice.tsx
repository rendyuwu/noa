'use client'

import { type ReactNode, useRef } from 'react'

import { CARD_FRAME_POLICY } from '@/lib/embed/frame-size'

import { FrameSizer } from './frame-sizer'
import styles from './notice.module.css'

/**
 * What a frame shows when it cannot show its subject (§T.41, §T.43, §T.56 — V27, V38, V94).
 *
 * Four states reach it — the session NOA does not recognise, the request or table that is not this
 * operator's, and the read that could not be made at all — and every one of them is a *state*,
 * never a blank frame with a control standing on nothing (V38).
 *
 * Lifted out of `app/approvals/[id]/` at §T.56, when the table surface became the second document
 * that renders these states. Two copies of "cannot authenticate here" is two places for the way out
 * to go missing from one of them (V66, V94).
 *
 * `children` is where a state puts its way out. There is nothing generic about it — most have none,
 * and the one that does is `sign-in-notice.tsx`.
 *
 * **It asks the host for a frame it fits in, like the two subjects it stands in for.** The state
 * with the strongest claim on that is the 401: what it hands the operator is a printed address, and
 * the printed address is the escape hatch itself rather than a convenience beside the link — the
 * sandbox at one of LibreChat's two render sites omits `allow-popups`, so there the link opens
 * nothing at all and the text is the only door (V94, R32). Measured before this, at the 150px box
 * `@mcp-ui/client` opens with: that address began 4.5px below the fold and ended 30px past it. It
 * was never unreachable — `.notice` is `max-height: 100dvh; overflow-y: auto` and scrolls — but a
 * document that can ask for a frame it fits in and does not ask is one that leaves its own way out
 * off screen.
 *
 * **The sizer lives here rather than at each render site.** There are six of those across the two
 * views, all of them one shape, and the argument for keeping the notice in one file (above) is the
 * argument for keeping its measurement in one file too. `frameOrigin` is required for the same
 * reason: a caller that omitted it would get a notice that silently never sizes, and a compile error
 * is the only thing that survives the next rewrite of those views.
 */
export function Notice({
  title,
  body,
  frameOrigin,
  children,
}: {
  title: string
  body: string
  /**
   * The origin the host frames this document from, or `null` when there is not a trustworthy one
   * (`lib/embed/frame-origin.ts`). `null` disables sizing and changes nothing else: the notice still
   * scrolls itself, which is what it did before it measured anything.
   */
  frameOrigin: string | null
  children?: ReactNode
}) {
  const container = useRef<HTMLElement>(null)

  return (
    <>
      <main className={styles.notice} ref={container}>
        <h1 className={styles.noticeTitle}>{title}</h1>
        <p className={styles.noticeBody}>{body}</p>
        {children}
      </main>
      {/* Outside `main` deliberately: a sizer that was a flex child of the box it measures would
          collect a `gap` of its own and change the number it reports. */}
      <FrameSizer container={container} policy={CARD_FRAME_POLICY} targetOrigin={frameOrigin} />
    </>
  )
}
