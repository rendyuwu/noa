import { fetchWithAuth, jsonOrThrow } from '@/lib/auth/fetch-helper'

import type { McpToken, McpTokensResponse, MintedToken, TokenScope } from './types'

// Transport for the MCP token vertical. Every call goes through the
// shared fetchWithAuth + jsonOrThrow helpers, so a 401 triggers the session-
// expiry flow and any non-OK response throws a typed ApiError preserving the
// backend's stable message / error_code / request_id. Callers surface that
// wording verbatim: `invalid_token_label` (400) and `mcp_token_not_found` (404)
// stay the API's to phrase.
//
// Nothing in this file logs a response. The mint answer carries a credential
// (shown once), and a transport that logged it would defeat every guard above
// it — so the rule is the file's, not the caller's.

// The one place a token path is built. Both routers expose the same three verbs
// over the same shape (mcp_tokens.py:9-14), so the scope picks a prefix and
// nothing else in this module branches on it.
export function tokenBasePath(scope: TokenScope): string {
  return scope.kind === 'self' ? '/me/mcp-tokens' : `/admin/users/${scope.userId}/tokens`
}

// List. The API answers `{ tokens: [...] }` newest-first, ordered in the
// statement — the order is not re-derived here.
export async function fetchTokens(scope: TokenScope): Promise<McpToken[]> {
  const response = await fetchWithAuth(tokenBasePath(scope))
  const payload = await jsonOrThrow<McpTokensResponse>(response)
  return Array.isArray(payload.tokens) ? payload.tokens : []
}

// Mint. The label is trimmed and a blank one becomes `null`, matching what the
// service does with it (`_validate_label`, core/auth/mcp_token_service.py:276-290)
// rather than relying on it: `label: null` is what an unnamed token means on the
// wire, and sending `""` would ask the server to normalize on our behalf.
//
// The returned plaintext is the caller's to display once and drop. This function
// does not cache it, and no caller may store it.
export async function mintToken(scope: TokenScope, label: string | null): Promise<MintedToken> {
  const trimmed = typeof label === 'string' ? label.trim() : ''
  const response = await fetchWithAuth(tokenBasePath(scope), {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ label: trimmed.length > 0 ? trimmed : null }),
  })
  return jsonOrThrow<MintedToken>(response)
}

// Revoke = delete. The endpoint answers `{ ok: true }`; there is no row to
// return, so a non-error response is the whole result. A token id belonging to
// another operator answers 404 exactly as a fabricated one does — the caller
// must not resolve which case it saw.
export async function revokeToken(scope: TokenScope, tokenId: string): Promise<void> {
  const response = await fetchWithAuth(`${tokenBasePath(scope)}/${tokenId}`, { method: 'DELETE' })
  await jsonOrThrow(response)
}
