'use client'

import { useEffect, useRef, useState } from 'react'

import { cardBody } from '@/lib/approvals/body'
import { type ApprovalCard, type ApprovalCardLoad, canDecide } from '@/lib/approvals/card'
import { fetchApprovalCard, isRunning, isStalled, isTerminal, pollIntervalMs } from '@/lib/approvals/poll'
import { buildSummary } from '@/lib/approvals/summary'
import { CARD_FRAME_POLICY } from '@/lib/embed/frame-size'
import { formatRelative } from '@/lib/format/jakarta-time'

import { DecisionControls } from './decision-controls'
// The pieces this card is drawn from live there so the completed card and the pending one are one
// renderer per piece rather than two — see that file for where the rows this card used to carry went.
import { EvidenceBlockView, Fact, Outcome, Statement } from './outcome-view'
import { CopySummary } from '@/components/copy-summary'
import { FrameSizer } from '@/components/frame-sizer'
import { Notice } from '@/components/notice'
import { SignInNotice } from '@/components/sign-in-notice'
import styles from './card.module.css'

/**
 * The card itself, and the loop that keeps it true.
 *
 * **Seeded by the server, not fetched by the browser.** `page.tsx` reads the card with the incoming
 * cookie and hands the result here as `initial`, so the HTML that reaches the frame is already the
 * authenticated card — there is no moment where an operator watches an empty box while a client
 * fetch decides who they are, and the CSRF token is part of the render rather than something a page
 * could ask for without having been allowed to read the request first.
 *
 * **Then it asks again, because the state lives in the database**. Approve returns 202 and the
 * change runs elsewhere; the only way for this frame to learn the outcome is to re-read the row.
 * `/approvals/[id]` owns the whole lifecycle of one request, and a card that showed the
 * question but never the answer would own half of it. The answer arrives in two parts — the run's
 * terminal status, and the receipt saying what it did — and the second is what a
 * poll is actually waiting for.
 *
 * **A poll can discover every state the first read could.** A session that expires under an open
 * frame answers 401, and that renders the 401 card's explicit "cannot authenticate here" rather
 * than leaving a live Approve button standing on a card nobody may decide any more — with the
 * escape hatch's way out of it beside it (`sign-in-notice.tsx`), which is a link-out and a retry
 * and never a form in the frame. A transient failure is the one answer that changes nothing: the
 * card stays, the loop stays,
 * because "NOA could not be reached just now" is not "there is nothing more to wait for".
 *
 * **It asks the host for a frame it fits in** (`components/frame-sizer.tsx`). The box LibreChat
 * opens with is small and fixed, and this card has to be worth one screenshot: one shot, no
 * scrolling, legible at the frame's width. So the card measures itself and posts its content
 * height, which the host applies verbatim — measured, and there is no host-side clamp, so the
 * ceiling in `lib/embed/frame-size.ts` is a rail this app holds rather than one the host holds for
 * it. Every state below that is a section appearing (the evidence block, the run id, the delivered
 * credential) is also a new height, which is why the measurement is re-taken on render rather than
 * once at mount.
 *
 * This is a client component and everything it renders is inside it, so the card has exactly one
 * renderer. A live region updated separately from a server-rendered one would be two descriptions
 * of one row, free to disagree the moment either is edited.
 */

/** What the loop carries between ticks: the last answer, and how long a run has been watched. */
type LiveCard = {
  load: ApprovalCardLoad
  /** Consecutive polls that found a run still in flight. Reset the moment one does not. */
  runPolls: number
}

/**
 * Who asked, when, and the one identifier support asks for.
 *
 * The requester is the identity the gate persisted at request time, not a join done now — the
 * requester FK is `SET NULL`, so a deleted operator would otherwise erase the identity from a
 * decision that was made.
 *
 * **Times are read the way an operator reads them.** "17 minutes ago" is what a decision turns on;
 * the exact instant is one hover away in `title` and, for the operator who has to quote it, in the
 * copied block under a heading naming the zone.
 *
 * **`Expires` is gone from this block, and the fact moved rather than left.** How long there is to
 * answer now sits beside the buttons as a sentence, because two clocks were on one screen with
 * nothing saying they were different clocks: this one is the approval window and the other is the
 * firewall entry's own duration, which the gate's `asked` sentence states in the minutes the schema
 * takes. The absolute end of the approval window is in the copied block, which has a heading naming
 * the zone; a bare stamp may not leave this frame without one.
 *
 * **The run id is here and only once a run exists.** It is what the audit list is keyed on, so it
 * is the one string that gets an operator who cannot open `/admin` an answer from somebody who can.
 * A run id exists only once a run has started, and nothing is substituted while there is none: a
 * placeholder reading `none` states an identifier that does not exist, and the action-request id is
 * deliberately not put in its place — it is a different identifier for a different row.
 *
 * The LibreChat account id and the conversation reference are on no surface here and are reached
 * through `/admin`, which is where they identify the request.
 */
