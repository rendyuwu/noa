import { describe, expect, it } from 'vitest'

import {
  DEFAULT_LIBRECHAT_ORIGIN,
  FRAMING_HEADER,
  FRAMING_SOURCE,
  LIBRECHAT_ORIGIN_ENV_VAR,
  buildFramingHeaders,
  resolveFrameAncestor,
} from './framing'

/**
 * The framing allowlist (§T.45, V41).
 *
 * Built against an env object passed in rather than `process.env`: the value is read from the
 * repo-root `.env` at build time, so a test that read the real environment would pass or fail on
 * whatever a developer happens to have configured — the same choice `root-env.test.ts` makes.
 */

const env = (value?: string): Record<string, string | undefined> =>
  value === undefined ? {} : { [LIBRECHAT_ORIGIN_ENV_VAR]: value }

describe('resolveFrameAncestor', () => {
  it('is the deployed LibreChat origin when nothing is configured', () => {
    // The shipped value. `.env.example` and `core/config.py::noa_librechat_origin` carry the same
    // string, and V41 names it; pinning it here means a change to it is a change to this line.
    expect(resolveFrameAncestor(env())).toBe('https://chat.noa.internal')
    expect(DEFAULT_LIBRECHAT_ORIGIN).toBe('https://chat.noa.internal')
  })

  it('falls back to the default when the variable is set but blank', () => {
    // Fail closed. An operator who empties the variable has unset it, and "unset" must not become
    // "no header" — that would leave the approval card frameable by anything (V41, V22).
    expect(resolveFrameAncestor(env(''))).toBe(DEFAULT_LIBRECHAT_ORIGIN)
    expect(resolveFrameAncestor(env('   '))).toBe(DEFAULT_LIBRECHAT_ORIGIN)
  })

  it('reads the configured origin — the separating case', () => {
    // Without this the whole suite passes against a function that ignores its input and always
    // answers the default (V87). It is also the mechanism the C21 re-verify rig needs: the spike
    // harness runs LibreChat on `http://chat.noa.internal:3080`.
    expect(resolveFrameAncestor(env('http://chat.noa.internal:3080'))).toBe(
      'http://chat.noa.internal:3080',
    )
  })

  it('strips trailing slashes, like the API normalises its copy of this value', () => {
    // `core/config.py::_normalize_base_url` does the same, so the two cannot disagree about
    // whether `https://chat.noa.internal/` and `https://chat.noa.internal` are one origin.
    expect(resolveFrameAncestor(env('  https://chat.noa.internal//  '))).toBe(
      'https://chat.noa.internal',
    )
  })

  it.each([
    ['*', 'a wildcard allows every origin'],
    ['https://*.noa.internal', 'a host wildcard allows every sibling on the domain'],
    ['https://chat.noa.internal https://evil.example', 'a second origin arrives unnoticed'],
    ["'self'", 'a keyword source is not an origin'],
    ['chat.noa.internal', 'no scheme — CSP would read this as a host-source, not this deployment'],
    ['https://chat.noa.internal/approvals', 'a path is meaningless here and hides a typo'],
    ['data:', 'a scheme-source allows every document of that scheme'],
  ])('refuses %s — %s', (value: string) => {
    // A throw, not a silent fallback: under `next build`/`next dev` this is a build or boot
    // failure, which is what §T.8 asks of a config error. A default here would ship a header
    // nobody meant and read as success.
    expect(() => resolveFrameAncestor(env(value))).toThrow(LIBRECHAT_ORIGIN_ENV_VAR)
  })
})

describe('buildFramingHeaders', () => {
  it('sends frame-ancestors for the configured origin on every path', () => {
    expect(buildFramingHeaders(env())).toEqual([
      {
        source: '/(.*)',
        headers: [
          { key: 'Content-Security-Policy', value: 'frame-ancestors https://chat.noa.internal' },
        ],
      },
    ])
  })

  it('covers every path, not just the pages', () => {
    // Config headers are applied during route resolution without ending the match, so one entry
    // covers the pages, the `/api/*` proxy (§T.44), `/healthz` and the 404. A page-shaped pattern
    // would leave the next route to remember a guard for itself.
    expect(FRAMING_SOURCE).toBe('/(.*)')
  })

  it('sends no X-Frame-Options', () => {
    // §T.45: none is needed — `frame-ancestors` supersedes it, and `ALLOW-FROM` (the only form
    // that could name one origin) is dead. Adding one back would break framing in any client that
    // honours XFO over CSP, so its absence is asserted rather than assumed.
    const keys = buildFramingHeaders(env()).flatMap((entry) =>
      entry.headers.map((header) => header.key.toLowerCase()),
    )

    // Literal, not `FRAMING_HEADER.toLowerCase()`: measured — pointing the constant itself at
    // `X-Frame-Options` left this assertion green, because it was comparing the value to itself.
    expect(keys).toEqual(['content-security-policy'])
    expect(FRAMING_HEADER).toBe('Content-Security-Policy')
  })

  it('propagates the refusal rather than emitting a header for a bad value', () => {
    expect(() => buildFramingHeaders(env('*'))).toThrow(LIBRECHAT_ORIGIN_ENV_VAR)
  })
})
