import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ResultTable, ResultTableLoad } from '@/lib/tables/table'

import { TableView } from './table-view'

/**
 * What the table surface renders, and what it must never render (§T.56 — V27, V38, V64, V85).
 *
 * **The absences are the point, and they are asserted by name.** §I.embed says this surface is
 * read-only with no decision controls: no Approve, no Deny, no reason box, no `<form>`, no `input`.
 * By name rather than by counting controls, for §T.43's reason — the 401 state adds two controls of
 * its own that are *not* decisions, so a `getByRole('button')` count would go red for the right
 * rule spelled wrongly.
 *
 * **The bound is rendered on every table**, not only on a capped one. A sentence that appeared
 * only when rows were dropped is one a reader learns to skip, and the failure this guards is a
 * capped page read as a complete one.
 *
 * The 401 state is `SignInNotice`, shared with the approval card since this row — its own
 * lane (`src/components/sign-in-notice.test.tsx`) owns the link-plus-address rule V94 names; what
 * is asserted here is that this surface reaches it at all.
 */

const TABLE: ResultTable = {
  token: 'table-token-1',
  toolName: 'whm_list_accounts',
  columns: [
    { key: 'user', label: 'Account' },
    { key: 'domain', label: 'Primary domain' },
  ],
  rows: [
    { user: 'acmeco', domain: 'acme.example' },
    { user: 'betaco', domain: 'beta.example' },
  ],
  totalRows: 2,
  storedRows: 2,
  truncated: false,
  createdAt: '2026-08-09T09:00:00+00:00',
  expiresAt: '2026-08-10T09:00:00+00:00',
}

function renderView(
  load: ResultTableLoad,
  signInUrl: string | null = 'https://noa.test/sign-in',
  // No frame origin by default, which switches the frame sizer off: these specs are about what the
  // surface renders, and the sizer's rules have their own lane
  // (`src/components/frame-sizer.test.tsx`). One spec below passes an origin, because "this page
  // mounts a sizer at all" is a claim about this file's subject.
  frameOrigin: string | null = null,
) {
  return render(<TableView initial={load} signInUrl={signInUrl} frameOrigin={frameOrigin} />)
}

function tableLoad(overrides: Partial<ResultTable> = {}): ResultTableLoad {
  return { kind: 'table', table: { ...TABLE, ...overrides } }
}

afterEach(cleanup)

