import { describe, expect, it } from 'vitest'

import { cellText, describeBound, parseResultTable } from './table'

/**
 * Parsing a parked table, and the one number it may never invent (§T.56 — V38, V85).
 *
 * Two rules pull in opposite directions here and both are deliberate. Parsing is **permissive**:
 * a missing label falls back to the key, a row that is not an object is dropped, and nothing
 * throws — a page that throws is a blank iframe, which V38 refuses. What is **fail-closed** is the
 * bound: `truncated` may be inferred from the counts but never cleared by their absence, because
 * the direction that must not fail open is the one where a capped table looks complete.
 */

const BODY = {
  token: 'table-token-1',
  tool_name: 'whm_list_accounts',
  columns: [
    { key: 'user', label: 'Account' },
    { key: 'domain', label: 'Primary domain' },
  ],
  rows: [{ user: 'acmeco', domain: 'acme.example' }],
  total_rows: 1,
  stored_rows: 1,
  truncated: false,
  created_at: '2026-08-09T09:00:00+00:00',
  expires_at: '2026-08-10T09:00:00+00:00',
}

describe('parseResultTable', () => {
  it('reads the API body into the shape this app renders', () => {
    const table = parseResultTable(BODY)

    expect(table).not.toBeNull()
    expect(table?.token).toBe('table-token-1')
    expect(table?.toolName).toBe('whm_list_accounts')
    expect(table?.columns).toEqual([
      { key: 'user', label: 'Account' },
      { key: 'domain', label: 'Primary domain' },
    ])
    expect(table?.rows).toEqual([{ user: 'acmeco', domain: 'acme.example' }])
    expect(table?.totalRows).toBe(1)
    expect(table?.truncated).toBe(false)
  })

  it('refuses a body with no token', () => {
    // The token is what this page is addressed by. A body without one is not a table, and
    // rendering it anyway would show headings over nothing where a refusal belongs.
    expect(parseResultTable({ ...BODY, token: '' })).toBeNull()
    expect(parseResultTable(null)).toBeNull()
    expect(parseResultTable([BODY])).toBeNull()
  })

  it('carries the total and the flag a capped table sends', () => {
    const table = parseResultTable({
      ...BODY,
      total_rows: 900,
      stored_rows: 25,
      truncated: true,
    })

    expect(table?.totalRows).toBe(900)
    expect(table?.storedRows).toBe(25)
    expect(table?.truncated).toBe(true)
  })

  it('infers truncation when the counts disagree with the flag', () => {
    // Fail-closed on the one direction that matters: a body claiming 900 matches on a 25-row page
    // is capped whatever its flag says, and reading the flag alone would render it as complete.
    const table = parseResultTable({
      ...BODY,
      total_rows: 900,
      stored_rows: 25,
      truncated: false,
    })

    expect(table?.truncated).toBe(true)
  })

  it('does not invent a total from the rows it received', () => {
    // The negative control for the rule above: an honest untruncated body stays untruncated,
    // so the inference cannot be passing by claiming every table is capped.
    const table = parseResultTable({ ...BODY, total_rows: 1, stored_rows: 1, truncated: false })

    expect(table?.truncated).toBe(false)
    expect(table?.totalRows).toBe(1)
  })

  it('falls back to the key when a column carries no label', () => {
    const table = parseResultTable({ ...BODY, columns: [{ key: 'user' }] })

    expect(table?.columns).toEqual([{ key: 'user', label: 'user' }])
  })

  it('drops a column with no key and a row that is not an object', () => {
    const table = parseResultTable({
      ...BODY,
      columns: [{ label: 'Nameless' }, { key: 'user', label: 'Account' }],
      rows: ['not a row', { user: 'acmeco' }],
    })

    expect(table?.columns).toEqual([{ key: 'user', label: 'Account' }])
    expect(table?.rows).toEqual([{ user: 'acmeco' }])
  })

  it('reads a malformed count as zero rather than NaN', () => {
    const table = parseResultTable({ ...BODY, total_rows: 'lots', stored_rows: -3 })

    expect(table?.totalRows).toBe(0)
    expect(table?.storedRows).toBe(0)
  })
})

describe('describeBound', () => {
  it('states both numbers when the page is capped', () => {
    const table = parseResultTable({
      ...BODY,
      total_rows: 1240,
      stored_rows: 25,
      truncated: true,
    })

    const sentence = describeBound(table!)

    expect(sentence).toContain('1,240')
    expect(sentence).toContain('25')
  })

  it('states the total without claiming truncation when nothing was dropped', () => {
    const sentence = describeBound(parseResultTable(BODY)!)

    expect(sentence).toContain('1')
    expect(sentence).not.toContain('narrow the search')
  })
})

describe('cellText', () => {
  it('renders scalars as themselves and nested values as JSON', () => {
    expect(cellText('acmeco')).toBe('acmeco')
    expect(cellText(12)).toBe('12')
    expect(cellText(true)).toBe('true')
    expect(cellText({ nested: 1 })).toBe('{"nested":1}')
  })

  it('renders a missing value as empty rather than as "undefined"', () => {
    // A row that lacks a column's key is a fact about the remote system's answer. Printing the
    // word `undefined` into a table cell would read as data.
    expect(cellText(undefined)).toBe('')
    expect(cellText(null)).toBe('')
  })
})
