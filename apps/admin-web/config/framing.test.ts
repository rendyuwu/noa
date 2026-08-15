import { describe, expect, it } from 'vitest'

import { FRAMING_HEADER, FRAMING_SOURCE, FRAME_ANCESTORS, buildFramingHeaders } from './framing'

/**
 * The framing rule (§T.49, V41).
 *
 * `tests/next-config-headers.test.ts` proves the app applies this; these specs prove the rule
 * itself is the one V41 names. Nothing is read from the environment here, so unlike the embed's
 * copy there is no env object to pass in — the absence of an input is part of what is asserted.
 */

describe('buildFramingHeaders', () => {
  it("sends frame-ancestors 'none' on every path", () => {
    expect(buildFramingHeaders()).toEqual([
      {
        source: '/(.*)',
        headers: [{ key: 'Content-Security-Policy', value: "frame-ancestors 'none'" }],
      },
    ])
  })

  it("quotes the keyword — a bare none would be a host source", () => {
    // `'none'` is a CSP keyword and must carry its quotes. Bare `none` parses as a hostname, which
    // matches nothing and so *behaves* the same today — the kind of value that reads as correct
    // until someone extends the policy beside it.
    expect(FRAME_ANCESTORS).toBe("'none'")
    expect(buildFramingHeaders()[0]!.headers[0]!.value).toContain("'none'")
  })

  it('covers every path, not just the pages', () => {
    // Config headers are applied during route resolution without ending the match, so one entry
    // covers the pages, `/healthz`, the 404 and the `/api/*` proxy §T.50 adds. A page-shaped
    // pattern would leave each new route to remember the guard for itself (V83b's shape).
    expect(FRAMING_SOURCE).toBe('/(.*)')
    expect(buildFramingHeaders()).toHaveLength(1)
  })

  it('sends no X-Frame-Options', () => {
    // §T.45 measured the same thing on the embed: `frame-ancestors` supersedes XFO wherever both
    // are read, and one added later would be honoured *instead* by a client that reads XFO first.
    const keys = buildFramingHeaders().flatMap((entry) =>
      entry.headers.map((header) => header.key.toLowerCase()),
    )

    // Literal, not `FRAMING_HEADER.toLowerCase()`: measured on the embed (§T.45) — pointing the
    // constant itself at `X-Frame-Options` left the self-comparing version of this assertion green.
    expect(keys).toEqual(['content-security-policy'])
    expect(FRAMING_HEADER).toBe('Content-Security-Policy')
  })

  it('takes no configuration — there is no origin to name', () => {
    // The separating case against the embed's shape (§T.45), where the origin is an env variable
    // with a default and a validator. This app has no legitimate parent, so a caller has nothing
    // to pass; an ignored parameter here would be the seam a knob later grows in.
    expect(buildFramingHeaders).toHaveLength(0)
  })
})
