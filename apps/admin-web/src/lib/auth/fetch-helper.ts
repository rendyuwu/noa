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
  message?: unknown
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
export const API_BASE = '/api'

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
    normalizedPath === API_BASE || normalizedPath.startsWith(`${API_BASE}/`)
      ? normalizedPath
      : `${API_BASE}${normalizedPath}`

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
// backend message/error_code/request_id (request id falls back to the
// x-request-id header) on any non-OK status.
//
// `message` is read before `detail`, and it is what NOA actually sends: its error
// envelope is { error_code, message, request_id } and `detail` is the *internal*
// diagnostic, deliberately kept out of response bodies — the envelope's own shape. Reading only
// `detail` meant every refusal across the Users, Roles, Tokens and Servers
// verticals rendered as "Request failed (409)" instead of the wording the API
// guarantees. `detail` stays in the chain for any surface that still sends one.
export const jsonOrThrow = async <T>(response: Response): Promise<T> => {
  const payload = (await response.json().catch(() => ({}))) as ErrorPayload
  if (!response.ok) {
    const detail =
      asString(payload?.message) ??
      asString(payload?.detail) ??
      `Request failed (${response.status})`
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
