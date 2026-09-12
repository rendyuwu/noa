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
 * **That silence now costs the reader the elapsed time outright**, and the guard stays anyway. The
 * copied summary used to print the run's start beside its finish, so a `null` there lost nothing;
 * it prints the finish alone now (`lib/approvals/summary.ts`), and the card prints this duration
 * alone, with the start in that row's `title`. On a `null` the card falls back to the start — one
 * row either way, one stamp either way — so the run's two ends are split one per surface, the
 * start on the card and the finish in the copied block, and how long it took is stated on neither.
 * Both stamps sit together only on the `/admin` audit row, whose own derived duration answers zero
 * rather than a negative (`core/audit/tool_run_reads.py`), so it does not state the elapsed time
 * either.
 *
 * Rare rather than impossible: both stamps come from one clock now (`core.clock.now_utc`, via
 * `core/db/columns.py`), so the cross-host gap that used to reach here is gone and an NTP step on
 * that one host mid-run is what is left. A clock that steps mid-run is exactly the case this
 * refuses to average away.
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
