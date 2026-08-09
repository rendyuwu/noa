import { describe, expect, it } from 'vitest'

import { SIGN_IN_ENV_VAR, resolveSignInUrl } from '@/lib/sign-in'

/**
 * The sign-in address the 401 state offers (§T.43 — V38, V42).
 *
 * Two properties, and the second is the one with teeth. **Unusable means no link**, because a door
 * that goes nowhere reads as an action that was refused rather than one that was never available.
 * And **the protocol allowlist is a security boundary**: the value lands in an `href` in the
 * operator's document, so a `javascript:` URL here would be script execution configured by
 * environment variable.
 */

function env(value?: string): Record<string, string | undefined> {
  return value === undefined ? {} : { [SIGN_IN_ENV_VAR]: value }
}

describe('resolveSignInUrl', () => {
  it('names the variable the operator docs name', () => {
    // Pinned: `.env.example` and the README carry this string, and a rename that stops at the code
    // leaves both pointing at a variable nothing reads (V84a's shape).
    expect(SIGN_IN_ENV_VAR).toBe('NOA_SIGN_IN_URL')
  })

  it.each([
    ['absent', undefined],
    ['empty', ''],
    ['whitespace', '   '],
  ])('offers no link when the variable is %s', (_case: string, value?: string) => {
    expect(resolveSignInUrl(env(value))).toBeNull()
  })

  it.each([
    ['javascript', 'javascript:alert(1)'],
    ['data', 'data:text/html,<h1>sign in</h1>'],
    ['ftp', 'ftp://noa.internal/login'],
    ['file', 'file:///etc/passwd'],
  ])('refuses a %s URL rather than rendering it as an href', (_case: string, value: string) => {
    // The boundary. A sanitising branch would be a second definition of what a link may be; there
    // is one, and it is http/https.
    expect(resolveSignInUrl(env(value))).toBeNull()
  })

  it.each([
    ['a relative path', '/login'],
    ['a bare host', 'noa.internal/login'],
    ['nonsense', 'not a url at all'],
  ])('refuses %s — a new tab cannot be opened at one', (_case: string, value: string) => {
    expect(resolveSignInUrl(env(value))).toBeNull()
  })

  it('refuses a URL carrying credentials', () => {
    // Rendering it would put a secret in the document, the history and any screenshot of the card.
    expect(resolveSignInUrl(env('https://operator:hunter2@noa.internal/login'))).toBeNull()
  })

  it.each([
    ['http', 'http://localhost:3000/login', 'http://localhost:3000/login'],
    ['https', 'https://admin.noa.internal/login', 'https://admin.noa.internal/login'],
    [
      'a query',
      'https://admin.noa.internal/login?next=%2F',
      'https://admin.noa.internal/login?next=%2F',
    ],
    ['an origin with no path', 'https://admin.noa.internal', 'https://admin.noa.internal/'],
  ])('accepts %s', (_case: string, value: string, expected: string) => {
    expect(resolveSignInUrl(env(value))).toBe(expected)
  })

  it('trims surrounding whitespace before judging the value', () => {
    // A trailing newline is what an operator's `.env` editor leaves behind, and `new URL` would
    // otherwise be deciding this.
    expect(resolveSignInUrl(env('  https://admin.noa.internal/login\t'))).toBe(
      'https://admin.noa.internal/login',
    )
  })
})
