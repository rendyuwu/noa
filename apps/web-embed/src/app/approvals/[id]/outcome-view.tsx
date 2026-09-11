import type { JSX } from 'react'

import type { ApprovalReceipt } from '@/lib/approvals/card'
import {
  type ChangeDelta,
  VERIFICATION_MISMATCH,
  VERIFICATION_NOT_IN_FORCE,
  VERIFICATION_UNAVAILABLE,
  VERIFICATION_VERIFIED,
} from '@/lib/approvals/delta'

import styles from './card.module.css'

/**
 * What the change did, once something recorded it.
 *
 * **One component owns the whole section.** The card renders this and holds none of the logic,
 * because every rule below is a rule about a *delta* and a rule about a delta has one place to
 * live. Two renderers for one receipt would be two answers to "what does absence look like", free
 * to drift the moment either is edited.
 *
 * **Two halves, never one word.** An operator reads back the state they authorised against *and*
 * what the change did to it, separately — so a failed change still shows its before-state (the
 * card's own block, above this one), and a successful one shows more than "done". The verdict line
 * is a third thing beside them and not a replacement for either.
 *
 * **The delta is the runner's own statement, rendered and never re-derived.** It knows things
 * neither half of the receipt records: which backend answered the confirming read, whether the
 * write landed and the step that puts it into effect did not, whether a credential was delivered.
 * `docs/change-delta.md` is where the shape and the rules a newcomer breaks are written down; this
 * file is the reader that has to hold them at the screen.
 *
 * **Absence is the hard part, and it is three different things.** A facet that is absent renders
 * nothing at all — never "no", never a red cross, because a fabricated negative reads exactly like
 * a measured one. An empty `changedFields` renders an explicit "nothing changed", because that is
 * a claim the runner made and an empty block reads as a rendering failure. And a receipt with no
 * delta at all renders as *that*, never as "nothing changed": a non-answer must not fold into the
 * benign value.
 *
 * `errorCode` is the API's own string, shown verbatim. It is the word an operator will quote to an
 * administrator, and translating it here would make the card and the audit trail disagree.
 */

/** A JSONB value as text. Nested values are shown as JSON rather than dropped. */
export function factText(value: unknown): string {
  if (typeof value === 'string') return value
  // `JSON.stringify` answers `undefined` for `undefined` itself, which would render the word as if
  // a runner had sent it. A key the payload does not carry is unrecorded, and says so.
  return JSON.stringify(value) ?? 'unrecorded'
}

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
 * A flat payload as a fact list.
 *
 * Exported alongside `Fact` so the card's other payload blocks — the arguments, the before-state —
 * can read one definition rather than keeping a second copy of it.
 */
export function FactList({ values }: { values: Record<string, unknown> }): JSX.Element {
  const entries = Object.entries(values)

  if (entries.length === 0) return <p className={styles.empty}>Nothing recorded.</p>

  return (
    <dl className={styles.facts}>
      {entries.map(([key, value]) => (
        <Fact key={key} label={key} value={factText(value)} />
      ))}
    </dl>
  )
}

/**
 * A labelled block of one-line-per-row facts.
 *
 * **One line per row is the layout decision, and it was made for the frame.** Nothing here has to
 * stay aligned in a column, so nothing here can break when the frame is narrow: a long value wraps
 * inside its own line and the line below it is still a whole row. A two-column table of old
 * against new would have had to either scroll sideways or collapse, and both of those are a
 * receipt an operator cannot read in the box LibreChat opens with.
 */
function Lines({ label, items }: { label: string; items: string[] }): JSX.Element {
  return (
    <div className={styles.deltaGroup}>
      <h3 className={styles.sectionTitle}>{label}</h3>
      <ul className={styles.deltaLines}>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  )
}

