'use client'

import { useState } from 'react'

import { type DecisionKind, submitDecision } from '@/lib/approvals/decide'
import { type DecisionOutcome, describeDecision } from '@/lib/approvals/outcome'

// `.actions` and `.button` live with the notice states since the table surface hoisted them;
// the two
// colour modifiers below are the card's own, so the class names compose across both modules.
import shared from '@/components/notice.module.css'

import styles from './card.module.css'

/**
 * The reason box and the two buttons.
 *
 * **The only client component on this card.** Everything else is rendered on the server, so what
 * ships to the browser is this: two `<button type="button">` elements, a `<textarea>`, and one
 * `fetch`.
 *
 * **There is no `<form>` in this tree, and that is load-bearing.** The sandbox LibreChat
 * renders the frame under omits `allow-forms` (measured live at the render gate), so a native
 * submit would do nothing at all — no request, no error, a button that lies. `type="button"` on
 * both is the same rule stated twice: even if a `<form>` were introduced above this component by
 * some future layout, neither button would submit it.
 *
 * **The reason is typed here and nowhere else**. It is not in any tool schema, the
 * model never authors it, never relays it and never sees it; it is born at this keystroke and
 * travels in the POST body. Blankness is the endpoint's judgement, not this component's — see
 * `lib/approvals/decide.ts` for why a second definition of "blank" is the wrong trade.
 */
export function DecisionControls({
  actionRequestId,
  csrf,
}: {
  actionRequestId: string
  /** A live token. The server renders this component only when there is one. */
  csrf: string
}) {
  const [reason, setReason] = useState('')
  const [pending, setPending] = useState<DecisionKind | null>(null)
  const [outcome, setOutcome] = useState<DecisionOutcome | null>(null)

  // A recorded decision is terminal for this card: exactly one `pending → decided` transition
  // exists, so leaving the buttons live afterwards would only ever earn a 409. A *refusal*
  // leaves them live on purpose — a blank reason is fixed by typing one and clicking again.
  const settled = outcome?.kind === 'recorded'

  async function decide(decision: DecisionKind) {
    if (pending !== null || settled) return

    setPending(decision)
    setOutcome(null)
    try {
      setOutcome(await submitDecision({ actionRequestId, decision, reason, csrf }))
    } finally {
      setPending(null)
    }
  }

  return (
    <div className={styles.section}>
      <label className={styles.reasonLabel} htmlFor="approval-reason">
        Why is this change being made or refused?
        <textarea
          id="approval-reason"
          name="reason"
          className={styles.reason}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          disabled={settled}
          // Not `required`: constraint validation belongs to a form submit, and there is none
          // here. The endpoint answers 409 `change_reason_required` and the card shows it.
          placeholder="Required. This is recorded with the decision."
        />
      </label>

      <div className={shared.actions}>
        <button
          type="button"
          className={`${shared.button} ${styles.approve}`}
          onClick={() => void decide('approve')}
          disabled={pending !== null || settled}
        >
          {pending === 'approve' ? 'Approving…' : 'Approve'}
        </button>
        <button
          type="button"
          className={`${shared.button} ${styles.deny}`}
          onClick={() => void decide('deny')}
          disabled={pending !== null || settled}
        >
          {pending === 'deny' ? 'Denying…' : 'Deny'}
        </button>
      </div>

      {outcome ? (
        <p className={styles.outcome} role="status">
          {describeDecision(outcome)}
        </p>
      ) : null}
    </div>
  )
}
