/**
 * The whole approval, as one block of text an operator can paste into a ticket.
 *
 * **Evidence has to be able to leave the frame, and it leaves as text.** Measured on the pinned
 * host: no image reaches the clipboard from inside the frame by any route, so a screenshot is not
 * an option and the copied block is the only durable record an operator can carry out. That is why
 * this builder states more than the card renders — the LibreChat account, the conversation
 * reference, the run id and every timestamp in absolute form. A field the card drops because it is
 * noise on screen is exactly the field a ticket needs six months later.
 *
 * **Two flavours, one builder.** `text` and `html` ride on a single copy, and the two must say the
 * same thing: a reader who pastes into a plain-text field and a reader who pastes into a rich one
 * are quoting the same record in a dispute. Producing them from one section list rather than from
 * two writers is what keeps that true — a fact added to one and forgotten in the other cannot
 * happen here, because there is nowhere to add it to only one.
 *
 * **Every timestamp carries `+07:00`** (`lib/format/jakarta-time.ts`). A bare wall-clock time in a
 * ticket is read in whatever zone the reader is in, and this block is meant to be a durable
 * reference rather than a note to somebody sitting in the same office this afternoon. English
 * regardless of the language of the conversation the frame is rendered inside, for the same
 * reason: the audit trail is not translated.
 *
 * **The identity line is the runner's own record, printed as it stands.** Each family fills
 * `delta.identity` with the keys that name its own subject — the account pair sends the server and
 * the username, the interface change sends server, node, vmid, net and action, the mail gateway
 * sends its target both raw and normalised — and the insertion order it arrives in already leads
 * with the machine. A table here mapping each tool to the keys it is expected to carry would be a
 * second copy of five runners' vocabularies with nothing reading it against them: it would print a
 * blank the day a runner adds a key, and print `unknown` for a key it never had. So the record is
 * rendered rather than interpreted, and the per-family shape comes from the party that has it.
 *
 * **`changedFields: null` and `changedFields: []` say different things and are never folded
 * together.** `null` is "nothing was measured" — the write may have landed and no confirming read
 * answered. `[]` is "the runner compared, and nothing moved". Rendering the first as the second
 * tells an operator a machine is untouched on the evidence that NOA failed to look. The same
 * distinction runs through `unanswered`, and there a silent source is named by name: "one backend
 * did not answer" does not say which server to go and look at.
 *
 * Those two facets print in all three states — measured, measured-as-nothing, not measured —
 * where every other absent facet is simply left out. The difference is what the absence means: a
 * missing `listDelta` says this family's change is not list membership, which is nothing to
 * report, while a missing field diff or a missing source accounting is a hole in the measurement
 * of the change itself, and the benign reading of that hole is the one that misleads.
 *
 * **A delivered credential is stated and its link is not.** The delivery URL opens once; pasting
 * it into a ticket hands the credential to whoever reads the ticket first and burns it for the
 * operator who needs it. The summary records that one was delivered, which is the fact an audit
 * needs, and the link stays on the card.
 *
 * **No reason field, here or anywhere near here.** The operator's reason is typed at decision time
 * and travels outward only; the card carries none back and this builder has nowhere to put one.
 *
 * **Every value is HTML-escaped on its way into the `html` flavour**, at one point in
 * `renderHtml`, because the component that copies it hands the string to `dangerouslySetInnerHTML`
 * and the values in it are API-supplied strings.
 */

import type { ApprovalCard, ApprovalReceipt } from '@/lib/approvals/card'
import { statusLabel } from '@/lib/approvals/card'
import type { ChangeDelta } from '@/lib/approvals/delta'
import { formatDuration, formatJakartaOffset } from '@/lib/format/jakarta-time'

/** The one copy, in both flavours the clipboard carries. */
export type Summary = { text: string; html: string }

/** One heading and the self-contained lines under it. Nothing nests — see `renderHtml`. */
type Section = { heading: string; lines: string[] }

const TITLE = 'NOA approval record'

function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/**
 * One value, as a ticket should read it.
 *
 * The empty string is called out rather than printed as nothing: the card parser's fallback for a
 * missing string field is `''`, and a line reading `username=` looks like a rendering bug where it
 * is actually a field the API did not send.
 */
function renderValue(value: unknown): string {
  if (value === null) return 'null'
  if (value === undefined) return 'not recorded'
  if (typeof value === 'string') return value === '' ? '(empty)' : value
  if (typeof value === 'object') return JSON.stringify(value) ?? String(value)
  return String(value)
}

/**
 * A record as `key=value` pairs, in the order it arrived.
 *
 * Not sorted. The order a runner writes its identity in is the order that reads well — the machine
 * first, then what sits on it — and alphabetising would lead an interface change with `action`.
 */
function renderPairs(record: Record<string, unknown>): string {
  const entries = Object.entries(record)
  if (entries.length === 0) return 'not recorded'
  return entries.map(([key, value]) => `${key}=${renderValue(value)}`).join(', ')
}

