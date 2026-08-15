import { ApiError } from '@/lib/auth/fetch-helper'

// Transport-level client error sink. This is the single place unexpected
// client/transport failures are routed. No concrete reporting backend (e.g.
// Sentry) is wired yet — until one is, the sink logs to the console so failures
// are never silently dropped. The filtering below is the part worth keeping
// either way: a sink that reports expected product states trains its reader to
// ignore it.

export type ReportExtraValue = boolean | number | string
export type ReportContext = Record<string, ReportExtraValue | null | undefined>

// Backend error codes that represent expected product/auth states, not bugs —
// these are surfaced in the UI and must not be reported as errors.
const HANDLED_API_ERROR_CODES = new Set([
  'invalid_credentials',
  'missing_authentication',
  'invalid_token',
  'user_pending_approval',
  'admin_access_required',
  'tool_access_denied',
  'thread_not_found',
  'action_request_not_found',
  'action_request_already_decided',
  'request_validation_error',
])

const CANCELED_ERROR_NAMES = new Set(['AbortError'])
const CANCELED_MESSAGE_PATTERN = /\b(abort(?:ed)?|cancel(?:ed|led)?)\b/i

const asErrorField = (error: unknown, key: 'message' | 'name'): string | undefined => {
  if (typeof error !== 'object' || error === null || !(key in error)) {
    return undefined
  }
  const value = (error as Record<typeof key, unknown>)[key]
  return typeof value === 'string' && value.trim().length > 0 ? value : undefined
}

const isCanceledError = (error: unknown): boolean => {
  const name = asErrorField(error, 'name')
  if (name !== undefined && CANCELED_ERROR_NAMES.has(name)) return true

  // A real ApiError carries an HTTP/upstream failure whose message can contain
  // cancellation words (e.g. 503 "Upstream request aborted", "transaction
  // canceled") — never suppress those via the fuzzy message match. The message
  // heuristic is a last resort only for structureless throwables (some
  // environments surface a fetch abort as a plain Error, not an AbortError
  // DOMException) that carry no reliable name/type signal.
  if (error instanceof ApiError) return false

  const message = asErrorField(error, 'message')
  return message !== undefined && CANCELED_MESSAGE_PATTERN.test(message)
}

const isHandledApiError = (error: unknown): error is ApiError => {
  return error instanceof ApiError && HANDLED_API_ERROR_CODES.has(error.errorCode ?? '')
}

// Aborted/canceled requests and expected product-state API errors are noise,
// not reportable failures.
export const shouldReportClientError = (error: unknown): boolean => {
  if (isCanceledError(error)) return false
  if (isHandledApiError(error)) return false
  return true
}

export const reportClientError = (error: unknown, context: ReportContext = {}): void => {
  if (!shouldReportClientError(error)) return
  console.error('[client-error]', error, context)
}
