// Safe return-to validation (issue #99). A returnTo must be an origin-relative
// path on this single shared host (topology.md section 5, section 7.2 —
// single-host routing, every auth URL is origin-relative). Anything that could
// send the user off the origin, back into a login loop, or into the
// API/callback surface is rejected and the caller falls back to the default
// landing.

// `/home` rather than a vertical: it is the role-aware dispatcher, so a
// post-login landing sends an admin to `/admin/users` and everyone else to
// `/me/tokens`. The previous default sent every non-admin straight into a 403.
export const DEFAULT_RETURN_TO = '/home'
const LOGIN_PATH = '/login'

// Matches any ASCII control character (0x00-0x1F) used to smuggle newlines/tabs
// past naive prefix checks.
// eslint-disable-next-line no-control-regex
const CONTROL_CHARS = /[\u0000-\u001f]/

// Reject, in order:
//  - non-strings / empty
//  - anything not starting with a single "/" (absolute URLs, bare words)
//  - protocol-relative "//host" (would leave the origin)
//  - backslashes (browsers normalize "\" to "/", so "/\evil.com" => "//evil.com")
//  - control characters
//  - login-loop targets ("/login", "/login/...", "/login?...")
//  - callback-loop / API-surface targets ("/api", "/api/...", "/api?...",
//    "/api#...") — the post-login redirect must land on a page, never back on
//    the same-origin proxy (raw backend JSON). The query/fragment forms are
//    guarded just like the login pattern above.
//  - percent-encoded slashes ("%2f", "%5c") — a target like "/api%2Fsecrets"
//    slips past the literal "/api/" guard yet decodes to the proxy surface after
//    navigation, so any encoded path/backslash separator is rejected outright.
export const isSafeReturnTo = (raw: unknown): raw is string => {
  if (typeof raw !== 'string' || raw.length === 0) return false
  if (!raw.startsWith('/')) return false
  if (raw.startsWith('//')) return false
  if (raw.includes('\\')) return false
  if (/%2f|%5c/i.test(raw)) return false
  if (CONTROL_CHARS.test(raw)) return false
  if (raw === LOGIN_PATH || raw.startsWith(`${LOGIN_PATH}/`) || raw.startsWith(`${LOGIN_PATH}?`)) {
    return false
  }
  if (
    raw === '/api' ||
    raw.startsWith('/api/') ||
    raw.startsWith('/api?') ||
    raw.startsWith('/api#')
  ) {
    return false
  }
  return true
}

// Return `raw` when it is a safe origin-relative path, otherwise the fallback.
export const sanitizeReturnTo = (
  raw: unknown,
  fallback: string = DEFAULT_RETURN_TO,
): string => {
  return isSafeReturnTo(raw) ? raw : fallback
}