function Provenance({ card }: { card: ApprovalCard }) {
  return (
    <section className={styles.section}>
      <h2 className={styles.sectionTitle}>Requested</h2>
      <dl className={styles.facts}>
        <Fact label="Requested by" value={card.requester.email || 'unrecorded'} />
        <Fact label="Opened" value={formatRelative(card.createdAt)} title={card.createdAt} />
        {card.decidedAt === null ? null : (
          <Fact label="Answered" value={formatRelative(card.decidedAt)} title={card.decidedAt} />
        )}
        {card.run === null ? null : <Fact label="Run id" value={card.run.toolRunId} />}
      </dl>
    </section>
  )
}

function Card({
  card,
  stalled,
  frameOrigin,
  onRecorded,
}: {
  card: ApprovalCard
  stalled: boolean
  frameOrigin: string | null
  /** The card's own reader, handed down so a decision lands on the card without waiting for a poll. */
  onRecorded: () => Promise<unknown>
}) {
  const scrollContainer = useRef<HTMLElement>(null)
  // Every word this card and the block copied off it say, resolved once so the two cannot be two
  // readings of one moment (`lib/approvals/body.ts`).
  const body = cardBody(card)

  return (
    <>
      <main className={styles.card} ref={scrollContainer}>
        <header className={styles.header}>
          {/* The change in the operator's words, composed by the runner or by the gate. The raw
              tool name stays in `title`: it is what an operator quotes to an administrator and what
              `/admin` shows, so a card that renders a headline has to keep the name reachable from
              the same element. */}
          <h1 className={styles.tool} title={card.toolName}>
            {body.headline}
          </h1>
          <div className={styles.corner}>
            <p className={styles.status}>{body.corner}</p>
            {/* The machine, because two of the seven families name only the node in their `asked`
                sentence and an operator reading "disable net0 on VM 108" cannot tell which
                Proxmox it is on. Absent where the gate named none rather than guessed at. */}
            {body.server === null ? null : <p className={styles.status}>{body.server}</p>}
          </div>
          <CopySummary summary={buildSummary(card)} />
        </header>

        {/* What the runner said, or what was asked until a run has said anything. Nothing predicted
            joins it while the request is PENDING: the delta is written at execution time, so at
            that point nothing has measured what the change will do, and a templated sentence would
            be a claim with no measurement behind it. */}
        {body.statement === null ? null : <Statement text={body.statement} />}
        {body.evidence === null ? null : <EvidenceBlockView block={body.evidence} />}

        <Provenance card={card} />
        {card.receipt === null ? null : <Outcome receipt={card.receipt} />}

        {stalled ? (
          // The card gave up asking. Not an error — the run may still be going — so it says what is
          // true and what to do about it, rather than reporting a failure NOA has no evidence of.
          // The corner says `running` for as long as this frame is watching; this is what the
          // corner stops being able to tell an operator once the loop stops.
          <p className={styles.empty} role="status">
            NOA is still running this change. Reload this card to check again.
          </p>
        ) : null}

        {canDecide(card) && card.csrf ? (
          <DecisionControls
            actionRequestId={card.actionRequestId}
            csrf={card.csrf}
            expiresAt={card.expiresAt}
            onRecorded={onRecorded}
          />
        ) : (
          // No reason box and no buttons once nothing may be decided (the explicit-state family): a live
          // Approve button over a decided or expired request is an action that was never
          // available, shown as one that was refused.
          <p className={styles.empty}>
            This request is no longer awaiting a decision, so it cannot be answered from here.
          </p>
        )}
      </main>
      {/* Outside `main` deliberately: a sizer that was a flex child of the box it measures would
          collect a `gap` of its own and change the number it reports. */}
      <FrameSizer
        container={scrollContainer}
        policy={CARD_FRAME_POLICY}
        targetOrigin={frameOrigin}
      />
    </>
  )
}

