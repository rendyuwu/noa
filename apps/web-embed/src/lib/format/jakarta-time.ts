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
})

const RELATIVE = new Intl.RelativeTimeFormat('en', { numeric: 'always' })

/** Ascending, so the loop below keeps the largest unit the gap fills. */
const UNITS: readonly (readonly [Intl.RelativeTimeFormatUnit, number])[] = [
  ['second', 1000],
  ['minute', 60_000],
  ['hour', 3_600_000],
  ['day', 86_400_000],
]

/**
 * The largest unit a gap fills, and its size in milliseconds.
 *
 * One definition, because two formatters below choose a unit and they must choose the same one: a
 * countdown that rounded to hours where the relative stamp beside it rounded to minutes would put
 * two readings of one instant on one card.
 */
function largestUnit(gap: number): readonly [Intl.RelativeTimeFormatUnit, number] {
  let chosen = UNITS[0]!
  for (const candidate of UNITS) if (Math.abs(gap) >= candidate[1]) chosen = candidate
  return chosen
}

type Stamp = { date: string; time: string }

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
  }
}

/**
 * `2026-09-09 13:48:01`, for anything that leaves this frame.
 *
 * **A bare stamp may leave the frame only under a heading that names the zone**, and the copied
 * summary block is the one surface that does it: `lib/approvals/summary.ts` groups its three stamps
 * under a heading reading "all times Jakarta (WIB)" and prints them bare beneath it. The zone is
 * stated once where a reader meets it rather than repeated on every line, but it is still stated —
 * a wall-clock time with nothing around it is read by whoever opens the ticket in whatever zone
 * they are in.
 *
 * There is deliberately still exactly one stamp function, which is what keeps the rule enforceable:
 * a second, shorter variant beside this one would be the tempting call, and there would then be a
 * stamp that can reach a ticket with neither an offset nor a heading above it. There is nowhere for
 * one to escape from, because there is only this.
 */
export function formatJakarta(iso: string): string {
  const stamp = jakartaStamp(iso)
  return stamp === null ? iso : `${stamp.date} ${stamp.time}`
}

/** `4 minutes ago`, `in 11 minutes`, `just now`. */
export function formatRelative(iso: string, now: Date = new Date()): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso

  const gap = at.getTime() - now.getTime()
  // Under a second there is no honest unit left: `in 0 seconds` is what the formatter answers and
  // it reads as a broken string rather than as a small number.
  if (Math.abs(gap) < 1000) return 'just now'

  const [unit, span] = largestUnit(gap)
  return RELATIVE.format(Math.round(gap / span), unit)
}

/**
 * `43 minutes`, `no time left`, or `null` when the string is not an instant.
 *
 * **A bare span rather than `in 43 minutes`**, because the card puts it inside a sentence — "You
 * have 43 minutes to answer." — and the relative formatter's own output is a whole phrase that
 * cannot be embedded in one. `Intl.NumberFormat` in unit style is what states a quantity without
 * the preposition, and it picks its unit through `largestUnit` so the countdown and the relative
 * stamp on the same card round to the same unit.
 *
 * **A window that has run out is a different state, not a negative countdown.** `43 minutes ago`
 * where the sentence expects a span would read as an instruction to hurry, so the closed window
 * gets its own phrase that the same sentence still reads correctly.
 *
 * `null` is an expiry string this app cannot parse, and the caller prints no sentence at all:
 * an approval window nobody can read is not an approval window that has any particular length.
 */
export function formatRemaining(iso: string, now: Date = new Date()): string | null {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return null

  const gap = at.getTime() - now.getTime()
  if (gap <= 0) return 'no time left'

  const [unit, span] = largestUnit(gap)
  // Rounding, never a floor: the last fraction of a unit is still time left, and `0 seconds` beside
  // a live Approve button is the one answer that reads as a broken string.
  const count = Math.max(1, Math.round(gap / span))
  return new Intl.NumberFormat('en', { style: 'unit', unit, unitDisplay: 'long' }).format(count)
}