/**
 * The four states verification has, plus the one this build has never heard of.
 *
 * **Four, not two.** `unavailable` is "NOA holds no measurement" and `mismatch` is "NOA took a
 * measurement and it disagrees" — collapsing those into one "not confirmed" merges a non-answer
 * with a failure, and they send an operator to different places. `not_in_force` is a third thing
 * again: the write landed and the step that applies it did not, which is neither a change nor a
 * refusal, and an operator told "failed" would go and re-add an entry that is already in the
 * config.
 */
const VERIFICATION_TEXT: Record<string, string> = {
  [VERIFICATION_VERIFIED]: 'Verified: NOA read the target back and it agrees.',
  [VERIFICATION_UNAVAILABLE]:
    'Not measured: NOA holds no reading, so this is neither confirmed nor refuted.',
  [VERIFICATION_MISMATCH]: 'Contradicted: NOA read the target back and it disagrees.',
  [VERIFICATION_NOT_IN_FORCE]:
    'Not in force: the change was written and the step that applies it did not run.',
}

/**
 * The sentence for a verification state, or the state itself when it is not one of the four.
 *
 * The fallback is the point, and it is why `verification` is a plain string rather than a union: a
 * state a later API grows must reach the screen **as itself** and be undecidable here. Anything
 * else fails open — an unrecognised value quietly rendered as the verified sentence would be NOA
 * claiming a measurement it does not have.
 */
function verificationText(state: string): string {
  return (
    VERIFICATION_TEXT[state] ??
    `Unrecognised verification state "${state}": this card cannot say what it means.`
  )
}

/**
 * What moved, per field.
 *
 * `null` and `[]` are different claims and this is where the difference is finally visible.
 * `null` is "NOA cannot say" and renders nothing at all; `[]` is "nothing moved, and NOA has
 * grounds for saying so" and renders a sentence, because an empty block reads as a renderer that
 * broke rather than as a measurement.
 *
 * The rows are the runner's own list of what changed — it is the only party that held both
 * vocabularies, the evidence's and the payload's — so nothing is filtered or re-compared here.
 */
function ChangedFields({ rows }: { rows: ChangeDelta['changedFields'] }): JSX.Element | null {
  if (rows === null) return null
  if (rows.length === 0) return <p className={styles.empty}>Nothing changed.</p>

  return (
    <Lines
      label="Fields changed"
      items={rows.map((row) => `${row.field}: ${factText(row.old)} → ${factText(row.new)}`)}
    />
  )
}

/**
 * What entered and left a list.
 *
 * Each line carries the target system's own spelling, never a tidied one: a mail gateway holds
 * `1.2.3.4` and `1.2.3.4/32` as two lines and one entry, so a receipt that said "removed" without
 * saying which line cannot be checked against the box.
 *
 * An empty pair is the same explicit "nothing changed" as an empty `changedFields`, for a runner
 * that re-read and found the world already as the operator wanted it.
 */
function ListDelta({ delta }: { delta: NonNullable<ChangeDelta['listDelta']> }): JSX.Element {
  const moved = [
    ...delta.added.map((entry) => `added ${entry}`),
    ...delta.removed.map((entry) => `removed ${entry}`),
  ]

  return (
    <>
      {moved.length === 0 ? (
        <p className={styles.empty}>Nothing entered or left the list.</p>
      ) : (
        <Lines label="List" items={moved} />
      )}
      {delta.totalEntries === null ? null : (
        <dl className={styles.facts}>
          <Fact label="Entries in the list" value={String(delta.totalEntries)} />
        </dl>
      )}
    </>
  )
}

/**
 * One row per backend, and it renders whether or not the envelope said the change worked.
 *
 * A refused change still measured which backend refused it and which ones answered, and those
 * falses are earned by a postflight that answered rather than assumed by a reader. `driven` and
 * `answered` are two facts about two different moments — a backend that ran the commands and then
 * went silent on the confirming read is the case the pair exists for, and one boolean could not
 * say it.
 */
