import type { ReactNode } from 'react'

import styles from './notice.module.css'

/**
 * What a frame shows when it cannot show its subject (§T.41, §T.43, §T.56 — V27, V38).
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
