/**
 * The sections the copied approval record is built out of, and the value rendering they share.
 *
 * `summary.ts` owns the two flavours a clipboard carries and the one pass that assembles them.
 * This owns what each section says. Split for the 300-line cap `apps/api/tests/test_config.py`
 * enforces over `git ls-files`: that file reached it with one line to spare, and a section whose
 * wording cannot be corrected without reddening an unrelated suite is a section that stays wrong.
 *
 * Every rule the sections below obey is stated where it is obeyed. What holds across all of them:
 *
 * **The sentence under the headline is the runner's own, reused rather than authored.** Every
 * CHANGE runner composes a plain-English `message` in its payload and it survives byte for byte
 * into the receipt's `after` half, so line two is that string when there is one and absent when
 * there is not. Nothing here writes a sentence per tool: a table mapping each tool to a phrasing
 * would be a second copy of five runners' vocabularies with nothing reading it against them — a
 * blank the day a runner is added, a stale phrase the day one is reworded. The same argument
 * covers the identity line: the runner's own record, in the insertion order it wrote, which
 * already leads with the machine.
 *
 * **`changedFields: null` and `changedFields: []` say different things and are never folded
 * together.** `null` is "nothing was measured" — the write may have landed and no confirming read
 * answered; `[]` is "the runner compared, and nothing moved". Rendering the first as the second
 * tells an operator a machine is untouched on the evidence that NOA failed to look. `unanswered`
 * carries the same split, and there a silent source is named: "one backend did not answer" does
 * not say which server to go and look at. Both print in all three states — measured,
 * measured-as-nothing, not measured — where every other absent facet is simply left out, because a
 * hole in the measurement is not the same thing as nothing to report.
 *
 * **A delivered credential is stated and its link is not.** The delivery URL opens once; pasting
 * it into a ticket burns it for the operator who needs it. That one was delivered is the fact an
 * audit needs, and the link stays on the card.
 *
 * **No reason field, here or anywhere near here.** The operator's reason is typed at decision time
 * and travels outward only; the card carries none back and these builders have nowhere to put one.
 */

import type { ApprovalCard, ApprovalReceipt } from '@/lib/approvals/card'
import { statusLabel } from '@/lib/approvals/card'
import type { ChangeDelta } from '@/lib/approvals/delta'
import { humanizeToolName } from '@/lib/approvals/tool-name'
import { outcomeText, runnerSentence, verificationText } from '@/lib/approvals/verdict'
import { formatJakarta } from '@/lib/format/jakarta-time'

/** One heading and the self-contained lines under it. Nothing nests — see `renderHtml`. */
export type Section = { heading: string; lines: string[] }

/**
 * The one line in the whole block that names the zone. Every stamp below it is bare
 * (`lib/format/jakarta-time.ts`). A bare wall-clock time in a ticket is otherwise read in whatever
 * zone the reader sits in, so the zone is stated — once, above the stamps it governs, rather than
 * on every line until it reads as furniture. English regardless of the language of the
 * conversation the frame is rendered inside, for the same reason: an audit trail is not translated.
 */
const WHEN_HEADING = 'When — all times Jakarta (WIB)'

/**
 * One value, as a ticket should read it, for every family of key — identity pairs, arguments, field
 * diffs and resolved values all arrive here, because the alternative is a second renderer answering
 * differently for the same byte. `true` is `yes`: `suspended: false → true` is a line about a
 * boolean rather than about an account.
 *
 * The three-way split below is a different distinction and is load-bearing — `null` is a value the
 * target system holds, `undefined` is a key the payload never carried, and folding either into the
 * other states a reading nobody took. The empty string is called out for the same reason: the card
 * parser's fallback for a missing string field is `''`, and `username=` looks like a render bug.
 */
function renderValue(value: unknown): string {
  if (value === null) return 'null'
  if (value === undefined) return 'not recorded'
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  if (typeof value === 'string') return value === '' ? '(empty)' : value
  if (typeof value === 'object') return JSON.stringify(value) ?? String(value)
  return String(value)
}

/** `key=value` pairs in the order they arrived — never sorted, for the reason above. */
function renderPairs(record: Record<string, unknown>): string {
  const entries = Object.entries(record)
  if (entries.length === 0) return 'not recorded'
  return entries.map(([key, value]) => `${key}=${renderValue(value)}`).join(', ')
}

/** Label-and-value rows, padded so a reader scanning the values reads down one column. */
function renderRows(rows: readonly (readonly [string, string])[]): string[] {
  const width = Math.max(...rows.map(([label]) => label.length))
  return rows.map(([label, value]) => `${label.padEnd(width)} ${value}`)
}

/**
 * The two facts, and the runner's own sentence under them. What counts as a sentence is decided in
 * `lib/approvals/verdict.ts` and not here, because the card asks the same question of the same key
 * and the two must not answer it differently — see that file for what a blank one costs.
 */
export function headlineSection(card: ApprovalCard, receipt: ApprovalReceipt | null): Section {
  const verdict = receipt === null ? '' : `, ${outcomeText(receipt.delta, receipt.ok)}`
  const sentence = receipt === null ? null : runnerSentence(receipt.after)
  return {
    heading: `${humanizeToolName(card.toolName)} — ${statusLabel(card.status)}${verdict}`,
    lines: sentence === null ? [] : [sentence],
  }
}

