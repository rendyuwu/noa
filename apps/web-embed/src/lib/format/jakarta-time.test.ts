/**
 * The one fixed clock every surface reads.
 *
 * **This file runs with the process zone set to New York, and asserts that it took.** A test of
 * "renders Jakarta time" that runs on a machine already in Jakarta passes against an
 * implementation that reads the machine zone — it is green against the exact bug it exists to
 * catch. The zone is set in `vitest.config.ts` under `test.env`, which lands at worker spawn: the
 * formatter under test captures its zone when this module graph is imported, and an assignment in
 * this file would sit below the hoisted import of it. The first case below measures the move
 * rather than trusting it: if that assertion fails, every case under it is meaningless and says so
 * loudly.
 *
 * The instants are chosen so the two zones disagree about the *date*, not just the hour. A case
 * where both zones land on the same calendar day cannot separate a fixed-zone formatter from a
 * local one.
 */

import { describe, expect, it } from 'vitest'

import { formatJakarta, formatRelative, formatRemaining } from './jakarta-time'

/** 13:48:01 in Jakarta, 02:48 the same morning in New York. */
const MIDDAY = '2026-09-09T06:48:01.500Z'

/** Past midnight in Jakarta, still the previous afternoon in New York. */
const OVER_MIDNIGHT = '2026-09-09T17:00:00Z'

describe('the process zone this file runs under', () => {
  it('is not Jakarta, so the cases below can fail', () => {
    // The instrument check, and it reads the zone off `Intl` rather than off `Date`. `Date`
    // re-reads `TZ` on every call, so it reports New York even in a process where `Intl` already
    // captured Jakarta at import time — which is the failure the formatter under test can have.
    // An instrument that cannot see that failure is green against it.
    expect(new Intl.DateTimeFormat().resolvedOptions().timeZone).not.toBe('Asia/Jakarta')
  })
})

/*
 * One stamp function, and these are its cases. The zone it renders in is named by the heading the
 * copied summary prints it under, never by the string itself, so the assertions below are exact
 * whole values: a stamp that grew a suffix would be a second statement of the zone with nothing
 * saying the two agree.
 */
describe('formatJakarta', () => {
  it('renders the wall clock in Jakarta, and carries no offset', () => {
    expect(formatJakarta(MIDDAY)).toBe('2026-09-09 13:48:01')
  })

  it('rolls the date forward when Jakarta is already on the next day', () => {
    expect(formatJakarta(OVER_MIDNIGHT)).toBe('2026-09-10 00:00:00')
  })

  it('renders midnight as hour 00, never 24', () => {
    // `hour12: false` is allowed to answer `24` on some builds, which is a timestamp no log search
    // will ever match. The formatter asks for `h23` for this reason.
    expect(formatJakarta(OVER_MIDNIGHT).slice(11, 13)).toBe('00')
  })

  it('hands back an unparseable value rather than throwing', () => {
    // Reachable: the card parser's fallback for a missing string field is the empty string, and
    // `Intl` answers a RangeError for an invalid Date. A blank iframe is never the better outcome.
    expect(formatJakarta('')).toBe('')
    expect(formatJakarta('whenever')).toBe('whenever')
  })
})

describe('formatRelative', () => {
  const now = new Date(MIDDAY)

  it('counts backwards in the largest unit that fits', () => {
    expect(formatRelative('2026-09-09T06:44:01.500Z', now)).toBe('4 minutes ago')
    expect(formatRelative('2026-09-09T06:48:00.500Z', now)).toBe('1 second ago')
    expect(formatRelative('2026-09-09T03:48:01.500Z', now)).toBe('3 hours ago')
    expect(formatRelative('2026-09-07T06:48:01.500Z', now)).toBe('2 days ago')
  })

  it('counts forwards for an instant that has not happened', () => {
    expect(formatRelative('2026-09-09T06:59:01.500Z', now)).toBe('in 11 minutes')
  })

  it('says just now inside the last second', () => {
    // `in 0 seconds` is what the formatter answers here, and it reads as a broken string.
    expect(formatRelative('2026-09-09T06:48:01.900Z', now)).toBe('just now')
  })
})

describe('formatRemaining', () => {
  const now = new Date(MIDDAY)

  it('answers a bare span, so a caller can put it inside a sentence', () => {
    // `in 11 minutes` is a whole phrase and cannot be embedded in one — "You have in 11 minutes to
    // answer" is the sentence the relative formatter would have produced.
    expect(formatRemaining('2026-09-09T06:59:01.500Z', now)).toBe('11 minutes')
  })

  it('picks the same unit the relative stamp beside it would', () => {
    // Two readings of one instant on one card have to round alike, which is why both formatters go
    // through one unit chooser.
    expect(formatRemaining('2026-09-09T08:48:01.500Z', now)).toBe('2 hours')
    expect(formatRelative('2026-09-09T08:48:01.500Z', now)).toBe('in 2 hours')
  })

  it('says a closed window is closed rather than counting backwards', () => {
    // A window that has run out is a different state, not a negative countdown: `11 minutes` there
    // would read as an instruction to hurry on a request nothing will accept.
    expect(formatRemaining('2026-09-09T06:40:01.500Z', now)).toBe('no time left')
  })

  it('says so at the instant itself, not just past it', () => {
    // The boundary is the whole question a countdown answers, and an off-by-one here shows an
    // operator a live-looking window.
    expect(formatRemaining(MIDDAY, now)).toBe('no time left')
  })

  it('never answers zero of anything while there is time left', () => {
    // Under a second there is no honest unit left, and `0 seconds` beside a live Approve button is
    // the one answer that reads as a broken string rather than as a small number.
    expect(formatRemaining('2026-09-09T06:48:01.900Z', now)).toBe('1 second')
  })

  it('has no answer for a stamp it cannot read, and the caller then prints no sentence', () => {
    // A window nobody can parse is not a window of any particular length. The card parser's
    // fallback for a missing string field is `''`, so this path is reachable from a body the API is
    // free to send.
    expect(formatRemaining('')).toBeNull()
  })
})
