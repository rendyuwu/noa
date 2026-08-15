import { describe, expect, it } from 'vitest'

import {
  activeActionFilterCount,
  activeToolFilterCount,
  buildActionQuery,
  buildToolRunQuery,
  normalizeDateRange,
} from './audit-model'
import { DEFAULT_ACTION_FILTERS, DEFAULT_TOOL_FILTERS } from './types'

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

describe('buildActionQuery', () => {
  it('always sets the limit and includes the cursor when paging', () => {
    const query = buildActionQuery(DEFAULT_ACTION_FILTERS, 'CURSOR')
    const params = new URLSearchParams(query)
    expect(params.get('limit')).toBe('50')
    expect(params.get('cursor')).toBe('CURSOR')
  })

  it('serializes every active filter with the API aliases', () => {
    const query = buildActionQuery(
      {
        ...DEFAULT_ACTION_FILTERS,
        toolName: 'whm_create_account',
        status: 'APPROVED',
        terminalPhase: 'completed',
        threadId: 'thread-1',
        requestedByEmail: 'ops@example.com',
        fromDate: '2026-07-01',
        toDate: '2026-07-02',
      },
      null,
    )
    const params = new URLSearchParams(query)
    expect(params.get('toolName')).toBe('whm_create_account')
    expect(params.get('status')).toBe('APPROVED')
    expect(params.get('terminalPhase')).toBe('completed')
    expect(params.get('threadId')).toBe('thread-1')
    expect(params.get('requestedByEmail')).toBe('ops@example.com')
    expect(params.get('from')).toBe('2026-07-01T00:00:00.000Z')
    expect(params.get('to')).toBe('2026-07-02T23:59:59.999Z')
    expect(params.has('cursor')).toBe(false)
  })
})

describe('buildToolRunQuery', () => {
  it('serializes the risk filter and omits inactive fields', () => {
    const query = buildToolRunQuery({ ...DEFAULT_TOOL_FILTERS, risk: 'CHANGE' }, null)
    const params = new URLSearchParams(query)
    expect(params.get('risk')).toBe('CHANGE')
    expect(params.has('status')).toBe(false)
  })
})

describe('active filter counts', () => {
  it('counts only the populated action filters', () => {
    expect(activeActionFilterCount(DEFAULT_ACTION_FILTERS)).toBe(0)
    expect(
      activeActionFilterCount({ ...DEFAULT_ACTION_FILTERS, status: 'APPROVED', toolName: 'x' }),
    ).toBe(2)
  })

  it('counts only the populated tool-run filters', () => {
    expect(activeToolFilterCount(DEFAULT_TOOL_FILTERS)).toBe(0)
    expect(activeToolFilterCount({ ...DEFAULT_TOOL_FILTERS, risk: 'READ' })).toBe(1)
  })
})
