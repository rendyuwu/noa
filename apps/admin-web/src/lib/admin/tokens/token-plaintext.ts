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
// ONE home because two lanes assert with it — the controller's "never in
// state" and the dialog's per-sink not-logged proofs. Two copies drift, and a
// detector that has quietly stopped matching passes every not-logged test
// ever written while seeing nothing. That failure is silent, which is exactly
// why the pattern is exported: a test can assert the detector still separates.

export const TOKEN_PLAINTEXT_PATTERN = /noa_[A-Za-z0-9_-]{43}/

// This is a WALK, not a serialisation, and that is the whole design.
//
// The obvious implementation is `JSON.stringify` with a replacer that expands
// what a plain stringify swallows. It cannot be made to see:
//
//  - `cause`. Non-enumerable for exactly the same reason `message` is, so
//    `new Error('mint failed', { cause: new Error(PLAINTEXT) })` serialises to
//    `{}` — and `cause` is the idiomatic way a wrapped failure carries the
//    thing that actually went wrong.
//  - `AggregateError.errors`, non-enumerable the same way.
//  - the contents of a `Map` or a `Set`, which stringify to `{}`.
//  - anything behind a `toJSON`, because `JSON.stringify` calls `toJSON` BEFORE
//    the replacer runs. No replacer can close that one from the inside.
//  - any graph containing a `BigInt`: stringify THROWS, and a catch-all falls
//    back to `String(value)` — `'[object Object]'` — reporting clean.
//
// Each of those was measured carrying a real plaintext and reported clean. So
// the search does not serialise at all: it walks the value graph and tests the
// strings it finds. `toJSON` is ignored on purpose — this is searching, not
// rendering, and a value's own opinion about how it should be displayed has no
// bearing on what it is holding.

// Fields an `Error` keeps NON-enumerable, so neither a spread nor
// `Object.entries` can reach them. `cause` recurses through the same branch, so
// a chain of any depth is followed; `errors` is how an `AggregateError` holds
// its children, and it is an array the walk already handles. Reading `errors`
// off every error rather than testing for `AggregateError` keeps one code path:
// a plain error answers `undefined`, which searches to nothing.
const ERROR_FIELDS = ['name', 'message', 'stack', 'cause', 'errors'] as const

function walk(value: unknown, seen: WeakSet<object>): boolean {
  if (typeof value === 'string') return TOKEN_PLAINTEXT_PATTERN.test(value)
  if (value === null || typeof value !== 'object') {
    // `String` is total over the remaining primitives — including `bigint` and
    // `symbol`, which `JSON.stringify` and template interpolation both throw
    // on. A function stringifies to its source, which is searched too.
    return TOKEN_PLAINTEXT_PATTERN.test(String(value))
  }

  // A repeat visit collapses rather than throwing: the first visit already
  // searched everything reachable through this object, so nothing goes unseen
  // and a cycle terminates.
  if (seen.has(value)) return false
  seen.add(value)

  try {
    if (Array.isArray(value)) return value.some((item) => walk(item, seen))

    if (value instanceof Map) {
      for (const [entryKey, entryValue] of value) {
        if (walk(entryKey, seen) || walk(entryValue, seen)) return true
      }
      return false
    }

    if (value instanceof Set) {
      for (const member of value) {
        if (walk(member, seen)) return true
      }
      return false
    }

    const record = value as Record<string, unknown>
    if (value instanceof Error) {
      for (const field of ERROR_FIELDS) {
        if (walk(record[field], seen)) return true
      }
      // and then its own enumerable fields below: an ApiError's `detail`,
      // `status` and `errorCode` are ordinary properties.
    }

    for (const [entryKey, entryValue] of Object.entries(record)) {
      // Keys as well as values. A secret used as a key is as leaked as one used
      // as a value, and a `Map` keyed by plaintext is a real shape.
      if (TOKEN_PLAINTEXT_PATTERN.test(entryKey) || walk(entryValue, seen)) return true
    }
    return false
  } catch {
    // A throwing accessor hides that one node, not the whole search: the walk
    // resumes at the caller's next sibling instead of reporting clean because
    // something exotic sat in the middle of the graph.
    return false
  }
}

// Anything a caller might hand a sink: a string, a serialised spy call list, an
// `Error`, a controller's state, `document.body.innerHTML`.
export function looksLikeTokenPlaintext(value: unknown): boolean {
  return walk(value, new WeakSet<object>())
}
