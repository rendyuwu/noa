/**
 * The one fixed clock every surface reads.
 *
 * **This file runs with the process zone set to New York, and asserts that it took.** A test of
 * "renders Jakarta time" that runs on a machine already in Jakarta passes against an
 * implementation that reads the machine zone — it is green against the exact bug it exists to
 * catch. So the zone is moved first, and the first case below measures the move rather than
 * trusting it: if that assertion fails, every case under it is meaningless and says so loudly.
 *
 * The instants are chosen so the two zones disagree about the *date*, not just the hour. A case
 * where both zones land on the same calendar day cannot separate a fixed-zone formatter from a
 * local one.
 */

process.env.TZ = 'America/New_York'

import { describe, expect, it } from 'vitest'

import { formatCountdown, formatDuration, formatJakartaOffset, formatRelative } from './jakarta-time'

/** 13:48:01 in Jakarta, 02:48 the same morning in New York. */
const MIDDAY = '2026-09-09T06:48:01.500Z'

/** Past midnight in Jakarta, still the previous afternoon in New York. */
const OVER_MIDNIGHT = '2026-09-09T17:00:00Z'

describe('the process zone this file runs under', () => {
  it('is not Jakarta, so the cases below can fail', () => {
    // The instrument check. New York is four zones and a date boundary away from Jakarta at this
    // instant; if `process.env.TZ` had not taken effect, this reads 13 and the file stops here
    // rather than reporting green on a formatter that reads the machine.
    expect(new Date(MIDDAY).getHours()).toBe(2)
    expect(new Date(OVER_MIDNIGHT).getDate()).toBe(9)
  })
})

/*
 * The stamp assembly is asserted through the offset variant, which is the one with callers. There
 * was a bare `formatJakarta` beside it; it was deleted once the card shipped without ever calling
 * it, and its cases moved here rather than going with it — both ran through the same `jakartaStamp`
 * helper, so the date assembly, the midnight crossing and the passthrough are all still measured.
 */
describe('formatJakartaOffset', () => {
  it('renders the wall clock in Jakarta, with the offset', () => {
    expect(formatJakartaOffset(MIDDAY)).toBe('2026-09-09 13:48:01 +07:00')
  })

  it('rolls the date forward when Jakarta is already on the next day', () => {
    expect(formatJakartaOffset(OVER_MIDNIGHT)).toBe('2026-09-10 00:00:00 +07:00')
  })

  it('renders midnight as hour 00, never 24', () => {
    // `hour12: false` is allowed to answer `24` on some builds, which is a timestamp no log search
    // will ever match. The formatter asks for `h23` for this reason.
    expect(formatJakartaOffset(OVER_MIDNIGHT).slice(11, 13)).toBe('00')
  })

  it('hands back an unparseable value rather than throwing', () => {
    // Reachable: the card parser's fallback for a missing string field is the empty string, and
    // `Intl` answers a RangeError for an invalid Date. A blank iframe is never the better outcome.
    expect(formatJakartaOffset('')).toBe('')
    expect(formatJakartaOffset('whenever')).toBe('whenever')
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

describe('formatCountdown', () => {
  const now = new Date(MIDDAY)

  it('counts down while the window is open', () => {
    expect(formatCountdown('2026-09-09T06:59:01.500Z', now)).toBe('in 11 minutes')
  })

  it('says expired once the window has closed', () => {
    expect(formatCountdown('2026-09-09T06:40:01.500Z', now)).toBe('expired')
  })

  it('says expired at the instant itself, not just past it', () => {
    // The boundary is the whole question a countdown answers, and an off-by-one here shows an
    // operator a live-looking window on a request nothing will accept.
    expect(formatCountdown(MIDDAY, now)).toBe('expired')
  })
})

describe('formatDuration', () => {
  it('renders a short run to a tenth of a second', () => {
    expect(formatDuration('2026-09-09T06:48:01.500Z', '2026-09-09T06:48:03.900Z')).toBe('2.4s')
  })

  it('breaks a long run into minutes and seconds', () => {
    expect(formatDuration('2026-09-09T06:48:01Z', '2026-09-09T06:49:35Z')).toBe('1m 34s')
  })

  it('has no answer while the run is still going', () => {
    expect(formatDuration(MIDDAY, null)).toBeNull()
  })

  it('has no answer when the end precedes the start', () => {
    // A host clock that stepped backwards mid-run did not take a negative amount of time, and
    // clamping to zero would print a measurement nobody made. Both absolute stamps are printed
    // beside this, so staying quiet loses nothing.
    expect(formatDuration('2026-09-09T06:48:03Z', '2026-09-09T06:48:01Z')).toBeNull()
  })

  it('has no answer when either stamp is unparseable', () => {
    expect(formatDuration('', '2026-09-09T06:48:03Z')).toBeNull()
    expect(formatDuration(MIDDAY, 'whenever')).toBeNull()
  })
})
