/**
 * The sections the copied approval record is built out of.
 *
 * `summary.ts` owns the two flavours a clipboard carries and the one pass that assembles them.
 * This owns what each section says. Split for the 300-line cap `apps/api/tests/test_config.py`
 * enforces over `git ls-files`: a section whose wording cannot be corrected without reddening an
 * unrelated suite is a section that stays wrong.
 *
 * **The block and the card are one statement of one measurement.** Every word of the body below
 * comes out of `lib/approvals/body.ts`, which the card renders too — the headline, the corner, the
 * sentence, the evidence block's heading, its lines and its closing provenance line. Nothing here
 * re-words any of them, and a case in `summary.test.ts` compares the two bodies byte for byte.
 * That is the argument `verdict.ts` already makes for the verdict words, applied to the whole body.
 *
 * **What this block carries that the card does not** is the absolute stamps and the identity a
 * ticket is answered from: the card renders relative times because a decision turns on them, and a
 * bare wall-clock stamp may leave the frame only under a heading naming the zone, which is what
 * `WHEN_HEADING` is for.
 *
 * **What it deliberately does not carry.** The delivered credential's link stays on the card: a
 * reusable link is readable by everyone who reads the ticket it was pasted into, until it expires.
 * That one *was* delivered is in the runner's own sentence, which is composed where the deployment
 * settings governing it are legible.
 *
 * **No reason field, here or anywhere near here.** The operator's reason is typed at decision time
 * and travels outward only; the card carries none back and these builders have nowhere to put one.
 */

import type { CardBody } from '@/lib/approvals/body'
import type { ApprovalCard } from '@/lib/approvals/card'
import { evidenceBlockLines } from '@/lib/approvals/evidence'
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

/** Label-and-value rows, padded so a reader scanning the values reads down one column. */
function renderRows(rows: readonly (readonly [string, string])[]): string[] {
  const width = Math.max(...rows.map(([label]) => label.length))
  return rows.map(([label, value]) => `${label.padEnd(width)} ${value}`)
}

/**
 * The two facts an approval is skimmed for, and the sentence under them.
 *
 * The heading is the card's own heading and corner, joined: the decision an operator made and what
 * the change then did. "Approved" alone over a change NOA could not read back folds a non-answer
 * into the benign value, and this is the line a ticket is skimmed for.
 */
export function headlineSection(body: CardBody): Section {
  return {
    heading: `${body.headline} — ${body.corner}`,
    lines: body.statement === null ? [] : [body.statement],
  }
}

/**
 * The target system's own text, exactly as the card draws it.
 *
 * `null` where the gate published no heading, and the caller then emits no section at all rather
 * than an empty one — the same decision the card makes from the same key.
 */
export function evidenceSection(body: CardBody): Section | null {
  const block = body.evidence
  if (block === null) return null

  return { heading: block.heading, lines: evidenceBlockLines(block) }
}

/**
 * Three stamps: asked for, decided, finished — plus the end of the approval window while there is
 * one.
 *
 * `Approval window ends` prints on a PENDING request and nowhere else, and the gate is the status
 * rather than a missing decision stamp: the expiry sweep writes `decided_at` when it flips a row
 * (`core/approvals/expiry.py`; an expiry is a decision the clock made), so a `decidedAt === null`
 * condition would drop the line from exactly the state it exists for.
 *
 * This is the surface that may state the window as an instant. The card states it as a span beside
 * the buttons instead, because a bare stamp leaving the frame needs a heading naming the zone and
 * the card has none to give it.
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
 * Who to ask, and the one identifier to quote.
 *
 * The action-request id, the conversation reference and the LibreChat account all render in the
 * admin drawer (`apps/admin-web/src/components/admin/audit/action-request-detail-drawer.tsx`) for
 * whoever can open it, and the audit list is keyed on the run id. So the run id is the one string
 * that gets an operator who cannot open that panel an answer from somebody who can.
 *
 * **Nothing is substituted while there is no run.** A run id exists only once a run has started,
 * and a row reading `none` states an identifier that does not exist; the action-request id is
 * deliberately not put in its place, being a different identifier for a different row. So a PENDING
 * request's block carries the requester alone.
 */
export function supportSection(card: ApprovalCard): Section {
  const rows: [string, string][] = [['Requested by:', card.requester.email || 'unrecorded']]
  if (card.run !== null) rows.push(['Run id:', card.run.toolRunId])

  return { heading: 'For support', lines: renderRows(rows) }
}