function Backends({ rows }: { rows: NonNullable<ChangeDelta['backends']> }): JSX.Element {
  /*
   * An empty list is a sentence, and it is deliberately not the same as an absent one.
   *
   * A heading over no rows reads as a renderer that broke, which is why the sibling facets each
   * have their own empty wording. Rendering nothing at all would be worse than either: absent
   * already means "this family has no per-source accounting to make", and `[]` means a change that
   * drove no backend and heard from none — the empty-gather shape the zero-backend rule exists to
   * refuse. Folding those two together would hide the one that matters, which is the same mistake
   * the unanswered fold above is careful not to make in the other direction.
   *
   * Not reachable from a live runner today: the mechanism raises before a delta with no backends
   * can be built. It is on the wire because an empty tuple serialises as `[]`, so the card states
   * it rather than trusting a producer to keep being careful.
   */
  if (rows.length === 0) {
    return <p className={styles.empty}>No backend was driven, and none answered.</p>
  }

  return (
    <Lines
      label="Backends"
      items={rows.map((row) => {
        const said = [
          row.driven ? 'ran the change' : 'not driven',
          row.answered ? 'answered' : 'silent',
        ]
        if (row.verdict !== null) said.push(`says ${row.verdict}`)
        if (row.errorCode !== null) said.push(row.errorCode)
        return `${row.name}: ${said.join(', ')}`
      })}
    />
  )
}

/**
 * Everything the delta states, in the order an operator reads it.
 *
 * **Identity renders whole, and both spellings of a target survive it.** Where the payload carries
 * the operator's typed target *and* the form the target system normalised it to, both are keys and
 * both are shown: the object that was approved is not literally the string that was typed, and a
 * reader that folded the pair into one would be choosing which of the two an operator gets to
 * check against the box.
 */
function Delta({ delta }: { delta: ChangeDelta }): JSX.Element {
  return (
    <>
      <FactList values={delta.identity} />

      <p className={styles.verification} data-noa-verification={delta.verification}>
        {verificationText(delta.verification)}
      </p>
      {/* The named code for why there is no measurement. It cannot ride on a verified delta — that
          is refused where the delta is built — so this is only ever a reason for a non-answer. */}
      {delta.verificationCause === null ? null : (
        <dl className={styles.facts}>
          <Fact label="Because" value={delta.verificationCause} />
        </dl>
      )}

      <ChangedFields rows={delta.changedFields} />
      {delta.listDelta === null ? null : <ListDelta delta={delta.listDelta} />}
      {delta.backends === null ? null : <Backends rows={delta.backends} />}

      {/*
       * Named, never counted: "one backend was silent" does not say which server to go and look at.
       *
       * **Three states, two renderings, and that is deliberate here.** A non-empty list names the
       * sources. `[]` is every source having answered and `null` is a family with no per-source
       * accounting to make — one source, so there is no list to keep — and neither of those is a
       * silent source, so neither has anything to name. What the partial-answer rule forbids is a
       * source that could not answer going unnamed, and that is the non-empty case alone.
       *
       * This is where the card and the copied block deliberately differ, and the difference is the
       * audience: `lib/approvals/summary.ts` prints all three, because a ticket read a year later
       * wants to know whether the accounting was even taken, and an absent one is a hole worth
       * stating there. On a card being read to make one decision now, a line saying nobody was
       * silent is a line that costs a screenshot's worth of height to say nothing happened.
       *
       * The reading rests on something this side does not enforce: that no runner publishes `null`
       * while a source *was* silent. Both firewall runners fill the facet on every branch and the
       * single-source families omit it, so it holds today — but it is their property, not this
       * component's, and nothing here would catch it changing.
       */}
      {delta.unanswered === null || delta.unanswered.length === 0 ? null : (
        <p className={styles.verification}>
          No answer from: {delta.unanswered.join(', ')}. Silence is not evidence of absence.
        </p>
      )}

      {/* A reading that was capped ships its own bound, or "this address was blocked and is now
          allowed" reads as a statement about every line the firewall holds for it. */}
      {delta.bound === null ? null : (
        <dl className={styles.facts}>
          <Fact
            label="Reading covered"
            value={
              delta.bound.truncated
                ? `${delta.bound.total} lines, and the reading was cut short`
                : `${delta.bound.total} lines, all of them`
            }
          />
        </dl>
      )}

      {/* The slot nothing pairs with: NOA never held the old value and must not record the new
          one, so the facet's presence is the whole claim — a credential may now be live. */}
      {delta.deliveredCredential === null ? null : (
        <dl className={styles.facts}>
          <Fact label="Credential delivered" value={delta.deliveredCredential} />
        </dl>
      )}

      {/* A new value with no before twin — a window resolved into an absolute expiry. Not a field
          change, because there was nothing there to change. */}
      {delta.newValues === null ? null : (
        <div className={styles.deltaGroup}>
          <h3 className={styles.sectionTitle}>Now set</h3>
          <FactList values={delta.newValues} />
        </div>
      )}
    </>
  )
}

