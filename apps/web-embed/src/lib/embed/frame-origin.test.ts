import { describe, expect, it } from 'vitest'

import { DEFAULT_LIBRECHAT_ORIGIN, LIBRECHAT_ORIGIN_ENV_VAR } from '../../../config/framing'

import { resolveFrameTargetOrigin } from './frame-origin'

/**
 * Where a sizing message is allowed to go.
 *
 * One resolver, reused: what is asserted here is the *wrapping*, because the parsing rules already
 * have a test of their own in `config/framing.test.ts` and a second copy of them here would be the
 * duplication this module was written to avoid.
 *
 * That the value agrees with the framing header the app actually sends is a different claim, and it
 * lives in `tests/next-config-headers.test.ts` where both halves can be compared against each other.
 */

describe('resolveFrameTargetOrigin', () => {
  it('is the configured origin', () => {
    expect(resolveFrameTargetOrigin({ [LIBRECHAT_ORIGIN_ENV_VAR]: 'https://chat.example' })).toBe(
      'https://chat.example',
    )
  })

  it('falls back to the pinned default when nothing is set', () => {
    // Worth naming rather than merely allowing: at request time this is also what an *unset*
    // variable produces on a build that had one set, and the two then disagree — the frame renders
    // (the CSP is the baked value) and every message is dropped by the browser for an origin
    // mismatch, with no exception and no console error. See the module docstring.
    expect(resolveFrameTargetOrigin({})).toBe(DEFAULT_LIBRECHAT_ORIGIN)
  })

  it('delegates rather than re-parsing — a trailing slash normalises the same way', () => {
    expect(resolveFrameTargetOrigin({ [LIBRECHAT_ORIGIN_ENV_VAR]: 'https://chat.example/' })).toBe(
      'https://chat.example',
    )
  })

  it('answers null for a malformed value, and does not throw', () => {
    // The whole reason this wrapper exists. `resolveFrameAncestor` throws on purpose so a bad value
    // stops a build; read at request time inside the card's server component, that same throw is a
    // 500 on `/approvals/[id]` — the approve path dying for a cosmetic feature's configuration.
    expect(resolveFrameTargetOrigin({ [LIBRECHAT_ORIGIN_ENV_VAR]: '*' })).toBeNull()
    expect(resolveFrameTargetOrigin({ [LIBRECHAT_ORIGIN_ENV_VAR]: 'javascript:alert(1)' })).toBeNull()
    expect(
      resolveFrameTargetOrigin({ [LIBRECHAT_ORIGIN_ENV_VAR]: 'https://a.example https://b.example' }),
    ).toBeNull()
  })

  it('is never a wildcard', () => {
    // A wildcard target hands this operator's card geometry to whatever document is framing us. The
    // resolver refuses `*` as a value; this is the statement that no branch here invents one.
    const answers = [
      resolveFrameTargetOrigin({}),
      resolveFrameTargetOrigin({ [LIBRECHAT_ORIGIN_ENV_VAR]: '' }),
      resolveFrameTargetOrigin({ [LIBRECHAT_ORIGIN_ENV_VAR]: 'https://chat.example' }),
    ]

    expect(answers).not.toContain('*')
  })
})
