/**
 * The whole approval, as one block of text an operator can paste into a ticket.
 *
 * **Evidence has to be able to leave the frame, and it leaves as text.** Measured on the pinned
 * host: no image reaches the clipboard from inside the frame by any route, so this block is the
 * only durable record an operator can carry out. What it carries is what a ticket is opened for a
 * year later — what the change did to which machine, when, and the one identifier an administrator
 * can look the run up by.
 *
 * **The headline states two facts and never one.** The status is the decision an operator made; the
 * verdict beside it (`lib/approvals/verdict.ts`) is what the change then did, read off the
 * verification state rather than off the call's return. A receipt NOA could not verify, pasted as
 * "Approved" alone, folds a non-answer into the benign value; an approved request with no receipt
 * at all states that nothing has recorded what the change did, rather than pasting as an approval
 * over an empty result.
 *
 * **The sentence under it is the runner's own, reused rather than authored.** Every CHANGE runner
 * composes a plain-English `message` in its payload and it survives byte for byte into the
 * receipt's `after` half, so line two is that string when there is one and absent when there is
 * not. Nothing here writes a sentence per tool: a table mapping each tool to a phrasing would be a
 * second copy of five runners' vocabularies with nothing reading it against them — a blank the day
 * a runner is added, a stale phrase the day one is reworded. The same argument covers the identity
 * line: the runner's own record, in the insertion order it wrote, which already leads with the
 * machine.
 *
 * **Two flavours, one builder.** `text` and `html` ride on a single copy and must say the same
 * thing: a reader who pastes into a plain-text field and a reader who pastes into a rich one are
 * quoting the same record in a dispute. One section list rather than two writers is what keeps that
 * true — there is nowhere to add a fact to only one of them.
 *
 * **The zone is named once, in a heading, and the stamps under it are bare**
 * (`lib/format/jakarta-time.ts`). A bare wall-clock time in a ticket is otherwise read in whatever
 * zone the reader sits in, so the zone is stated — once, above the stamps it governs, rather than
 * on every line until it reads as furniture. English regardless of the language of the conversation
 * the frame is rendered inside, for the same reason: an audit trail is not translated.
 *
 * **`changedFields: null` and `changedFields: []` say different things and are never folded
 * together.** `null` is "nothing was measured" — the write may have landed and no confirming read
 * answered; `[]` is "the runner compared, and nothing moved". Rendering the first as the second
 * tells an operator a machine is untouched on the evidence that NOA failed to look. `unanswered`
 * carries the same split, and there a silent source is named: "one backend did not answer" does not
 * say which server to go and look at. Both print in all three states — measured,
 * measured-as-nothing, not measured — where every other absent facet is simply left out, because a
 * hole in the measurement is not the same thing as nothing to report.
 *
 * **A delivered credential is stated and its link is not.** The delivery URL opens once; pasting
 * it into a ticket burns it for the operator who needs it. That one was delivered is the fact an
 * audit needs, and the link stays on the card.
 *
 * **No reason field, here or anywhere near here.** The operator's reason is typed at decision time
 * and travels outward only; the card carries none back and this builder has nowhere to put one.
 *
 * **Every value is HTML-escaped on its way into the `html` flavour**, at one point in `renderHtml`,
 * because the component that copies it hands the string to `dangerouslySetInnerHTML`.
 */

import type { ApprovalCard, ApprovalReceipt } from '@/lib/approvals/card'
import { statusLabel } from '@/lib/approvals/card'
import type { ChangeDelta } from '@/lib/approvals/delta'
import { humanizeToolName } from '@/lib/approvals/tool-name'
import { outcomeText, verificationText } from '@/lib/approvals/verdict'
import { formatJakarta } from '@/lib/format/jakarta-time'

/** The one copy, in both flavours the clipboard carries. */
export type Summary = { text: string; html: string }

/** One heading and the self-contained lines under it. Nothing nests — see `renderHtml`. */
type Section = { heading: string; lines: string[] }

const TITLE = 'NOA approval record'
/** The one line in the whole block that names the zone. Every stamp below it is bare. */
const WHEN_HEADING = 'When — all times Jakarta (WIB)'

function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

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
 * The two facts, and the runner's own sentence under them. The sentence is taken only when it is a
 * non-empty string: `after` is a `Record<string, unknown>`, so a `null` or a number under that key
 * is representable, and a value that is not a sentence gets no line rather than an invented one.
 */
function headlineSection(card: ApprovalCard, receipt: ApprovalReceipt | null): Section {
  const verdict = receipt === null ? '' : `, ${outcomeText(receipt.delta, receipt.ok)}`
  const message = receipt === null ? undefined : receipt.after['message']
  return {
    heading: `${humanizeToolName(card.toolName)} — ${statusLabel(card.status)}${verdict}`,
    lines: typeof message === 'string' && message.trim() !== '' ? [message] : [],
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

function changedSection(card: ApprovalCard, receipt: ApprovalReceipt | null): Section {
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
function timingSection(card: ApprovalCard): Section {
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
function supportSection(card: ApprovalCard): Section {
  return {
    heading: 'For support',
    lines: renderRows([
      ['Requested by:', renderValue(card.requester.email)],
      ['Run id:', card.run === null ? 'none' : renderValue(card.run.toolRunId)],
      ['Tool:', renderValue(card.toolName)],
    ]),
  }
}

function renderText(sections: Section[]): string {
  const blocks = sections.map((section) =>
    [section.heading, ...section.lines.map((line) => `  ${line}`)].join('\n'),
  )
  return [TITLE, ...blocks].join('\n\n')
}

/**
 * The same sections as markup. A list per section rather than a `<pre>`: the flavour exists so a
 * rich-text field renders it as structure, and a `<pre>` pasted into one arrives as a monospace
 * wall no better than the plain flavour beside it, which would make the second flavour pointless.
 *
 * Escaping is here, once, on the whole line. Every line is assembled from a literal label and
 * API-supplied values, so escaping the finished line covers both and there is no second place to
 * forget.
 */
function renderHtml(sections: Section[]): string {
  const blocks = sections.map((section) => {
    const items = section.lines.map((line) => `<li>${escapeHtml(line)}</li>`).join('')
    const list = items === '' ? '' : `<ul>${items}</ul>`
    return `<p><strong>${escapeHtml(section.heading)}</strong></p>${list}`
  })
  return `<div><p><strong>${escapeHtml(TITLE)}</strong></p>${blocks.join('')}</div>`
}

/** The whole record, in both flavours, from one pass over one card. */
export function buildSummary(card: ApprovalCard): Summary {
  const receipt = card.receipt
  const sections = [
    headlineSection(card, receipt),
    changedSection(card, receipt),
    timingSection(card),
    supportSection(card),
  ]

  return { text: renderText(sections), html: renderHtml(sections) }
}
