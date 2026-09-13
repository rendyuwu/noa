import type { JSX } from 'react'

import type { ApprovalReceipt } from '@/lib/approvals/card'
import type { EvidenceBlock } from '@/lib/approvals/evidence'

import styles from './card.module.css'

/**
 * The pieces a card is drawn from, and the one block only a finished change has.
 *
 * **What used to be here is not lost, it moved to a plainer sentence.** This section once carried a
 * verdict word, a verification sentence, the per-backend rows, the named silent sources, the
 * evidence bound, the error code, the field diffs and a dump of every `after` key the blocks above
 * had not stated. Each of those facts now reaches an operator somewhere it reads as English:
 *
 * - the names of the sources that went silent are **in the runner's own sentence**, named rather
 *   than counted, composed in Python beside the family that knows them;
 * - the cap on a capped reading is **on the evidence block's closing line** below;
 * - the `null` / `()` split — a value NOA never held against one the target system holds as empty —
 *   is **in the before-clause the runner composes**, for the same reason;
 * - the four verification states are **in the corner** (`lib/approvals/verdict.ts`), all four still
 *   distinguishable, including a fifth this build has never heard of.
 *
 * Everything that is no longer drawn anywhere on this surface is still in the database, still in
 * the API's body, and still rendered whole on `/admin`
 * (`apps/admin-web/src/components/admin/audit/action-request-detail-drawer.tsx` shows the approval
 * context and all three receipt halves as JSON). Only the surface printing it changed.
 *
 * **Nothing here writes a sentence about a change.** Every string a card shows is the runner's, the
 * gate's, or the target system's own text; what this file owns is where each one sits.
 */

export function Fact({
  label,
  value,
  title,
}: {
  label: string
  value: string
  /**
   * The exact value behind a value that was rendered for reading — the absolute timestamp under a
   * relative one. Optional, and it rides on the value rather than the label because it is the
   * value it explains. Never the only place a fact appears: a `title` is a weak hover affordance,
   * so anything it carries is also in the block an operator copies out of the frame.
   */
  title?: string
}): JSX.Element {
  return (
    <>
      <dt className={styles.factKey}>{label}</dt>
      <dd className={styles.factValue} title={title}>
        {value}
      </dd>
    </>
  )
}

/**
 * The one paragraph under the heading: the runner's own words, or the gate's restatement of the
 * request until a run has any.
 *
 * **Byte for byte, and the stylesheet is what makes that possible.** Several runner branches put a
 * `\n` inside `message` — four of the five composing families spell it that way — and a plain `<p>`
 * collapses it to a space, so the break the runner wrote simply did not appear. `white-space:
 * pre-line` on this class renders it, without this component touching the string: splitting the
 * sentence here would be the renderer deciding where a runner's sentence ends. The copied block
 * makes the same repair at its own seam (`lib/approvals/summary.ts`), so the two halves of one
 * measurement agree about it.
 *
 * Body font rather than monospace, which is the rule every sentence on this card follows: these are
 * read, not checked character by character against a target system the way an evidence line is.
 */
export function Statement({ text }: { text: string }): JSX.Element {
  return (
    <p className={styles.verification} data-noa-statement>
      {text}
    </p>
  )
}

/**
 * The target system's own text, under the gate's own heading.
 *
 * **One block, two surfaces, one source.** The model is `lib/approvals/evidence.ts` and both this
 * and the copied summary render the strings it resolved — the heading, the verbatim lines, the
 * closing provenance line, and the cap appended to it only where the reading was capped. Two
 * builders for one reading would be two answers to what the server said.
 *
 * **The lines are monospace and one per row**, sharing the class the delta lines used: these are
 * the strings an operator checks character by character against the box, and one row per line is
 * what lets a long one wrap inside itself instead of scrolling the card sideways at a narrow frame
 * (bound in `e2e/card-evidence.browser.e2e.ts`).
 *
 * The two empty states render as the sentence the model chose between, never as an empty list: a
 * heading over no rows reads as a renderer that broke rather than as a reading that came back
 * empty, and a reading that came back empty is not one nobody took.
 */
export function EvidenceBlockView({ block }: { block: EvidenceBlock }): JSX.Element {
  return (
    <section className={styles.section} data-noa-evidence>
      <h2 className={styles.sectionTitle}>{block.heading}</h2>
      {block.lines === null ? (
        <p className={styles.empty}>{block.note}</p>
      ) : (
        <ul className={styles.evidenceLines}>
          {block.lines.map((line, index) => (
            // The index is part of the key because a target system may hold one line twice — a
            // firewall reading concatenates two backends' matches, and both can carry the same
            // rule. Dropping one as a duplicate key would understate the reading.
            <li key={`${index}-${line}`}>{line}</li>
          ))}
        </ul>
      )}
      <p className={styles.empty}>{block.closing}</p>
    </section>
  )
}

/**
 * What a finished change adds to the card that a pending one cannot have.
 *
 * One row today, and it is the row with a rule of its own: a credential NOA generated may now be
 * live, and the link that delivers it belongs on the screen of the operator who asked for it and
 * **never** in the block they paste into a ticket (`lib/approvals/summary-sections.ts` states the
 * reason — a reusable link is readable by everyone who reads that ticket until it expires). The
 * facet's presence is the whole claim: NOA never held the old value and must not record the new
 * one, so there is nothing to pair it with.
 *
 * It used to render here twice — once as this row and again as a `yopass_url` key in the dump of
 * everything the runner reported. That dump is gone, and with it the second copy.
 */
export function Outcome({ receipt }: { receipt: ApprovalReceipt }): JSX.Element | null {
  const credential = receipt.delta?.deliveredCredential ?? null
  if (credential === null) return null

  return (
    <dl className={styles.facts}>
      <Fact label="Credential delivered" value={credential} />
    </dl>
  )
}
