/**
 * The target system's own text, as the gate read it before the change ran.
 *
 * **Two surfaces draw this block and there is one model of it**, for the reason `verdict.ts` is not
 * a block inside either of them: the card renders it at the screen and the copied summary pastes it
 * into a ticket, and two builders for one reading are two answers to "what did the server say" that
 * drift the moment either is edited. The card and the block must be one statement of one
 * measurement, so both read the strings below rather than composing their own.
 *
 * **The heading is the gate's, never a table in here.** `evidence_heading` is written in Python
 * beside the sentence each runner already composes (`apps/api/src/noa_api/mcp_tools/change_gate.py`,
 * `EVIDENCE_HEADING`) — `Why it was blocked` for the firewall release, `What was allowed` for an
 * allowlist removal, `What was on the list` for a PMG remove. A tool-name-to-heading table here
 * would be a second copy of six runners' vocabularies with nothing reading it against them: a blank
 * the day a tool is added, a stale phrase the day one is reworded.
 *
 * **The key's presence is the decision.** Four of the seven CHANGE tools read structured fields and
 * have no vendor text to head, and a PMG *add* has none either — its matching entries are empty
 * because the address is not on the list, and that emptiness is the before-state. Heading present,
 * draw the block; heading absent, draw nothing at all, never an empty heading over nothing.
 *
 * **Three states, and the middle one is the point.** Lines present is the reading; `matches: []` is
 * the gate having looked and found nothing matching; no `matches` key at all is no reading having
 * been taken. The last two are different claims and must not render as each other — a measurement
 * that came back empty is not a measurement nobody took.
 *
 * **Two of those three are live and the third is defensive, which is worth saying rather than
 * letting the list above read as three equals.** Lines present is the ordinary card. `matches: []`
 * is real and reachable today: the release gate opens on every well-formed call with no
 * already-unblocked short circuit, so an address no backend holds produces an empty reading under
 * the heading the gate wrote — and `Why it was blocked` sitting over "NOA read the server and
 * found no matching lines" is honest but self-contradicting, because the heading is composed
 * before the reading and the reading can refute it. The third, no `matches` key at all, is
 * unreachable from any current writer — both firewall tools fill the key on every branch and PMG
 * always writes its list — so it is handled here on the strength of being representable rather
 * than observed.
 */

import { spoken } from '@/lib/approvals/verdict'

/**
 * The keys this block reads, off `action_requests.approval_context`.
 *
 * Spelled once here rather than at each reader: they are JSONB written minutes earlier by Python,
 * so a misspelling reads as an absent key and the block silently stops being drawn.
 */
const HEADING_KEY = 'evidence_heading'
const MATCHES_KEY = 'matches'
const TOTAL_KEY = 'total_matches'
const TRUNCATED_KEY = 'truncated'

/**
 * Where the firewall pair keeps its reading. PMG puts the same two keys at the top level.
 *
 * One lookup rather than a per-tool branch: the nested record is either there or it is not, and
 * that is a fact about the payload rather than a fact about which tool wrote it.
 */
const FIREWALL_KEY = 'firewall'

/**
 * The provenance of the reading, and it is the whole reason the block is trustworthy: these lines
 * are the gate-time preflight the operator authorised against, not a read taken afterwards.
 */
const READ_BEFORE = 'Read from the server before the change ran'

/** `matches: []` — the gate read the server and nothing matched. A claim, not a hole. */
const NO_MATCHING_LINES = 'NOA read the server and found no matching lines.'

/** No `matches` key at all. Never the sentence above: nothing was read, so nothing is claimed. */
const NO_READING = 'NOA has no reading of the server to show here.'

export type EvidenceBlock = {
  /** The gate's own words for what this block answers. */
  heading: string
  /** The target system's lines, verbatim and uncut. `null` when there are none to show. */
  lines: string[] | null
  /** Which of the two no-lines states this is. `null` when `lines` carries the reading. */
  note: string | null
  /** The provenance line, carrying the cap when — and only when — the reading was capped. */
  closing: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/**
 * One line as the target system spelled it.
 *
 * Two shapes reach here and both come from NOA's own gate. The firewall pair publishes plain
 * strings (`apps/api/src/noa_api/mcp_tools/whm_firewall.py`, `BackendLookup.matches` is a
 * `list[str]`), already cut of NOA's own comment text at the one seam every evidence line crosses.
 * PMG publishes a record per entry whose `cidr` is the string `pmgsh ls` printed, beside the
 * canonical form membership was decided on (`core/integrations/pmg/mynetworks.py`). The target
 * system's own spelling is the one an operator can check against the box, so `cidr` is the line and
 * the normalised twin is not.
 *
 * Anything else renders as JSON rather than being dropped: a line this app cannot read is still a
 * line the gate read from the server, and dropping it would understate the reading the decision
 * rests on.
 */
function evidenceLine(value: unknown): string {
  if (typeof value === 'string') return value
  if (isRecord(value) && typeof value['cidr'] === 'string') return value['cidr']
  // `JSON.stringify` answers `undefined` for `undefined` itself, which would print the word as if a
  // gate had sent it.
  return JSON.stringify(value) ?? 'unrecorded'
}

/**
 * The closing line: provenance always, and the cap only where there was one.
 *
 * **A capped reading states its cap; an uncapped one says nothing at all.** `truncated: false` adds
 * no words — a sentence saying the reading was complete on every uncapped block would read as
 * furniture within two cards, and the property that matters is the one that fires.
 *
 * A `truncated` with no readable total still says it was cut, without a number. Stating the cap
 * without inventing its size is the honest direction; silently dropping the whole clause because
 * one field was unreadable is how an honesty property leaves without anyone deciding it should.
 */
function closingLine(shown: number | null, total: unknown, truncated: boolean): string {
  if (!truncated) return `${READ_BEFORE}.`
  if (shown === null || typeof total !== 'number') return `${READ_BEFORE}; the reading was cut short.`
  return `${READ_BEFORE}; this is the first ${shown} of ${total} lines.`
}

/** The block for a card's evidence, or `null` when the gate published no heading for one. */
export function evidenceBlock(evidence: Record<string, unknown>): EvidenceBlock | null {
  const heading = spoken(evidence[HEADING_KEY])
  if (heading === null) return null

  const firewall = evidence[FIREWALL_KEY]
  const read = isRecord(firewall) ? firewall : evidence
  const matches = read[MATCHES_KEY]
  const lines = Array.isArray(matches) ? matches.map(evidenceLine) : null

  return {
    heading,
    lines: lines !== null && lines.length > 0 ? lines : null,
    note: noteFor(lines),
    // The cap rides on the reading it bounds: `truncated` beside no lines at all would print
    // "the first 0 of 34" under "NOA read the server and found no matching lines", two claims
    // about one reading that contradict each other. Unreachable from today's writers — the
    // firewall pair derives `truncated` from the same lookups it writes `matches` from — and
    // guarded here anyway, because which of them holds is the producer's property and not this
    // reader's to assume.
    closing: closingLine(
      lines?.length ?? null,
      read[TOTAL_KEY],
      lines !== null && lines.length > 0 && read[TRUNCATED_KEY] === true,
    ),
  }
}

/** Which of the two empty states this is, or `null` when there are lines to show instead. */
function noteFor(lines: string[] | null): string | null {
  if (lines === null) return NO_READING
  return lines.length === 0 ? NO_MATCHING_LINES : null
}

/** The block as the lines a ticket carries: the reading (or why there is none), then its provenance. */
export function evidenceBlockLines(block: EvidenceBlock): string[] {
  return [...(block.lines ?? [block.note ?? NO_READING]), block.closing]
}
