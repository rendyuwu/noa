/**
 * What every surface of one card says, resolved once.
 *
 * **The card and the block copied off it are one statement of one measurement.** That is the
 * argument `verdict.ts` already makes for the verdict words, applied to the whole body: the screen
 * and the ticket must not be two readings of one moment, so both render the strings below rather
 * than each assembling their own. A test compares the two bodies byte for byte, and it can only do
 * that because there is one place they both come from.
 *
 * **No text here is authored at runtime.** The headline and the sentence are the runner's own bytes
 * and the imperative is the gate's, each composed in Python beside the family that holds its
 * vocabulary. What this file contributes is the `Asked: ` label, which is the card's chrome rather
 * than a claim about a machine, and the order the four things are read in.
 *
 * **`Asked: <imperative>`, never `<subject> will <verb>`.** The gate composed an imperative on
 * purpose (`apps/api/src/noa_api/mcp_tools/change_gate.py`, `EVIDENCE_ASKED`): "remove
 * 203.0.113.24 from the deny lists" is the arguments restated, and "203.0.113.24 will be unblocked"
 * is a claim with nothing measured behind it. The grammar is the whole difference, and it is why
 * the sentence is rendered rather than re-worded here.
 */

import { type ApprovalCard, statusLabel } from '@/lib/approvals/card'
import { type EvidenceBlock, evidenceBlock } from '@/lib/approvals/evidence'
import { isRunning } from '@/lib/approvals/poll'
import { cardHeadline, outcomeCorner, runnerSentence, spoken } from '@/lib/approvals/verdict'

/** The gate's restatement of the request, as an imperative. */
const ASKED_KEY = 'asked'

/** The machine the change is about, on every one of the seven gates' evidence. */
const SERVER_KEY = 'server'

/**
 * The label over the gate's imperative.
 *
 * The card's own word rather than the gate's, and the one string on these surfaces that is neither
 * measured nor quoted: it names which of the two kinds of sentence this is. A completed card shows
 * the runner's own words unlabelled, because what a change *did* needs no framing.
 */
const ASKED_LABEL = 'Asked: '

export type CardBody = {
  /** The change in the operator's words — the runner's headline, the gate's, or the tool label. */
  headline: string
  /** The decision, and what the change then did. */
  corner: string
  /** The machine, as the gate named it. `null` when the evidence does not name one. */
  server: string | null
  /**
   * The one paragraph under the heading: what the runner said once a run recorded something, and
   * what was asked until then.
   *
   * One slot rather than two, because the two never both apply. A card with a runner's sentence has
   * an answer to show and the request is behind it; a card without one has only the request. A
   * PENDING card is always the second, and so is a denied one — nothing ran.
   */
  statement: string | null
  /** The target system's own text under the gate's heading. `null` when the gate published none. */
  evidence: EvidenceBlock | null
}

/** Everything one card states, in the order it is read. */
export function cardBody(card: ApprovalCard): CardBody {
  const receipt = card.receipt
  const asked = spoken(card.evidence[ASKED_KEY])

  return {
    headline: cardHeadline(receipt?.after ?? null, card.evidence, card.toolName),
    corner: outcomeCorner({
      status: card.status,
      statusLabel: statusLabel(card.status),
      receipt,
      running: isRunning(card),
    }),
    server: spoken(card.evidence[SERVER_KEY]),
    statement:
      (receipt === null ? null : runnerSentence(receipt.after)) ??
      (asked === null ? null : `${ASKED_LABEL}${asked}`),
    // The gate's own evidence on both surfaces and in both states, never `receipt.before` once
    // there is a receipt: the two are the same payload copied at execution time, and a card whose
    // evidence changed between deciding and reading back would be two readings of one moment.
    evidence: evidenceBlock(card.evidence),
  }
}
