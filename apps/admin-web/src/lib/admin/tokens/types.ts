// Wire shapes for the MCP token vertical, field-for-field from
// `McpTokenResponse` (apps/api/src/noa_api/api/routes/mcp_tokens.py:55-79). The
// six routes share one response model across the admin and the /me router by
// design, so one interface here serves both scopes.
//
// What is ABSENT is the point (mcp_tokens.py:23-27): there is no `plaintext` and
// no `token_hash` field on a read shape, and adding one is not a convenience —
// the credential crosses a response boundary exactly once, in `MintedToken`
// below, and every other path returns something that has nowhere to put it.

// One `mcp_tokens` row as every read path returns it.
export interface McpToken {
  id: string
  user_id: string
  // The display fragment: the public `noa_` marker plus eight characters, with
  // ~208 bits unrevealed. Enough to match a row against a credential pasted into
  // a LibreChat config, never enough to be one.
  token_prefix: string
  label: string | null
  // NULL until TOFU binding happens — the token has not yet been used
  // by a LibreChat identity.
  librechat_user_id: string | null
  // NULL means it has never authenticated a request.
  last_used_at: string | null
  last_ldap_check_at: string | null
  // NULL means nothing retires the row but a revoke.
  expires_at: string | null
  // Not nullable, unlike the four above: a server default, so a row that
  // exists has one.
  created_at: string
}

// `GET` on either router. Newest first, ordered in the statement.
export interface McpTokensResponse {
  tokens: McpToken[]
}

// `POST` answer: the row, plus the plaintext, once. The only shape in this
// app that carries a credential. It is a return value and never controller
// state — see `use-tokens.ts`.
export interface MintedToken {
  token: McpToken
  plaintext: string
}

// Which operator's tokens a call acts on. The union is the only place a token
// path is built (`tokenBasePath`), so a self-scoped action cannot be sent to an
// admin path or the reverse.
export type TokenScope = { kind: 'self' } | { kind: 'user'; userId: string }
