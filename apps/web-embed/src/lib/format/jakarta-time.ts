/**
 * Timestamps, in one fixed zone, in English.
 *
 * **The zone is `Asia/Jakarta` and never the machine's.** Two operators looking at the same
 * approval have to be looking at the same clock: a screenshot pasted into a ticket by somebody
 * whose laptop is on UTC and one pasted by somebody whose laptop is on WIB must agree, or the two
 * of them are comparing an hour that does not exist against one that does. `toLocaleString()` with
 * no `timeZone` reads the browser's zone, so it is exactly the call this module exists to keep out
 * of the components.
 *
 * **English regardless of the chat around it.** The frame is rendered inside LibreChat and the
 * conversation may be in any language; the audit trail this card feeds is not translated, so the
 * locale is pinned the same way the zone is.
 *
 * **No date library.** `Intl` does all of it, ships with the platform, and the alternative is a
 * dependency in a package whose whole point is that it carries almost none.
 *
 * **`now` is a parameter on everything relative.** A test that reads the wall clock is a test that
 * fails on a slow machine at a month boundary, so the clock is passed in and defaulted rather than
 * read from inside.
 *
 * **An unparseable timestamp is returned verbatim, never thrown on.** `Intl` answers a
 * `RangeError` for an invalid `Date`, and the card parser's fallback for a missing string field is
 * `''` — so the throwing path is reachable from a body the API is free to send. A card that throws
 * is a blank iframe, and the same rule that renders an unknown status as itself applies here: an
 * operator who sees the raw value can go and look at it, an operator who sees a blank frame cannot.
 */

const ZONE = 'Asia/Jakarta'

/**
 * One formatter, built once, carrying every part a stamp needs.
 *
 * `hourCycle: 'h23'` rather than `hour12: false` — the latter is allowed to render midnight as
 * hour `24` on some ICU builds, which is a real timestamp nobody can search for.
 */
const STAMP = new Intl.DateTimeFormat('en-US', {
  timeZone: ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
  timeZoneName: 'longOffset',
})

const RELATIVE = new Intl.RelativeTimeFormat('en', { numeric: 'always' })

/** Ascending, so the loop below keeps the largest unit the gap fills. */
const UNITS: readonly (readonly [Intl.RelativeTimeFormatUnit, number])[] = [
  ['second', 1000],
  ['minute', 60_000],
  ['hour', 3_600_000],
  ['day', 86_400_000],
]

type Stamp = { date: string; time: string; offset: string }

/** The pieces of one instant in Jakarta, or `null` when the string is not an instant. */
function jakartaStamp(iso: string): Stamp | null {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return null

  const part: Record<string, string> = {}
  for (const { type, value } of STAMP.formatToParts(at)) part[type] = value
  const read = (name: string): string => part[name] ?? ''

  return {
    date: `${read('year')}-${read('month')}-${read('day')}`,
    time: `${read('hour')}:${read('minute')}:${read('second')}`,
    // `GMT+07:00` as the formatter gives it, minus the prefix. Read off the same formatter rather
    // than written here as a literal: the offset then cannot drift from the zone above, whatever
    // Indonesia does to its clocks later.
    offset: read('timeZoneName').replace('GMT', ''),
  }
}

/**
 * `2026-09-09 13:48:01 +07:00`, for anything that leaves this frame.
 *
 * The offset is not decoration. A bare wall-clock time pasted into a ticket is read by whoever
 * opens it in whatever zone they are in, and the copied summary is meant to still be true a year
 * from now in another office.
 *
 * There is deliberately no offset-less variant. There was one, and the card shipped without ever
 * calling it: on screen the times are relative, with the ISO in a `title`, and everything that
 * leaves the frame needs the offset by the rule above. A second stamp function existing only to be
 * the tempting shorter call is how a bare wall-clock time reaches a ticket.
 */
export function formatJakartaOffset(iso: string): string {
  const stamp = jakartaStamp(iso)
  return stamp === null ? iso : `${stamp.date} ${stamp.time} ${stamp.offset}`
}

/** `4 minutes ago`, `in 11 minutes`, `just now`. */
export function formatRelative(iso: string, now: Date = new Date()): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso

  const gap = at.getTime() - now.getTime()
  // Under a second there is no honest unit left: `in 0 seconds` is what the formatter answers and
  // it reads as a broken string rather than as a small number.
  if (Math.abs(gap) < 1000) return 'just now'

  let unit: Intl.RelativeTimeFormatUnit = 'second'
  let span = 1000
  for (const [candidate, size] of UNITS) {
    if (Math.abs(gap) >= size) {
      unit = candidate
      span = size
    }
  }
  return RELATIVE.format(Math.round(gap / span), unit)
}

/**
 * `in 11 minutes` while the window is open, `expired` once it has closed.
 *
 * The caller composes the sentence around it, so both answers are whole phrases: an approval
 * window that has run out is not a negative countdown, it is a different state, and rendering it
 * as `11 minutes ago` would read as an instruction to hurry.
 */
export function formatCountdown(iso: string, now: Date = new Date()): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  return at.getTime() <= now.getTime() ? 'expired' : formatRelative(iso, now)
}

/**
 * `2.4s`, `1m 34s`, or `null` when there is no end yet.
 *
 * `null` is also the answer when the two stamps cannot yield a duration — either is unparseable,
 * or the end sits before the start. A run whose host clock stepped backwards mid-execution has not
 * taken a negative amount of time, and clamping it to zero would state a measurement nobody made.
 *
 * Staying quiet loses nothing an audit needs, because the copied summary prints both absolute
 * stamps whatever this answers (`lib/approvals/summary.ts`). It is not a claim about every caller:
 * the card collapses the two stamps into one row and keeps the start in that row's `title`, so a
 * `null` there costs the reader the elapsed time until they reach the copied block.
 */
export function formatDuration(startIso: string, endIso: string | null): string | null {
  if (endIso === null) return null

  const start = new Date(startIso).getTime()
  const end = new Date(endIso).getTime()
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return null

  const elapsed = end - start
  if (elapsed < 60_000) return `${(elapsed / 1000).toFixed(1)}s`

  const seconds = Math.round(elapsed / 1000)
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
}
