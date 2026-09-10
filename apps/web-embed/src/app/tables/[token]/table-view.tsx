'use client'

import { type RefObject, useRef, useState } from 'react'

import { FrameSizer } from '@/components/frame-sizer'
import { Notice } from '@/components/notice'
import { SignInNotice } from '@/components/sign-in-notice'
import { TABLE_FRAME_POLICY } from '@/lib/embed/frame-size'
import { type ResultTable, type ResultTableLoad, cellText, describeBound } from '@/lib/tables/table'

import styles from './table.module.css'

/**
 * The large READ's rows, on NOA's own origin (§T.56 — V27, V38, V64, V85, V94).
 *
 * **No decision controls, and nowhere to put one** (§I.embed). This page renders a listing a READ
 * already produced: there is no approve, no deny, no reason box, no `<form>` and no CSRF token,
 * because there is nothing here to authorise. The approval card is the surface that decides; a
 * table is the surface that shows.
 *
 * **The bound is rendered, not merely stored**. `describeBound` says how many rows matched and
 * how many this page holds, on every table — a sentence that only appeared when something was
 * dropped would be one a reader learns to skip, and the whole point is that a capped page can never
 * be mistaken for a complete one.
 *
 * **It asks the host for a frame it fits in** (`components/frame-sizer.tsx`). The box LibreChat
 * opens with is small and fixed, and a listing is long — so the page measures itself and posts a
 * bounded height, which the host applies verbatim (measured; there is no host-side clamp). Bounded
 * because a 438-row listing's natural height is on the order of 15,000px and a frame that tall
 * inside a chat conversation is as unusable as one 150px tall; the ceiling and its reasoning are in
 * `lib/embed/frame-size.ts`. Below it `.scroller` keeps scrolling internally, which is why the
 * stylesheet's floor on that element and this measurement are one change and not two: a floor
 * without a taller frame pushes the expiry line below `.page`'s cut, and answering "no rows" with
 * "no expiry" is not a fix.
 *
 * **A client component for two reasons: the 401 state's retry** (§T.43's way out, V38, V42, V94)
 * **and the measurement above.**
 * The first read is the server's (`page.tsx` → `lib/tables/detail.ts`), so the HTML that reaches the
 * frame is already the authenticated page. What ships to the browser is the retry, and it is a
 * `fetch` — nothing here navigates the frame and nothing submits a form, because the sandbox
 * LibreChat applies omits `allow-forms`.
 *
 * The retry goes through the app's own route rather than the API: this page is server-rendered, so
 * re-reading means re-rendering it, and `location.reload()` is the one navigation a framed document
 * can always do to itself. That keeps the proxy allowlist at the four entries §T.44 pinned — a
 * table needs no browser-reachable API route at all.
 */

function TableRows({
  table,
  scrollerRef,
}: {
  table: ResultTable
  /**
   * The rows' own scroller, handed up to the frame sizer.
   *
   * `.page` is the flex container and `.scroller` is the child flex shrinks, so the page's own
   * height counts this box rather than the rows inside it — the sizer adds back what this element
   * is hiding (`nestedOverflow`). A ref rather than a query for the class name: the CSS module's
   * generated name is not an identifier this app should be reading back out of the DOM.
   */
  scrollerRef: RefObject<HTMLDivElement | null>
}) {
  return (
    <div className={styles.scroller} ref={scrollerRef}>
      {table.rows.length === 0 ? (
        // A table that matched nothing is a real answer — an empty page with headings would read
        // as a listing that failed to load (V38's family).
        <p className={styles.empty}>This read matched no rows.</p>
      ) : (
        <table className={styles.table}>
          <thead>
            <tr>
              {table.columns.map((column) => (
                <th key={column.key} scope="col">
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, index) => (
              // Index keys: rows carry no identifier of their own — the columns are whatever the
              // remote system returned — and the list never reorders, because a parked table is
              // written once and read.
              <tr key={index}>
                {table.columns.map((column) => (
                  <td key={column.key}>{cellText(row[column.key])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function Table({ table, frameOrigin }: { table: ResultTable; frameOrigin: string | null }) {
  const page = useRef<HTMLElement>(null)
  const scroller = useRef<HTMLDivElement>(null)

  return (
    <>
      <main className={styles.page} ref={page}>
        <header className={styles.header}>
          <h1 className={styles.tool}>{table.toolName}</h1>
          <p className={styles.bound}>
            {table.truncated ? (
              <span className={styles.truncated}>{describeBound(table)}</span>
            ) : (
              describeBound(table)
            )}
          </p>
        </header>

        <TableRows table={table} scrollerRef={scroller} />

        <p className={styles.footer}>
          This page expires at {table.expiresAt}. Run the tool again to produce a new one.
        </p>
      </main>
      {/* Outside `main` deliberately: a sizer that was a flex child of the box it measures would
          collect a `gap` of its own and change the number it reports. */}
      <FrameSizer
        container={page}
        nested={scroller}
        policy={TABLE_FRAME_POLICY}
        targetOrigin={frameOrigin}
      />
    </>
  )
}

export function TableView({
  initial,
  signInUrl,
  frameOrigin,
}: {
  initial: ResultTableLoad
  /** Where an operator signs in, or `null` when nothing usable is configured (`lib/sign-in.ts`). */
  signInUrl: string | null
  /**
   * The origin the host frames this page from, or `null` when there is not a trustworthy one
   * (`lib/embed/frame-origin.ts`). `null` means the frame is left at whatever height the host gave
   * it, and this page scrolls itself the way it does today.
   */
  frameOrigin: string | null
}) {
  const [load] = useState<ResultTableLoad>(initial)

  /**
   * One retry, from the state that has nothing to show (§T.43's shape, V42).
   *
   * A reload rather than a re-fetch: the read is the server component's, so the way to repeat it is
   * to re-render the page. The frame stays on its own URL — this navigates the document to itself,
   * never to a login page, which is the absence §T.43 bound as an absence.
   *
   * The answer is unknowable from here (the reload replaces this document), so it reports
   * `unavailable` if the reload has not happened by the time the promise resolves. Saying nothing
   * would leave the button looking broken in the one case where it is not.
   */
  async function retryRead(): Promise<{ kind: string }> {
    if (typeof window !== 'undefined') window.location.reload()
    return { kind: 'unavailable' }
  }

  if (load.kind === 'table') {
    return <Table table={load.table} frameOrigin={frameOrigin} />
  }

  // Every state below is a notice, and each one carries `frameOrigin` for the same reason the table
  // above does: it measures itself and asks the host for a frame it fits in. The 401 is the one that
  // needs it — its printed address is the escape hatch V94 makes the rule, and at the box the host
  // opens with that address started below the fold (`components/notice.tsx`).
  if (load.kind === 'unauthenticated') {
    // V38, V42, V94: an explicit state with a way out — a top-level link-out *and* the same address
    // as text, because the render site whose sandbox omits `allow-popups` opens neither silently.
    return <SignInNotice signInUrl={signInUrl} onRetry={retryRead} frameOrigin={frameOrigin} />
  }

  if (load.kind === 'not-found') {
    // One sentence for all four causes the API answers alike: unknown, another operator's, one
    // whose requester was deleted, and one past its deadline.
    return (
      <Notice
        title="Table not available"
        body="This table does not exist, it is not yours to read, or it has expired. Run the tool again to produce a new one."
        frameOrigin={frameOrigin}
      />
    )
  }

  return (
    <Notice
      title="Could not load this table"
      body="NOA could not be reached, or answered unexpectedly. Reload this page; contact an administrator if it continues."
      frameOrigin={frameOrigin}
    />
  )
}