/**
 * The headline, and it reads the delta rather than the envelope alone.
 *
 * **A third word, because two were a claim NOA cannot make.** `ok: false` means the call did not
 * come back with a success, which is not the same as the change not having happened: a mutation
 * that timed out may well have landed on the far side, and the runner says so by publishing
 * `unavailable` rather than inventing a reading. Headlining that as "did not complete" while the
 * block below says NOA holds no measurement is the card contradicting itself, and an operator
 * reads the top line.
 *
 * **`not_in_force` moves the headline in the other direction**, and it is the same defect
 * mirrored: the envelope says the write succeeded, so the old headline said so too, over a
 * sentence explaining that the step which applies the change never ran. An operator told
 * "completed" does not go and run it.
 *
 * **`mismatch` is named here rather than left to fall through**, and naming it changes nothing for
 * the way it arrives today: a contradicted reading comes with `ok: false`, and "did not complete"
 * is what the envelope said anyway. It changes the other direction. The same delta over a payload
 * reporting success headlined "completed" above a sentence saying NOA read the target back and it
 * disagrees — the same self-contradiction the two states above are here to remove, and the rule is
 * one rule: the headline reads the verification state, not the call's return. A mapping with a
 * hole in it is worse than the mapping, because the next state added gets its shape copied from
 * this one.
 *
 * No delta at all falls through to the envelope, which is the only thing there is to report.
 */
function outcomeText(receipt: ApprovalReceipt): string {
  switch (receipt.delta?.verification) {
    case VERIFICATION_UNAVAILABLE:
      return 'Outcome unknown'
    case VERIFICATION_NOT_IN_FORCE:
      return 'Not in force'
    case VERIFICATION_MISMATCH:
      return 'Did not complete'
    default:
      return receipt.ok ? 'Completed' : 'Did not complete'
  }
}

export function Outcome({ receipt }: { receipt: ApprovalReceipt }): JSX.Element {
  return (
    <section className={styles.section}>
      <h2 className={styles.sectionTitle}>What the change did</h2>
      <dl className={styles.facts}>
        <Fact label="Outcome" value={outcomeText(receipt)} />
        {/*
         * "Error code", not "Reason". In this repository a reason is one thing — the justification
         * an operator types at decision time, which the model never authors, relays or sees — and
         * the admin surface renders that field now. One word must not name two facts. The string
         * itself is the API's and is unchanged.
         */}
        {receipt.errorCode === null ? null : <Fact label="Error code" value={receipt.errorCode} />}
      </dl>

      {receipt.delta === null ? (
        // Not "nothing changed", and the difference is the whole rule: this is NOA having no
        // statement about what moved, which is compatible with a change that landed. The named
        // cause, when there is one, is the error code above.
        <p className={styles.empty}>
          No delta recorded. Nothing states what moved, which is not the same as nothing having
          changed.
        </p>
      ) : (
        <Delta delta={receipt.delta} />
      )}

      <div className={styles.deltaGroup}>
        <h3 className={styles.sectionTitle}>Reported by the runner</h3>
        <FactList values={receipt.after} />
      </div>
    </section>
  )
}
