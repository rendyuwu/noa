/**
 * A parked large-READ table, as this app models it (§T.56, §I.embed).
 *
 * One shape, parsed once, at the edge of the app. The API's body is snake_case because it is an
 * HTTP contract shared with the spec; everything inside this package reads the camelCase view
 * below, so a field rename upstream breaks in `parseResultTable` rather than in whichever component
 * happened to read it. The same split `lib/approvals/card.ts` makes one surface over.
 *
 * **Parsing is permissive; the counts are not invented.** A body missing a label renders the key
 * instead, because a table that throws is a blank iframe and V38 says that is not an acceptable
 * state. But `totalRows` is never derived from the rows that arrived: V85's whole point is that the
 * number of matches and the number of rows on the page are two facts, and a page that recomputed
 * the first from the second would report a capped table as a complete one.
 *
 * **There is nothing to decide here** (§I.embed). No CSRF token, no reason, no approve or deny —
 * this is a listing a READ already produced, and the API sends no field for any of it.
 */

/** One column of a parked table: the row key, and the heading printed above it. */
export type ResultTableColumn = {
  key: string
  label: string
}

export type ResultTable = {
  token: string
  toolName: string
  columns: ResultTableColumn[]
  rows: Record<string, unknown>[]
  /** Matches before the cap. Never `rows.length` — see the module docstring. */
  totalRows: number
  /** How many rows this page holds. */
  storedRows: number
  /** Whether the cap dropped anything. */
  truncated: boolean
  createdAt: string
  expiresAt: string
}

/**
 * One read of one table, however it turned out (§T.56).
 *
 * Four kinds because four of them render differently, exactly as `ApprovalCardLoad` has: a 401 is
 * V38's "cannot authenticate here", a 404 is the API's single answer for unknown / another
 * operator's / a deleted requester's / expired, and anything else is "could not load" — which is
 * neither, and must never be shown as an empty table.
 */
export type ResultTableLoad =
  | { kind: 'table'; table: ResultTable }
  | { kind: 'unauthenticated' }
  | { kind: 'not-found' }
  | { kind: 'unavailable'; status: number }

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function asCount(value: unknown): number {
  // Finite, non-negative integers only. A count that arrived as a string or a `NaN` renders as
  // zero rather than as `NaN rows`, which is the same fail-quiet rule the labels follow.
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : 0
}

function parseColumns(value: unknown): ResultTableColumn[] {
  if (!Array.isArray(value)) return []

  return value.flatMap((entry) => {
    if (!isRecord(entry)) return []

    const key = asString(entry['key'])
    // No key, no column: a heading with nothing under it is a column of blanks in every row.
    if (!key) return []

    return [{ key, label: asString(entry['label'], key) }]
  })
}

function parseRows(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) return []
  return value.filter(isRecord)
}

/**
 * The API body as a `ResultTable`, or `null` if it is not one.
 *
 * `null` for a body with no `token`: that is the field the page is addressed by and the one a retry
 * would re-read, so a body without it is not a table — and rendering one anyway would show an
 * operator an empty page where a refusal belongs.
 *
 * `truncated` is `true` only for a literal `true`, but it is also inferred when the counts
 * disagree: a body claiming 900 matches with 25 rows and `truncated: false` is malformed, and the
 * direction that must never fail open is the one where a capped table looks complete.
 */
export function parseResultTable(value: unknown): ResultTable | null {
  if (!isRecord(value)) return null

  const token = asString(value['token'])
  if (!token) return null

  const rows = parseRows(value['rows'])
  const storedRows = asCount(value['stored_rows'])
  const totalRows = asCount(value['total_rows'])

  return {
    token,
    toolName: asString(value['tool_name']),
    columns: parseColumns(value['columns']),
    rows,
    totalRows,
    storedRows,
    truncated: value['truncated'] === true || totalRows > storedRows,
    createdAt: asString(value['created_at']),
    expiresAt: asString(value['expires_at']),
  }
}

/**
 * The sentence a table says about its own bound.
 *
 * Both numbers either way, so "1,240 of 1,240" and "25 of 900" are the same sentence with
 * different numbers rather than two shapes a reader has to tell apart — and so a capped page can
 * never be mistaken for a complete one at a glance, which is the failure V85 names.
 */
export function describeBound(table: ResultTable): string {
  const total = table.totalRows.toLocaleString('en-US')
  if (!table.truncated) {
    return `${total} rows, all of them shown.`
  }

  const shown = table.storedRows.toLocaleString('en-US')
  return `${total} rows matched. This page shows the first ${shown} — narrow the search to see the rest.`
}

/** One cell, rendered from a row's own value. Objects are JSON so a nested field is still legible. */
export function cellText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}
