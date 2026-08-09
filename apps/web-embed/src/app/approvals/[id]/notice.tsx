import type { ReactNode } from 'react'

import styles from './card.module.css'

/**
 * What the frame shows when it cannot show a card (§T.41, §T.43 — V27, V38).
 *
 * Three states reach it — the session NOA does not recognise, the request that is not this
 * operator's to decide, and the read that could not be made at all — and every one of them is a
 * *state*, never a blank frame with an Approve button standing on nothing (V38). Lifted into its own
 * module when §T.43 gave the 401 case buttons of its own: the alternative was `card-view.tsx`
 * exporting to `sign-in-notice.tsx` while importing it back (V66 without the cycle).
 *
 * `children` is where a state puts its way out. There is nothing generic about it — two of the three
 * have none, and the one that does is `sign-in-notice.tsx`.
 */
export function Notice({
  title,
  body,
  children,
}: {
  title: string
  body: string
  children?: ReactNode
}) {
  return (
    <main className={styles.notice}>
      <h1 className={styles.noticeTitle}>{title}</h1>
      <p className={styles.noticeBody}>{body}</p>
      {children}
    </main>
  )
}
