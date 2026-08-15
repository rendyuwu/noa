'use client'

import {
  AuthRedirectError,
  clearAuth,
  isClearAuthInProgress,
} from '@/lib/auth/session'
import { reportClientError } from '@/lib/observability/error-reporting'

// Transport-level authenticated JSON helpers (issue #100). Browser code always
// talks to the same-origin /api/* proxy — never NOA_API_URL directly — and a
// 401 anywhere triggers the shared session-expiry flow.

type ErrorPayload = {
  detail?: unknown
  error_code?: unknown
  request_id?: unknown
  errorCode?: unknown
  requestId?: unknown
}

const asString = (value: unknown): string | undefined => {
  return typeof value === 'string' && value.length > 0 ? value : undefined
}

// Report only the failures that indicate a real problem: proxy-unreachable
// (status 0) and upstream 5xx. 4xx are expected product/auth states.
const shouldReportApiFailure = (status: number): boolean => {
  return status === 0 || status >= 500
}

// Browser code uses relative same-origin API routes only.
export const getApiUrl = (): string => {
  return '/api'
}

export class ApiError extends Error {
  status: number
  detail: string
  errorCode?: string
  requestId?: string

  constructor(
    status: number,
    detail: string,
    options: { errorCode?: string; requestId?: string } = {},
  ) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.errorCode = options.errorCode
    this.requestId = options.requestId
  }
}

export const fetchWithAuth = async (
  path: string,
  init: RequestInit = {},
): Promise<Response> => {
  const rawPath = path.trim()
  const isAbsolute = /^[a-zA-Z][a-zA-Z\d+\-.]*:/.test(rawPath) || rawPath.startsWith('//')
  if (isAbsolute) {
    throw new Error(
      `fetchWithAuth expects a path (e.g. "/api/foo"), but received an absolute URL: ${path}`,
    )
  }

  // If a logout redirect is already in flight, short-circuit immediately.
  if (isClearAuthInProgress()) {
    throw new AuthRedirectError()
  }

  const headers = new Headers(init.headers ?? {})

  const normalizedPath = rawPath.startsWith('/') ? rawPath : `/${rawPath}`
  const url =
    normalizedPath === '/api' || normalizedPath.startsWith('/api/')
      ? normalizedPath
      : `${getApiUrl()}${normalizedPath}`

  let response: Response

  try {
    response = await fetch(url, { ...init, headers, credentials: 'include' })
  } catch (error) {
    try {
      reportClientError(error)
    } catch {
      // Never let reporting mask the original network failure.
    }
    throw error
  }

  if (response.status === 401) {
    clearAuth('session_expired')
    throw new AuthRedirectError('session_expired')
  }

  return response
}

// Parse a JSON response, throwing a typed ApiError that preserves the stable
// backend detail/error_code/request_id (request id falls back to the
// x-request-id header) on any non-OK status.
export const jsonOrThrow = async <T>(response: Response): Promise<T> => {
  const payload = (await response.json().catch(() => ({}))) as ErrorPayload
  if (!response.ok) {
    const detail = asString(payload?.detail) ?? `Request failed (${response.status})`
    const errorCode = asString(payload?.error_code) ?? asString(payload?.errorCode)
    const requestId =
      asString(payload?.request_id) ??
      asString(payload?.requestId) ??
      asString(response.headers.get('x-request-id'))

    const error = new ApiError(response.status, detail, { errorCode, requestId })

    if (shouldReportApiFailure(response.status)) {
      reportClientError(error, { errorCode, requestId, status: response.status })
    }

    throw error
  }
  return payload as T
}
