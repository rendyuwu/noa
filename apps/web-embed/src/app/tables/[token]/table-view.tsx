'use client'

import { useState } from 'react'

import { Notice } from '@/components/notice'
import { SignInNotice } from '@/components/sign-in-notice'
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
 * **The bound is rendered, not merely stored** (V85). `describeBound` says how many rows matched and
 * how many this page holds, on every table — a sentence that only appeared when something was
 * dropped would be one a reader learns to skip, and the whole point is that a capped page can never
 * be mistaken for a complete one.
 *
 * **A client component, for one reason: the 401 state's retry** (§T.43's way out, V38, V42, V94).
 * The first read is the server's (`page.tsx` → `lib/tables/detail.ts`), so the HTML that reaches the
 * frame is already the authenticated page. What ships to the browser is the retry, and it is a
 * `fetch` — nothing here navigates the frame and nothing submits a form, because the sandbox
 * LibreChat applies omits `allow-forms` (V80, R13, R29).
 *
 * The retry goes through the app's own route rather than the API: this page is server-rendered, so
 * re-reading means re-rendering it, and `location.reload()` is the one navigation a framed document
 * can always do to itself. That keeps the proxy allowlist at the four entries §T.44 pinned — a
 * table needs no browser-reachable API route at all.
 */

function TableRows({ table }: { table: ResultTable }) {
  return (
    <div className={styles.scroller}>
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

function Table({ table }: { table: ResultTable }) {
  return (
    <main className={styles.page}>
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

      <TableRows table={table} />

      <p className={styles.footer}>
        This page expires at {table.expiresAt}. Run the tool again to produce a new one.
      </p>
    </main>
  )
}

export function TableView({
  initial,
  signInUrl,
}: {
  initial: ResultTableLoad
  /** Where an operator signs in, or `null` when nothing usable is configured (`lib/sign-in.ts`). */
  signInUrl: string | null
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
    return <Table table={load.table} />
  }

  if (load.kind === 'unauthenticated') {
    // V38, V42, V94: an explicit state with a way out — a top-level link-out *and* the same address
    // as text, because the render site whose sandbox omits `allow-popups` opens neither silently.
    return <SignInNotice signInUrl={signInUrl} onRetry={retryRead} />
  }

  if (load.kind === 'not-found') {
    // One sentence for all four causes the API answers alike: unknown, another operator's, one
    // whose requester was deleted, and one past its deadline (V27).
    return (
      <Notice
        title="Table not available"
        body="This table does not exist, it is not yours to read, or it has expired. Run the tool again to produce a new one."
      />
    )
  }

  return (
    <Notice
      title="Could not load this table"
      body="NOA could not be reached, or answered unexpectedly. Reload this page; contact an administrator if it continues."
    />
  )
}
