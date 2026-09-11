import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  DEFAULT_LIBRECHAT_ORIGIN,
  LIBRECHAT_ORIGIN_ENV_VAR,
  resolveFrameAncestor,
} from '../../../config/framing'

import { PUBLIC_LIBRECHAT_ORIGIN_ENV_VAR, resolveFrameTargetOrigin } from './frame-origin'

/**
 * Where a sizing message is allowed to go.
 *
 * One validator, two variables: what is asserted here is the *wrapping* and the *policy*, because
 * the parsing rules already have a test of their own in `config/framing.test.ts` and a second copy
 * of them here would be the duplication this module was written to avoid.
 *
 * **Read off the real `process.env`, unlike every sibling test in this package, and that is forced
 * rather than chosen.** The accessor names its variable itself so the compiled output can carry the
 * value, so there is no environment object to hand it. The cost is stated plainly: nothing below
 * can tell a build that baked the value in from one that did not, because in-process the read is
 * dynamic either way and this file sets whichever variable it was written to set — including, in
 * the spec below, the wrong one. That claim is end-to-end and lives in
 * `tests/frame-origin-inlining.mjs`.
 *
 * That the value agrees with the framing header the app actually sends is a different claim again,
 * and it lives in `tests/next-config-headers.test.ts` where both halves can be compared.
 */

let original: string | undefined

/** Set to a string, or cleared when given `undefined`. */
function configure(value: string | undefined): void {
  if (value === undefined) delete process.env[PUBLIC_LIBRECHAT_ORIGIN_ENV_VAR]
  else process.env[PUBLIC_LIBRECHAT_ORIGIN_ENV_VAR] = value
}

beforeEach(() => {
  original = process.env[PUBLIC_LIBRECHAT_ORIGIN_ENV_VAR]
})

afterEach(() => {
  configure(original)
})

describe('resolveFrameTargetOrigin', () => {
  it('is the configured origin', () => {
    configure('https://chat.example')

    expect(resolveFrameTargetOrigin()).toBe('https://chat.example')
  })

  it('reads the public name, and the header variable does not reach it', () => {
    // The separating case for the two-variable split. A resolver that had been wired to
    // `NOA_LIBRECHAT_ORIGIN` — the name every build, compose file and pipeline already sets —
    // would pass every other spec in this file and answer the wrong variable in production.
    configure(undefined)
    const headerOnly = process.env[LIBRECHAT_ORIGIN_ENV_VAR]
    process.env[LIBRECHAT_ORIGIN_ENV_VAR] = 'https://chat.header-only.example'

    try {
      expect(resolveFrameTargetOrigin()).toBeNull()
    } finally {
      if (headerOnly === undefined) delete process.env[LIBRECHAT_ORIGIN_ENV_VAR]
      else process.env[LIBRECHAT_ORIGIN_ENV_VAR] = headerOnly
    }
  })

  it('answers null when nothing is set, rather than the pinned default', () => {
    // The whole point of not sharing the header's fallback. A default here looks configured and is
    // not: the browser drops every message for an origin mismatch, with no exception and no console
    // error, and the frame simply never grows — indistinguishable from a host that stopped
    // listening. `null` is what `FrameSizer` renders as `no-target-origin`, on the page, findable.
    configure(undefined)

    expect(resolveFrameTargetOrigin()).toBeNull()
    expect(resolveFrameTargetOrigin()).not.toBe(DEFAULT_LIBRECHAT_ORIGIN)
  })

  it('answers null for a blank value, the same as unset', () => {
    // An operator who emptied the variable has unset it. The header reads that as "fall back and
    // never omit"; this side reads it as "say so", and the two policies are deliberate opposites.
    configure('   ')

    expect(resolveFrameTargetOrigin()).toBeNull()
  })

  it('delegates rather than re-parsing — a trailing slash normalises the same way', () => {
    configure('https://chat.example/')

    expect(resolveFrameTargetOrigin()).toBe('https://chat.example')
    expect(resolveFrameTargetOrigin()).toBe(
      resolveFrameAncestor({ [LIBRECHAT_ORIGIN_ENV_VAR]: 'https://chat.example/' }),
    )
  })

  it('answers null for a malformed value, and does not throw', () => {
    // The whole reason this wrapper exists. The shared validator throws on these values on purpose
    // so a bad one stops a build; read at request time inside the card's server component, that
    // same throw is a 500 on `/approvals/[id]` — the approve path dying for a cosmetic feature's
    // configuration. The throw itself is asserted in `config/framing.test.ts`, on the header path,
    // where it is the gate rather than a hazard.
    for (const value of ['*', 'javascript:alert(1)', 'https://a.example https://b.example']) {
      configure(value)
      expect(resolveFrameTargetOrigin()).toBeNull()
    }
  })

  it('is never a wildcard', () => {
    // A wildcard target hands this operator's card geometry to whatever document is framing us. The
    // validator refuses `*` as a value; this is the statement that no branch here invents one.
    const answers: Array<string | null> = []
    for (const value of [undefined, '', 'https://chat.example']) {
      configure(value)
      answers.push(resolveFrameTargetOrigin())
    }

    expect(answers).not.toContain('*')
  })
})