/** What the runner measured, from the subject it names to the reading it took afterwards. */
function measuredLines(delta: ChangeDelta): string[] {
  const lines = [renderPairs(delta.identity)]

  if (delta.changedFields === null) {
    lines.push('Field changes: not measured. Nothing here says that nothing moved.')
  } else if (delta.changedFields.length === 0) {
    lines.push('Field changes: none. The runner compared, and nothing moved.')
  } else {
    for (const change of delta.changedFields) {
      lines.push(`${change.field}: ${renderValue(change.old)} → ${renderValue(change.new)}`)
    }
  }

  const list = delta.listDelta
  if (list !== null) {
    lines.push(`List entries added: ${list.added.length === 0 ? 'none' : list.added.join(', ')}`)
    lines.push(
      `List entries removed: ${list.removed.length === 0 ? 'none' : list.removed.join(', ')}`,
    )
    lines.push(
      list.totalEntries === null
        ? 'List size: not measured. The runner read only the lines matching its target.'
        : `List size: ${list.totalEntries} entries`,
    )
  }

  if (delta.newValues !== null) lines.push(`New values: ${renderPairs(delta.newValues)}`)

  if (delta.deliveredCredential !== null) {
    lines.push(
      'A credential was delivered by one-open link. The link is deliberately not in this ' +
        'summary — opening it spends it, and a ticket is read by more people than one.',
    )
  }

  const cause = delta.verificationCause === null ? '' : ` (${delta.verificationCause})`
  lines.push(`${verificationText(delta.verification)}${cause}`)
  return lines
}

/** Which sources were driven, which went quiet, and how much of the list the claim rests on. */
function sourceLines(delta: ChangeDelta): string[] {
  const lines: string[] = []

  for (const backend of delta.backends ?? []) {
    const verdict = backend.verdict === null ? '' : `, verdict ${backend.verdict}`
    const error = backend.errorCode === null ? '' : `, error ${backend.errorCode}`
    lines.push(
      `Backend ${backend.name}: ${backend.driven ? 'driven' : 'not driven'}, ` +
        `${backend.answered ? 'answered' : 'no answer'}${verdict}${error}`,
    )
  }

  if (delta.unanswered === null) {
    lines.push('Unanswered sources: not measured.')
  } else if (delta.unanswered.length === 0) {
    lines.push('Unanswered sources: none. Every source answered.')
  } else {
    // By name. A count tells an operator that something is wrong and not where to go.
    lines.push(`Unanswered sources: ${delta.unanswered.join(', ')}`)
  }

  if (delta.bound !== null) {
    lines.push(
      delta.bound.truncated
        ? `Evidence bound: ${delta.bound.total} entries, truncated — the claim above rests ` +
            'on a capped reading, not on the whole list.'
        : `Evidence bound: ${delta.bound.total} entries, complete.`,
    )
  }

  return lines
}

export function changedSection(card: ApprovalCard, receipt: ApprovalReceipt | null): Section {
  const delta = receipt?.delta ?? null
  // With no delta there is no measured subject, and the arguments are the only statement of what
  // was aimed at. Labelled as requested rather than as fact: a denied or expired request never ran,
  // and the line must not read as a machine that was touched.
  const lines =
    delta === null
      ? [
          `Target, as requested: ${renderPairs(card.arguments)}`,
          receipt === null
            ? 'Nothing has recorded what this change did.'
            : 'Not measured. No runner published a delta for this request.',
        ]
      : measuredLines(delta)

  if (receipt?.errorCode != null) lines.push(`Error code: ${receipt.errorCode}`)
  if (delta !== null) lines.push(...sourceLines(delta))

  return { heading: 'What changed', lines }
}

/**
 * Three stamps: asked for, decided, finished. `Approval window ends` prints on a PENDING request
 * and nowhere else, and the gate is the status rather than a missing decision stamp — the expiry
 * sweep writes `decided_at` when it flips a row (`core/approvals/expiry.py`; an expiry is a
 * decision the clock made), so a `decidedAt === null` condition would drop the line from exactly
 * the state it exists for.
 */
export function timingSection(card: ApprovalCard): Section {
  const rows: [string, string][] = [['Requested:', formatJakarta(card.createdAt)]]
  if (card.status === 'PENDING') rows.push(['Approval window ends:', formatJakarta(card.expiresAt)])
  rows.push([
    'Decided:',
    card.decidedAt === null ? 'no decision recorded' : formatJakarta(card.decidedAt),
  ])

  const run = card.run
  if (run === null) rows.push(['Finished:', 'nothing ran'])
  else if (run.completedAt === null) rows.push(['Finished:', 'still running when this was copied'])
  else rows.push(['Finished:', formatJakarta(run.completedAt)])

  return { heading: WHEN_HEADING, lines: renderRows(rows) }
}

/**
 * Who to ask, and the one identifier to quote — rather than the four this block used to carry. The
 * action-request id, the conversation reference and the LibreChat account all render in the admin
 * drawer (`apps/admin-web/src/components/admin/audit/action-request-detail-drawer.tsx`) for whoever
 * can open it, and the audit list is keyed on the run id. So the run id is the one string that gets
 * an operator who cannot open that panel an answer from somebody who can. The raw tool name sits
 * here for the same reason: an administrator greps for it, the headline's reader wants the label.
 */
export function supportSection(card: ApprovalCard): Section {
  return {
    heading: 'For support',
    lines: renderRows([
      ['Requested by:', renderValue(card.requester.email)],
      ['Run id:', card.run === null ? 'none' : renderValue(card.run.toolRunId)],
      ['Tool:', renderValue(card.toolName)],
    ]),
  }
}