describe('the table', () => {
  it('renders every row under its own heading', () => {
    renderView(tableLoad())

    expect(screen.getByRole('columnheader', { name: 'Account' })).toBeDefined()
    expect(screen.getByRole('columnheader', { name: 'Primary domain' })).toBeDefined()
    expect(screen.getByRole('cell', { name: 'acmeco' })).toBeDefined()
    expect(screen.getByRole('cell', { name: 'beta.example' })).toBeDefined()
    // Header row plus one per record: the listing is whole, which is what V64 offloads it for.
    expect(screen.getAllByRole('row')).toHaveLength(3)
  })

  it('names the tool the rows came from', () => {
    renderView(tableLoad())

    expect(screen.getByRole('heading', { name: 'whm_list_accounts' })).toBeDefined()
  })

  it('states the bound when the page is capped', () => {
    renderView(tableLoad({ totalRows: 1240, storedRows: 25, truncated: true, rows: TABLE.rows }))

    expect(screen.getByText(/1,240 rows matched/)).toBeDefined()
    expect(screen.getByText(/first 25/)).toBeDefined()
  })

  it('states the total on an uncapped page too', () => {
    // The negative control: without it, "a capped page says so" would pass against a page
    // that says it always — and a warning that is always on is one nobody reads.
    renderView(tableLoad())

    expect(screen.getByText(/2 rows, all of them shown/)).toBeDefined()
    expect(screen.queryByText(/narrow the search/)).toBeNull()
  })

  it('renders a table that matched nothing as an answer, not as a failure', () => {
    renderView(tableLoad({ rows: [], totalRows: 0, storedRows: 0 }))

    expect(screen.getByText(/matched no rows/)).toBeDefined()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('leaves a cell empty when a row lacks that column', () => {
    renderView(tableLoad({ rows: [{ user: 'acmeco' }], totalRows: 1, storedRows: 1 }))

    const cells = screen.getAllByRole('cell')
    expect(cells).toHaveLength(2)
    expect(cells[1]?.textContent).toBe('')
  })

  it('says when the page expires', () => {
    // Read off the rendered text rather than through a text matcher: the sentence is assembled
    // from three nodes, and a matcher that only sees one of them would pass on a page that had
    // dropped the deadline itself.
    const { container } = renderView(tableLoad())

    expect(container.textContent).toContain(TABLE.expiresAt)
  })
})

describe('what a table surface never carries', () => {
  it('offers no decision control of any kind (§I.embed)', () => {
    renderView(tableLoad())

    // By name, not by count. A table has nothing to authorise; the approval card is the surface
    // that decides, and a control here would be one that could only ever 409.
    expect(screen.queryByRole('button', { name: /approve/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /deny/i })).toBeNull()
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('contains no form and no input (V80, §I.embed)', () => {
    const { container } = renderView(tableLoad())

    expect(container.querySelector('form')).toBeNull()
    expect(container.querySelector('input')).toBeNull()
    expect(container.querySelector('textarea')).toBeNull()
  })

  it('renders no link into the frame beyond the sign-in state', () => {
    // Nothing on a rendered table navigates: the frame stays on the URL it was given, and the one
    // anchor this app renders belongs to the 401 state (§T.43, V42).
    const { container } = renderView(tableLoad())

    expect(container.querySelector('a')).toBeNull()
  })
})

describe('the states that are not a table', () => {
  it('renders the sign-in way out on a 401', () => {
    renderView({ kind: 'unauthenticated' })

    expect(screen.getByRole('heading', { name: /cannot authenticate here/i })).toBeDefined()
    expect(screen.getByRole('link', { name: /sign in to noa/i })).toBeDefined()
    // The same address as text beside the link: the render site whose sandbox omits `allow-popups`
    // opens the link silently and this is the door left.
    expect(screen.getByText('https://noa.test/sign-in')).toBeDefined()
  })

  it('renders no link at all when no sign-in address is configured', () => {
    // A door to a host nobody deployed reads as an action that was refused (§T.43(b)).
    renderView({ kind: 'unauthenticated' }, null)

    expect(screen.queryByRole('link')).toBeNull()
    expect(screen.getByRole('button', { name: /try again/i })).toBeDefined()
  })

  it('answers all four not-found causes with one sentence', () => {
    renderView({ kind: 'not-found' })

    expect(screen.getByRole('heading', { name: /table not available/i })).toBeDefined()
    expect(screen.getByText(/not yours to read, or it has expired/i)).toBeDefined()
  })

  it('separates "could not be reached" from "does not exist"', () => {
    renderView({ kind: 'unavailable', status: 503 })

    expect(screen.getByRole('heading', { name: /could not load this table/i })).toBeDefined()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('asks the host for a frame the listing fits in', () => {
    // The wiring, and only the wiring: that this surface mounts a sizer and that a height leaves
    // the document. The bound on that number, and the rows the scroller hides that go into it, are
    // the sizer's own rules (`src/components/frame-sizer.test.tsx`); jsdom computes no layout, so
    // there is nothing here for a height assertion to be about.
    const post = vi.spyOn(window.parent, 'postMessage')
    renderView(tableLoad(), null, 'https://chat.noa.internal')

    expect(post).toHaveBeenCalledTimes(1)
    const [message, target] = post.mock.calls[0]!
    expect((message as { type: string }).type).toBe('ui-size-change')
    expect(target).toBe('https://chat.noa.internal')

    post.mockRestore()
  })
})
