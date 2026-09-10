import { describe, expect, it } from 'vitest'

import { ApiError } from '@/lib/auth/fetch-helper'

import { TOKEN_PLAINTEXT_PATTERN, looksLikeTokenPlaintext } from './token-plaintext'

// A detector that has silently stopped matching passes every not-logged
// assertion built on it. So it gets its own tests, and half of them are proofs
// that it still SEES rather than proofs that it stays quiet.

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
    // is the realistic route into console.error, which §V103 names among the
    // sinks the plaintext must never reach, so it is the case that matters most.
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

    // And the cycle is reached FIRST here, so this is the stronger form: a
    // repeat visit has to collapse and let the walk continue, not abort it.
    const cycleFirst: Record<string, unknown> = {}
    cycleFirst.self = cycleFirst
    cycleFirst.token = PLAINTEXT
    expect(looksLikeTokenPlaintext(cycleFirst)).toBe(true)
  })

  it('follows an Error `cause`, at any depth and whatever it holds', () => {
    // `cause` is non-enumerable for exactly the reason `message` is, and it is
    // the idiomatic way a wrapped failure carries the thing that went wrong.
    const wrapped = new Error('mint failed', { cause: new Error(PLAINTEXT) })
    expect(JSON.stringify(wrapped)).toBe('{}')
    expect(looksLikeTokenPlaintext(wrapped)).toBe(true)

    expect(looksLikeTokenPlaintext(new Error('failed', { cause: { plaintext: PLAINTEXT } }))).toBe(
      true,
    )
    const chain = new Error('a', { cause: new Error('b', { cause: new Error(PLAINTEXT) }) })
    expect(looksLikeTokenPlaintext(chain)).toBe(true)
  })

  it('reads the children of an AggregateError', () => {
    const both = new AggregateError([new Error('first failed'), new Error(PLAINTEXT)], 'all failed')
    expect(looksLikeTokenPlaintext(both)).toBe(true)
  })

  it('opens a Map and a Set, which stringify to {} entirely', () => {
    expect(JSON.stringify(new Map([['token', PLAINTEXT]]))).toBe('{}')
    expect(looksLikeTokenPlaintext(new Map([['token', PLAINTEXT]]))).toBe(true)
    // Keys too: a cache keyed by the credential leaks it just as thoroughly.
    expect(looksLikeTokenPlaintext(new Map([[PLAINTEXT, 'minted']]))).toBe(true)
    expect(looksLikeTokenPlaintext(new Set(['unrelated', PLAINTEXT]))).toBe(true)
  })

  it('ignores toJSON, which hides fields before any replacer could see them', () => {
    // `JSON.stringify` calls `toJSON` FIRST, so this hole cannot be closed from
    // inside a replacer at all. The detector is searching, not rendering, and a
    // value's opinion about its own display has no bearing on what it holds.
    const masked = { plaintext: PLAINTEXT, toJSON: () => ({ plaintext: '[redacted]' }) }
    expect(JSON.stringify(masked)).toBe('{"plaintext":"[redacted]"}')
    expect(looksLikeTokenPlaintext(masked)).toBe(true)
  })

  it('survives a BigInt in the graph, which makes stringify throw outright', () => {
    const graph = { attempt: 1n, minted: { plaintext: PLAINTEXT } }
    expect(() => JSON.stringify(graph)).toThrow(TypeError)
    expect(looksLikeTokenPlaintext(graph)).toBe(true)
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

  it('stays quiet on every newly walked path when what it finds is safe', () => {
    // The see-cases above are only worth their assertions if the same paths can
    // answer false: a walk that returned true for any Error, Map or Set would
    // pass all of them and separate nothing.
    expect(looksLikeTokenPlaintext(new Error('mint failed', { cause: new Error(PREFIX) }))).toBe(
      false,
    )
    expect(looksLikeTokenPlaintext(new AggregateError([new Error(PREFIX)], 'all failed'))).toBe(
      false,
    )
    expect(looksLikeTokenPlaintext(new Map([[PREFIX, 'noa_abcd']]))).toBe(false)
    expect(looksLikeTokenPlaintext(new Set([PREFIX, 'laptop']))).toBe(false)
    expect(looksLikeTokenPlaintext({ attempt: 1n, token_prefix: PREFIX })).toBe(false)
    expect(looksLikeTokenPlaintext({ token_prefix: PREFIX, toJSON: () => PREFIX })).toBe(false)
  })

  it('terminates on a cause cycle rather than recursing forever', () => {
    // `cause` is followed, so a cycle through it has to be collapsed by the same
    // WeakSet that guards ordinary references. Without that this test hangs; the
    // suite timeout, not the assertion, would be what reported it.
    const outer = new Error('outer')
    const inner = new Error('inner', { cause: outer })
    outer.cause = inner
    expect(looksLikeTokenPlaintext(outer)).toBe(false)
  })
})
