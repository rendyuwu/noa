import { describe, expect, it } from 'vitest'

import {
  ACTION_REQUEST_QUERY_KEYS,
  TOOL_RUN_QUERY_KEYS,
  activeActionRequestFilterCount,
  activeToolFilterCount,
  buildActionRequestQuery,
  buildToolRunQuery,
  normalizeDateRange,
} from './audit-model'
import { DEFAULT_ACTION_REQUEST_FILTERS, DEFAULT_TOOL_FILTERS } from './types'

// The API-facing half of the audit list (T55). What matters here is that the
// query string the panel builds uses the parameter names the API accepts —
// `apps/api/tests/test_admin_audit_routes.py::FILTER_QUERIES` walks the same
// seven — so the offered set and the accepted set are each asserted once instead
// of drifting apart silently. A filter the API ignores is a control that appears
// to work and does not.

describe('normalizeDateRange', () => {
  it('expands a from day to the UTC day start and a to day to the UTC day end', () => {
    const { from, to } = normalizeDateRange({ fromDate: '2026-07-01', toDate: '2026-07-02' })
    expect(from).toBe('2026-07-01T00:00:00.000Z')
    expect(to).toBe('2026-07-02T23:59:59.999Z')
  })

  it('omits blank dates', () => {
    expect(normalizeDateRange({ fromDate: '', toDate: '' })).toEqual({})
  })
})

describe('buildToolRunQuery', () => {
  it('always sets the limit and includes the cursor when paging', () => {
    const params = new URLSearchParams(buildToolRunQuery(DEFAULT_TOOL_FILTERS, 'CURSOR'))
    expect(params.get('limit')).toBe('50')
    expect(params.get('cursor')).toBe('CURSOR')
  })

  it('serializes every filter under the parameter name the API accepts', () => {
    const params = new URLSearchParams(
      buildToolRunQuery(
        {
          toolName: 'whm_list_accounts',
          status: 'FAILED',
          risk: 'CHANGE',
          conversationRef: 'conv-77',
          requestedByEmail: 'ops@example.com',
          fromDate: '2026-07-01',
          toDate: '2026-07-02',
        },
        null,
      ),
    )

    expect(params.get('toolName')).toBe('whm_list_accounts')
    expect(params.get('status')).toBe('FAILED')
    expect(params.get('risk')).toBe('CHANGE')
    expect(params.get('conversationRef')).toBe('conv-77')
    expect(params.get('requestedByEmail')).toBe('ops@example.com')
    expect(params.get('from')).toBe('2026-07-01T00:00:00.000Z')
    expect(params.get('to')).toBe('2026-07-02T23:59:59.999Z')
    expect(params.has('cursor')).toBe(false)
  })

  it('sends exactly the documented key set when every filter is active', () => {
    const params = new URLSearchParams(
      buildToolRunQuery(
        {
          toolName: 'a',
          status: 'FAILED',
          risk: 'READ',
          conversationRef: 'c',
          requestedByEmail: 'e',
          fromDate: '2026-07-01',
          toDate: '2026-07-02',
        },
        null,
      ),
    )

    // The count is asserted too: a key added to the builder without being added
    // to TOOL_RUN_QUERY_KEYS — i.e. without anybody checking the API takes it —
    // fails here rather than being ignored by the server at runtime.
    expect(TOOL_RUN_QUERY_KEYS).toHaveLength(7)
    expect([...params.keys()].sort()).toEqual(['limit', ...TOOL_RUN_QUERY_KEYS].sort())
  })

  it('omits inactive and whitespace-only filters', () => {
    const params = new URLSearchParams(
      buildToolRunQuery({ ...DEFAULT_TOOL_FILTERS, toolName: '   ' }, null),
    )
    expect([...params.keys()]).toEqual(['limit'])
  })
})

describe('activeToolFilterCount', () => {
  it('counts only the populated filters', () => {
    expect(activeToolFilterCount(DEFAULT_TOOL_FILTERS)).toBe(0)
    expect(
      activeToolFilterCount({ ...DEFAULT_TOOL_FILTERS, risk: 'READ', conversationRef: 'c' }),
    ).toBe(2)
  })

  it('ignores whitespace, so a stray space does not read as a filtered list', () => {
    expect(activeToolFilterCount({ ...DEFAULT_TOOL_FILTERS, toolName: '  ' })).toBe(0)
  })
})

// The same claim over the authorisation trail's five parameters. Two of the
// tool-run controls are deliberately absent — `risk` would scope nothing here
// because every row is a CHANGE, and `conversationRef` is the other list's — so
// the count is asserted as well as the set: a sixth key added without an API
// row to accept it would otherwise pass.
describe('buildActionRequestQuery', () => {
  it('sends only parameters the API accepts', () => {
    const params = new URLSearchParams(
      buildActionRequestQuery(
        {
          fromDate: '2026-09-01',
          toDate: '2026-09-30',
          toolName: 'whm_suspend_account',
          status: 'APPROVED',
          requestedByEmail: 'ops@',
        },
        null,
      ),
    )

    expect(ACTION_REQUEST_QUERY_KEYS).toHaveLength(5)
    expect([...params.keys()].sort()).toEqual(['limit', ...ACTION_REQUEST_QUERY_KEYS].sort())
    expect(params.get('status')).toBe('APPROVED')
    expect(params.get('requestedByEmail')).toBe('ops@')
    expect(params.get('from')).toBe('2026-09-01T00:00:00.000Z')
  })

  it('offers no risk parameter: the API has none on this route', () => {
    const params = new URLSearchParams(
      buildActionRequestQuery(
        { ...DEFAULT_ACTION_REQUEST_FILTERS, toolName: 'whm_suspend_account' },
        null,
      ),
    )
    expect(params.has('risk')).toBe(false)
    expect(params.has('conversationRef')).toBe(false)
  })

  it('carries the cursor when there is one and omits it on the first page', () => {
    expect(
      new URLSearchParams(buildActionRequestQuery(DEFAULT_ACTION_REQUEST_FILTERS, 'abc')).get(
        'cursor',
      ),
    ).toBe('abc')
    expect(
      new URLSearchParams(buildActionRequestQuery(DEFAULT_ACTION_REQUEST_FILTERS, null)).has(
        'cursor',
      ),
    ).toBe(false)
  })

  it('drops blank and whitespace-only filters rather than sending them', () => {
    // A blank `status` sent through would be a 422, and a whitespace `toolName`
    // would narrow to a tool nothing is named — both read as "the filter is
    // broken" rather than "you typed nothing".
    const params = new URLSearchParams(
      buildActionRequestQuery({ ...DEFAULT_ACTION_REQUEST_FILTERS, toolName: '   ' }, null),
    )
    expect([...params.keys()]).toEqual(['limit'])
  })
})

describe('activeActionRequestFilterCount', () => {
  it('counts only filters that carry a value', () => {
    expect(activeActionRequestFilterCount(DEFAULT_ACTION_REQUEST_FILTERS)).toBe(0)
    expect(
      activeActionRequestFilterCount({
        ...DEFAULT_ACTION_REQUEST_FILTERS,
        status: 'DENIED',
        requestedByEmail: '  ',
      }),
    ).toBe(1)
  })
})
