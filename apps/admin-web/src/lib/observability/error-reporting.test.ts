import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/lib/auth/fetch-helper'
import { reportClientError, shouldReportClientError } from './error-reporting'

describe('shouldReportClientError', () => {
  it('skips aborted/canceled errors', () => {
    const abort = new DOMException('The operation was aborted', 'AbortError')
    expect(shouldReportClientError(abort)).toBe(false)
    expect(shouldReportClientError(new Error('request was cancelled'))).toBe(false)
  })

  it('skips expected product/auth ApiError codes', () => {
    expect(shouldReportClientError(new ApiError(403, 'x', { errorCode: 'user_pending_approval' }))).toBe(false)
    expect(shouldReportClientError(new ApiError(401, 'x', { errorCode: 'invalid_token' }))).toBe(false)
  })

  it('reports unexpected errors and unhandled ApiError codes', () => {
    expect(shouldReportClientError(new Error('boom'))).toBe(true)
    expect(shouldReportClientError(new ApiError(500, 'x', { errorCode: 'internal_server_error' }))).toBe(true)
  })

  it('reports a structured ApiError even when its message contains a cancellation word', () => {
    // A genuine upstream 5xx must never be suppressed by the fuzzy message
    // match; the "aborted"/"canceled" heuristic is only for structureless
    // throwables that carry no reliable name/type signal.
    expect(shouldReportClientError(new ApiError(503, 'Upstream request aborted'))).toBe(true)
    expect(shouldReportClientError(new ApiError(500, 'transaction canceled'))).toBe(true)
  })
})

describe('reportClientError', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('delivers reportable errors to the sink', () => {
    reportClientError(new Error('boom'), { status: 500 })
    expect(console.error).toHaveBeenCalled()
  })

  it('drops non-reportable errors', () => {
    reportClientError(new DOMException('aborted', 'AbortError'))
    reportClientError(new ApiError(403, 'x', { errorCode: 'thread_not_found' }))
    expect(console.error).not.toHaveBeenCalled()
  })
})
