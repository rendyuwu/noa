import { describe, expect, it } from 'vitest'

import { loginErrorMessage } from './login-messages'
import { ApiError } from '@/lib/auth/fetch-helper'

/**
 * The sign-in copy.
 *
 * The envelope shape keeps the submitted value out of an error body API-side; this is the
 * browser half of the same rule. A refused credential answers one vague pair whatever the
 * cause, so the page cannot be used
 * to find out which half was wrong — while the states that are *not* a wrong credential stay
 * distinguishable, because retrying the password is not the remedy for any of them.
 */

const withCode = (code: string, status = 401) =>
  new ApiError(status, 'internal diagnostic that must not be shown', { errorCode: code })

describe('loginErrorMessage', () => {
  it('is vague on a refused credential', () => {
    expect(loginErrorMessage(withCode('invalid_credentials'))).toEqual({
      title: 'Sign-in failed',
      description: 'Your email or password is incorrect.',
    })
  })

  it('treats an unrecognised 401 as a refused credential too', () => {
    // A new auth code should not read as a transport fault and invite a retry that cannot succeed.
    expect(loginErrorMessage(withCode('some_future_auth_code')).description).toBe(
      'Your email or password is incorrect.',
    )
  })

  it('never carries the backend detail into operator-facing copy', () => {
    // `detail` is a log-line diagnostic (body = error_code + message + request_id). On the
    // credential path it can name the account or the bind failure, so no branch may echo it.
    const codes = [
      'invalid_credentials',
      'user_pending_approval',
      'login_rate_limited',
      'authentication_service_unavailable',
      'some_future_auth_code',
    ]

    for (const code of codes) {
      const message = loginErrorMessage(withCode(code))
      expect(`${message.title} ${message.description}`).not.toContain('internal diagnostic')
    }
  })

  it('separates a pending account from a wrong password', () => {
    // The credentials were right and the account exists; the login flow provisions it
    // `is_active=False`. This
    // one names a state an administrator has to clear, not a field an attacker can probe.
    expect(loginErrorMessage(withCode('user_pending_approval', 403)).title).toBe(
      'Account pending approval',
    )
  })

  it('separates rate limiting and an LDAP outage from both', () => {
    expect(loginErrorMessage(withCode('login_rate_limited', 429)).title).toBe('Too many attempts')
    expect(loginErrorMessage(withCode('authentication_service_unavailable', 503)).title).toBe(
      'Sign-in unavailable',
    )
  })

  it('falls back generically for something that is not an ApiError', () => {
    // A transport failure — the proxy unreachable, the browser offline. Not a credential verdict,
    // and mapping it to one would send the operator hunting for a typo.
    expect(loginErrorMessage(new TypeError('Failed to fetch'))).toEqual({
      title: 'Sign-in failed',
      description: 'Something went wrong signing you in. Try again.',
    })
  })

  it('does not answer the vague credential copy for every input', () => {
    // The negative control for the two "separates" specs: a mapper that returned INVALID always
    // would satisfy the first three cases in this file.
    const distinct = new Set(
      ['invalid_credentials', 'user_pending_approval', 'login_rate_limited'].map(
        (code) => loginErrorMessage(withCode(code)).title,
      ),
    )

    expect(distinct.size).toBe(3)
  })
})
