import { describe, expect, it } from 'vitest'

import { ApiError } from '@/lib/auth/fetch-helper'

import { TOKEN_PLAINTEXT_PATTERN, looksLikeTokenPlaintext } from './token-plaintext'

// A detector that has silently stopped matching passes every not-logged
// assertion built on it. So it gets its own tests, and half of them are proofs
// that it still SEES (V87) rather than proofs that it stays quiet.

const PLAINTEXT = `noa_${'k3Y'.repeat(14)}z`
const PREFIX = 'noa_abcd1234'

describe('the pattern', () => {
  it('is marker plus exactly 43 characters, matching TOKEN_ENTROPY_BYTES', () => {
    expect(PLAINTEXT).toHaveLength(4 + 43)
    expect(TOKEN_PLAINTEXT_PATTERN.test(PLAINTEXT)).toBe(true)
    // 42 is not enough to be a credential.
    expect(TOKEN_PLAINTEXT_PATTERN.test(`noa_${'a'.repeat(42)}`)).toBe(false)
  })

  it('carries no /g flag, so repeated calls cannot skip a match', () => {
    // A /g regex keeps `lastIndex` between `.test()` calls: the second identical
    // question would answer false, and a per-sink loop would go blind halfway.
    expect(TOKEN_PLAINTEXT_PATTERN.flags).toBe('')
    expect(looksLikeTokenPlaintext(PLAINTEXT)).toBe(true)
    expect(looksLikeTokenPlaintext(PLAINTEXT)).toBe(true)
  })
})

describe('what it must see', () => {
  it('finds a plaintext embedded in surrounding text and markup', () => {
    expect(looksLikeTokenPlaintext(`Your token is ${PLAINTEXT}, copy it now`)).toBe(true)
    expect(looksLikeTokenPlaintext(`<input readonly value="${PLAINTEXT}">`)).toBe(true)
  })

  it('finds one inside a serialised spy call list', () => {
    // The shape worker-ui feeds it: `mock.calls`, an array of argument arrays.
    expect(looksLikeTokenPlaintext([['Token minted', { plaintext: PLAINTEXT }]])).toBe(true)
    expect(looksLikeTokenPlaintext([['Token minted', { prefix: PREFIX }]])).toBe(false)
  })

  it('finds one in an Error message or stack, which JSON.stringify alone would miss', () => {
    // `message` is non-enumerable: `JSON.stringify(new Error(x))` is `{}`. This
    // is the realistic route into console.error (A10), so it is the case that
    // matters most.
    expect(JSON.stringify(new Error(PLAINTEXT))).toBe('{}')
    expect(looksLikeTokenPlaintext(new Error(PLAINTEXT))).toBe(true)
    expect(looksLikeTokenPlaintext(new ApiError(500, `mint failed for ${PLAINTEXT}`))).toBe(true)
  })

  it('finds one in an Error nested inside a call list', () => {
    expect(looksLikeTokenPlaintext([[new Error(PLAINTEXT), { status: 500 }]])).toBe(true)
  })

  it('keeps looking after a circular reference instead of giving up', () => {
    // A cycle makes a plain stringify throw; falling back to String(value) would
    // read '[object Object]' and report clean.
    const node: Record<string, unknown> = { plaintext: PLAINTEXT }
    node.self = node
    expect(looksLikeTokenPlaintext(node)).toBe(true)
  })
})

describe('what it must not see', () => {
  it('does not fire on a token_prefix — the fragment every row renders', () => {
    expect(looksLikeTokenPlaintext(PREFIX)).toBe(false)
    expect(looksLikeTokenPlaintext({ token_prefix: PREFIX, label: 'laptop' })).toBe(false)
  })

  it('does not fire on empty, absent or unrelated values', () => {
    expect(looksLikeTokenPlaintext('')).toBe(false)
    expect(looksLikeTokenPlaintext(null)).toBe(false)
    expect(looksLikeTokenPlaintext(undefined)).toBe(false)
    expect(looksLikeTokenPlaintext(42)).toBe(false)
    expect(looksLikeTokenPlaintext('This token is no longer present')).toBe(false)
  })
})