export function CardView({
  initial,
  actionRequestId,
  signInUrl,
  frameOrigin,
}: {
  initial: ApprovalCardLoad
  /**
   * The id from the URL, not from the card.
   *
   * A 401 or a 404 carries no card and therefore no id, so a retry offered from one of those states
   * would have nothing to re-read — this is the page's own parameter, which is true whatever the
   * last read answered.
   */
  actionRequestId: string
  /** Where an operator signs in, or `null` when nothing usable is configured (`lib/sign-in.ts`). */
  signInUrl: string | null
  /**
   * The origin the host frames this card from, or `null` when there is not a trustworthy one
   * (`lib/embed/frame-origin.ts`). `null` means the frame keeps whatever height the host gave it
   * and the card scrolls itself, which is what it does today.
   */
  frameOrigin: string | null
}) {
  const [live, setLive] = useState<LiveCard>({ load: initial, runPolls: 0 })

  useEffect(() => {
    const { load, runPolls } = live
    if (load.kind !== 'card') return

    const card = load.card
    if (isTerminal(card) || isStalled(card, runPolls)) return

    // A chained timeout rather than an interval: two polls can never overlap, and the wait can
    // change with the phase — 15s while a request sits PENDING, 2s while a run is in flight.
    let active = true
    const timer = setTimeout(() => {
      void fetchApprovalCard(card.actionRequestId).then((next) => {
        // The cleanup already ran: this answer belongs to a card that is no longer on screen.
        if (!active) return

        setLive((previous) => ({
          // A transient failure leaves the card exactly as it was and keeps the loop alive; a 401
          // or a 404 replaces it, because those are states an operator has to be shown.
          load: next.kind === 'unavailable' ? previous.load : next,
          runPolls: next.kind === 'card' && isRunning(next.card) ? previous.runPolls + 1 : 0,
        }))
      })
    }, pollIntervalMs(card))

    return () => {
      active = false
      clearTimeout(timer)
    }
    // A new object every tick, including the ticks that change nothing — that identity is what
    // re-arms the timer, so a poll answering "unavailable" is retried rather than ending the loop.
  }, [live])

  /**
   * One read, on demand, from the state that has nothing to poll.
   *
   * The poll's reader, not a second one — and the poll's rule for what to do with the answer:
   * "NOA could not be reached just now" leaves the operator looking at the notice they were already
   * looking at, and anything else replaces it. A `card` answer re-arms the loop by itself, because
   * the effect above watches `live`.
   */
  async function retryRead(): Promise<ApprovalCardLoad> {
    const next = await fetchApprovalCard(actionRequestId)
    setLive((previous) => ({
      load: next.kind === 'unavailable' ? previous.load : next,
      runPolls: 0,
    }))
    return next
  }

  const { load, runPolls } = live

  if (load.kind === 'card') {
    return (
      <Card
        card={load.card}
        stalled={isStalled(load.card, runPolls)}
        frameOrigin={frameOrigin}
        onRecorded={retryRead}
      />
    )
  }

  // Every state below is a notice, and each one carries `frameOrigin` for the same reason the card
  // above does: it measures itself and asks the host for a frame it fits in. The 401 is the one that
  // needs it — its printed address is what the escape-hatch rule requires, and at the box the host
  // opens with that address started below the fold (`components/notice.tsx`).
  if (load.kind === 'unauthenticated') {
    // An explicit state, never a blank card and never a live Approve button — and the escape
    // hatch's way out of it, which is a top-level link-out plus a retry. This app has no login
    // page and no LDAP form, and nothing in that notice navigates the frame.
    return <SignInNotice signInUrl={signInUrl} onRetry={retryRead} frameOrigin={frameOrigin} />
  }

  if (load.kind === 'not-found') {
    // The requester-match: absent, another operator's, and one whose requester was deleted all
    // answer alike — the API gives one body for the three, and this page gives one sentence.
    return (
      <Notice
        title="Request not available"
        body="This approval request does not exist, or it is not yours to decide."
        frameOrigin={frameOrigin}
      />
    )
  }

  return (
    <Notice
      title="Could not load this request"
      body="NOA could not be reached, or answered unexpectedly. Reload this card; contact an administrator if it continues."
      frameOrigin={frameOrigin}
    />
  )
}
