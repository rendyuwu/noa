// The token-plaintext SHAPE detector (§V103), and the reason it has one home.
//
// It mirrors `_plaintexts_in` in `apps/api/tests/test_mcp_token_routes.py:75`:
// the public `noa_` marker (mcp_token_service.py:64) followed by the 43
// urlsafe-base64 characters `TOKEN_ENTROPY_BYTES` produces (:83). Both sides of
// the boundary therefore ask the same question of the same value.
//
// It is a SHAPE, not equality against the minted plaintext. Equality only
// catches the value a test happened to mint; a shape catches any credential-
// shaped string that reaches a sink — including one built, re-encoded or
// concatenated on a path nobody thought to check. The corollary is that a
// `token_prefix` (marker + 8 characters) must NOT match, or every assertion
// built on this fires on a row that is safe to render.
//
// ONE home because two lanes assert with it — the controller's "never in state"
// and the dialog's per-sink not-logged proofs. Two copies of a detector drift,
// and a detector that has quietly stopped matching passes every not-logged test
// ever written while seeing nothing. That failure is silent, which is exactly
// why the pattern is exported: a test can assert the detector still separates.

export const TOKEN_PLAINTEXT_PATTERN = /noa_[A-Za-z0-9_-]{43}/

// Expand what `JSON.stringify` would otherwise swallow. An `Error`'s `message`
// and `stack` are non-enumerable, so a plain stringify of one is `{}` — a
// detector fed a thrown error would report clean while the secret sat in the
// message. That is the realistic route into `console.error`, so it is the case
// this replacer exists for. Repeated object references collapse rather than
// throwing on a cycle: the first visit already serialised that object's
// contents, so nothing reachable goes unseen.
function expandForSearch(): (key: string, value: unknown) => unknown {
  const seen = new WeakSet<object>()
  return (_key: string, value: unknown): unknown => {
    if (value instanceof Error) {
      // Spread first, then the three non-enumerable fields: an ApiError's own
      // `detail` / `status` / `errorCode` come along, and `message` / `stack`
      // are read off the error rather than left to the spread, which cannot
      // reach them.
      return { ...value, name: value.name, message: value.message, stack: value.stack }
    }
    if (typeof value === 'object' && value !== null) {
      if (seen.has(value)) return '[circular]'
      seen.add(value)
    }
    return value
  }
}

// Anything a caller might hand a sink: a string, a serialised spy call list, an
// `Error`, a controller's state, `document.body.innerHTML`.
function searchable(value: unknown): string {
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, expandForSearch()) ?? String(value)
  } catch {
    return String(value)
  }
}

export function looksLikeTokenPlaintext(value: unknown): boolean {
  return TOKEN_PLAINTEXT_PATTERN.test(searchable(value))
}
