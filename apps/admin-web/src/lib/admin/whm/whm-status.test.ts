import { describe, expect, it } from 'vitest'

import type { ValidateWhmServerResponse } from './types'
import { getWhmValidationMessage } from './whm-status'

// The admin validate route's follow-up: `whm_token_acl_insufficient` is a validate failure
// code the panel can now receive (myprivs replaced applist as the credential probe, per the
// ACL-set validate rule). BIGSU's status vocabulary
// is fixed (rule 9), so the chip stays `Failed` for every code — what must not happen is an
// operator reading a raw code string with no next step, which is what these cases hold.

const result = (over: Partial<ValidateWhmServerResponse>): ValidateWhmServerResponse => ({
  ok: false,
  message: 'WHM validation failed',
  ...over,
})

describe('getWhmValidationMessage', () => {
  it('describes what Validate does before it has ever run', () => {
    expect(getWhmValidationMessage(undefined)).toMatch(/Validate checks the WHM API token/)
  })

  it('names the fix for a missing suspend-acct ACL, not a generic retry', () => {
    const message = getWhmValidationMessage(
      result({
        error_code: 'whm_token_acl_insufficient',
        message: 'WHM API token cannot suspend accounts. WHM ACLs (3 granted) — suspend-acct: missing, list-accts: granted',
      }),
    )
    expect(message).toMatch(/suspend-acct/)
    expect(message).toMatch(/Grant/i)
    expect(message).not.toMatch(/retry/i)
  })

  it('falls back to the backend message for any other code, including one neither side has named yet', () => {
    expect(getWhmValidationMessage(result({ error_code: 'ssh_timeout', message: 'SSH connection timed out' }))).toBe(
      'SSH connection timed out',
    )
    expect(
      getWhmValidationMessage(result({ error_code: 'a_future_code_this_test_never_saw', message: 'raw backend text' })),
    ).toBe('raw backend text')
  })

  it('falls back to the backend message on success too', () => {
    expect(
      getWhmValidationMessage({ ok: true, message: 'WHM ACLs (98 granted) — suspend-acct: granted, list-accts: granted' }),
    ).toBe('WHM ACLs (98 granted) — suspend-acct: granted, list-accts: granted')
  })
})