function changeSection(card: ApprovalCard, delta: ChangeDelta | null): Section {
  return {
    heading: 'Change',
    lines: [
      `Tool: ${renderValue(card.toolName)}`,
      `Status: ${statusLabel(card.status)}`,
      // With no delta there is no measured subject, and the arguments are the only statement of
      // what was aimed at. Labelled as requested rather than as fact: a denied or expired request
      // never ran, and this line must not read as a machine that was touched.
      delta === null
        ? `Target, as requested: ${renderPairs(card.arguments)}`
        : `Target: ${renderPairs(delta.identity)}`,
    ],
  }
}

function movedSection(delta: ChangeDelta | null): Section {
  if (delta === null) {
    return {
      heading: 'What moved',
      lines: ['Not measured. No runner published a delta for this request.'],
    }
  }

  const cause = delta.verificationCause === null ? '' : ` (${delta.verificationCause})`
  const lines = [`Verification: ${delta.verification}${cause}`]

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
    const added = list.added.length === 0 ? 'none' : list.added.join(', ')
    const removed = list.removed.length === 0 ? 'none' : list.removed.join(', ')
    lines.push(`List entries added: ${added}`)
    lines.push(`List entries removed: ${removed}`)
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

  return { heading: 'What moved', lines }
}

function resultSection(receipt: ApprovalReceipt | null, delta: ChangeDelta | null): Section {
  if (receipt === null) {
    return { heading: 'Result', lines: ['Nothing has recorded what this change did.'] }
  }

  const lines = [receipt.ok ? 'The change completed.' : 'The change did not complete.']
  if (receipt.errorCode !== null) lines.push(`Error code: ${receipt.errorCode}`)

  for (const backend of delta?.backends ?? []) {
    const verdict = backend.verdict === null ? '' : `, verdict ${backend.verdict}`
    const error = backend.errorCode === null ? '' : `, error ${backend.errorCode}`
    lines.push(
      `Backend ${backend.name}: ${backend.driven ? 'driven' : 'not driven'}, ` +
        `${backend.answered ? 'answered' : 'no answer'}${verdict}${error}`,
    )
  }

  if (delta !== null) {
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
  }

  return { heading: 'Result', lines }
}

function timingSection(card: ApprovalCard): Section {
  const lines = [
    `Requested: ${formatJakartaOffset(card.createdAt)}`,
    `Approval window ends: ${formatJakartaOffset(card.expiresAt)}`,
    card.decidedAt === null
      ? 'Decided: no decision recorded'
      : `Decided: ${formatJakartaOffset(card.decidedAt)}`,
  ]

  const run = card.run
  if (run === null) {
    lines.push('Run: none started')
    return { heading: 'Timing', lines }
  }

  const elapsed = formatDuration(run.createdAt, run.completedAt)
  lines.push(`Run status: ${renderValue(run.status)}`)
  lines.push(`Run started: ${formatJakartaOffset(run.createdAt)}`)
  lines.push(
    run.completedAt === null
      ? 'Run completed: still running when this was copied'
      : `Run completed: ${formatJakartaOffset(run.completedAt)}${elapsed === null ? '' : ` (${elapsed})`}`,
  )

  return { heading: 'Timing', lines }
}

/**
 * The identifiers the card itself no longer shows.
 *
 * All four are here because all four are what a ticket is searched by later, and none of them is
 * worth the space on a card an operator is reading to make one decision right now.
 */
function referenceSection(card: ApprovalCard): Section {
  return {
    heading: 'Reference',
    lines: [
      `Request id: ${renderValue(card.actionRequestId)}`,
      card.run === null ? 'Run id: none' : `Run id: ${renderValue(card.run.toolRunId)}`,
      `LibreChat account: ${renderValue(card.requester.email)} ` +
        `(user ${renderValue(card.requester.librechatUserId)})`,
      card.conversationRef === null
        ? 'Conversation: not recorded'
        : `Conversation: ${renderValue(card.conversationRef)}`,
    ],
  }
}

function renderText(sections: Section[]): string {
  const blocks = sections.map((section) =>
    [section.heading, ...section.lines.map((line) => `  ${line}`)].join('\n'),
  )
  return [TITLE, ...blocks].join('\n\n')
}

/**
 * The same sections as markup.
 *
 * A list per section rather than a `<pre>`: the flavour exists so a rich-text field renders it as
 * structure, and a `<pre>` pasted into one arrives as a monospace wall no worse and no better than
 * the plain flavour beside it — which would make the second flavour pointless.
 *
 * Escaping is here, once, on the whole line. Every line is assembled from a literal label and
 * API-supplied values, so escaping the finished line covers both, and there is no second place to
 * forget.
 */
function renderHtml(sections: Section[]): string {
  const blocks = sections.map((section) => {
    const items = section.lines.map((line) => `<li>${escapeHtml(line)}</li>`).join('')
    return `<p><strong>${escapeHtml(section.heading)}</strong></p><ul>${items}</ul>`
  })
  return `<div><p><strong>${escapeHtml(TITLE)}</strong></p>${blocks.join('')}</div>`
}

/** The whole record, in both flavours, from one pass over one card. */
export function buildSummary(card: ApprovalCard): Summary {
  const receipt = card.receipt
  const delta = receipt?.delta ?? null

  const sections = [
    changeSection(card, delta),
    movedSection(delta),
    resultSection(receipt, delta),
    timingSection(card),
    referenceSection(card),
  ]

  return { text: renderText(sections), html: renderHtml(sections) }
}
