'use client'

import { useEffect, useState } from 'react'

import {
  type ApprovalCard,
  type ApprovalCardLoad,
  type ApprovalReceipt,
  canDecide,
  statusLabel,
} from '@/lib/approvals/card'
import { fetchApprovalCard, isRunning, isStalled, isTerminal, pollIntervalMs } from '@/lib/approvals/poll'

import { DecisionControls } from './decision-controls'
import { Notice } from '@/components/notice'
import { SignInNotice } from '@/components/sign-in-notice'
import styles from './card.module.css'

/**
 * The card itself, and the loop that keeps it true (§T.41, §T.42 — V27, V29, V33, V34, V35, V38).
 *
 * **Seeded by the server, not fetched by the browser.** `page.tsx` reads the card with the incoming
 * cookie and hands the result here as `initial`, so the HTML that reaches the frame is already the
 * authenticated card — there is no moment where an operator watches an empty box while a client
 * fetch decides who they are, and the CSRF token is part of the render rather than something a page
 * could ask for without having been allowed to read the request first (§T.41(c), V39).
 *
 * **Then it asks again, because the state lives in the database** (V29). Approve returns 202 and the
 * change runs elsewhere; the only way for this frame to learn the outcome is to re-read the row.
 * `/approvals/[id]` owns the whole lifecycle of one request (V34), and a card that showed the
 * question but never the answer would own half of it. The answer arrives in two parts — the run's
 * terminal status, and the receipt saying what it did (§T.42(b), V46) — and the second is what a
 * poll is actually waiting for.
 *
 * **A poll can discover every state the first read could.** A session that expires under an open
 * frame answers 401, and that renders V38's explicit "cannot authenticate here" rather than leaving
 * a live Approve button standing on a card nobody may decide any more — with §T.43's way out of it
 * beside it (`sign-in-notice.tsx`), which is a link-out and a retry and never a form in the frame
 * (V42). A transient failure is the one answer that changes nothing: the card stays, the loop stays,
 * because "NOA could not be reached just now" is not "there is nothing more to wait for".
 *
 * This is a client component and everything it renders is inside it, so the card has exactly one
 * renderer. A live region updated separately from a server-rendered one would be two descriptions
 * of one row, free to disagree the moment either is edited (V66).
 */

/** What the loop carries between ticks: the last answer, and how long a run has been watched. */
type LiveCard = {
  load: ApprovalCardLoad
  /** Consecutive polls that found a run still in flight. Reset the moment one does not. */
  runPolls: number
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className={styles.factKey}>{label}</dt>
      <dd className={styles.factValue}>{value}</dd>
    </>
  )
}

/** A JSONB payload as flat text. Nested values are shown as JSON rather than dropped. */
function FactList({ values }: { values: Record<string, unknown> }) {
  const entries = Object.entries(values)

  if (entries.length === 0) return <p className={styles.empty}>Nothing recorded.</p>

  return (
    <dl className={styles.facts}>
      {entries.map(([key, value]) => (
        <Fact
          key={key}
          label={key}
          value={typeof value === 'string' ? value : JSON.stringify(value)}
        />
      ))}
    </dl>
  )
}

function KeyValues({ title, values }: { title: string; values: Record<string, unknown> }) {
  return (
    <section className={styles.section}>
      <h2 className={styles.sectionTitle}>{title}</h2>
      <FactList values={values} />
    </section>
  )
}

/**
 * Provenance (V35): when it was asked for, by whom, from which conversation, and until when.
 *
 * The requester and the LibreChat account are the identity the gate persisted at request time
 * (T33), not a join done now — the requester FK is `SET NULL` (T34), so a deleted operator would
 * otherwise erase the identity from a decision that was made.
 */
function Provenance({ card }: { card: ApprovalCard }) {
  return (
    <section className={styles.section}>
      <h2 className={styles.sectionTitle}>Requested</h2>
      <dl className={styles.facts}>
        <Fact label="Requested by" value={card.requester.email || 'unrecorded'} />
        <Fact label="LibreChat account" value={card.requester.librechatUserId || 'unrecorded'} />
        <Fact label="Conversation" value={card.conversationRef ?? 'not supplied'} />
        <Fact label="Opened" value={card.createdAt} />
        <Fact label="Expires" value={card.expiresAt} />
        {card.decidedAt ? <Fact label="Decided" value={card.decidedAt} /> : null}
      </dl>
    </section>
  )
}

/**
 * What the approval started, once something has (V29, V34, V47).
 *
 * `stalled` is the one thing here the row does not say: the card gave up asking. It is not an
 * error — the run may still be going — so it says what is true and what to do about it, rather
 * than reporting a failure NOA has no evidence of.
 */
function Run({ card, stalled }: { card: ApprovalCard; stalled: boolean }) {
  if (card.run === null) return null

  return (
    <section className={styles.section}>
      <h2 className={styles.sectionTitle}>Execution</h2>
      <dl className={styles.facts}>
        <Fact label="Run" value={card.run.toolRunId} />
        <Fact label="Status" value={card.run.status} />
        <Fact label="Started" value={card.run.createdAt} />
        {card.run.completedAt ? <Fact label="Finished" value={card.run.completedAt} /> : null}
        {card.run.resultSummary ? <Fact label="Result" value={card.run.resultSummary} /> : null}
      </dl>
      {stalled ? (
        <p className={styles.empty} role="status">
          NOA is still running this change. Reload this card to check again.
        </p>
      ) : null}
    </section>
  )
}

/**
 * What the change did, once something recorded it (§T.42(b) — V34, V46, DECISIONS §6.5).
 *
 * **Two halves, never one word.** The requirement this section exists for is that an operator can
 * read back the state they authorised against *and* what the change did to it, separately — so a
 * failed change still shows its before-state, and a successful one shows more than "done". The
 * verdict line is a third thing beside them, not a replacement for either.
 *
 * The before-state renders here rather than in the section above once a receipt exists, and that
 * is one heading either way: `receipt.before` is the copy T38's writer took at execution time, so
 * showing both would be the same payload twice under two labels, which reads as two facts.
 *
 * `errorCode` is the API's own string, shown verbatim. It is the word an operator will quote to an
 * administrator, and translating it here would make the card and the audit trail disagree.
 */
function Outcome({ receipt }: { receipt: ApprovalReceipt }) {
  return (
    <section className={styles.section}>
      <h2 className={styles.sectionTitle}>What the change did</h2>
      <dl className={styles.facts}>
        <Fact label="Outcome" value={receipt.ok ? 'Completed' : 'Did not complete'} />
        {receipt.errorCode ? <Fact label="Reason" value={receipt.errorCode} /> : null}
      </dl>
      <FactList values={receipt.after} />
    </section>
  )
}

function Card({ card, stalled }: { card: ApprovalCard; stalled: boolean }) {
  return (
    <main className={styles.card}>
      <header className={styles.header}>
        <h1 className={styles.tool}>{card.toolName}</h1>
        <p className={styles.status}>{statusLabel(card.status)}</p>
      </header>

      <Provenance card={card} />
      <KeyValues title="Arguments" values={card.arguments} />
      {/* The in-process preflight (C9, V17): the before-state this card exists to show, and the
          one thing on the row the model is never told (`core.approvals.results`). Off the receipt
          once there is one — see `Outcome` for why that is one heading and not two. */}
      <KeyValues title="Before state" values={card.receipt?.before ?? card.evidence} />
      <Run card={card} stalled={stalled} />
      {card.receipt ? <Outcome receipt={card.receipt} /> : null}

      {canDecide(card) && card.csrf ? (
        <DecisionControls actionRequestId={card.actionRequestId} csrf={card.csrf} />
      ) : (
        // No reason box and no buttons once nothing may be decided (V38's family): a live
        // Approve button over a decided or expired request is an action that was never
        // available, shown as one that was refused.
        <p className={styles.empty}>
          This request is no longer awaiting a decision, so it cannot be answered from here.
        </p>
      )}
    </main>
  )
}

export function CardView({
  initial,
  actionRequestId,
  signInUrl,
}: {
  initial: ApprovalCardLoad
  /**
   * The id from the URL, not from the card.
   *
   * A 401 or a 404 carries no card and therefore no id, so a retry offered from one of those states
   * would have nothing to re-read — this is the page's own parameter, which is true whatever the
   * last read answered (§T.43).
   */
  actionRequestId: string
  /** Where an operator signs in, or `null` when nothing usable is configured (`lib/sign-in.ts`). */
  signInUrl: string | null
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
          // or a 404 replaces it, because those are states an operator has to be shown (V38, V27).
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
   * One read, on demand, from the state that has nothing to poll (§T.43).
   *
   * The poll's reader, not a second one (V66) — and the poll's rule for what to do with the answer:
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
    return <Card card={load.card} stalled={isStalled(load.card, runPolls)} />
  }

  if (load.kind === 'unauthenticated') {
    // V38, V42: an explicit state, never a blank card and never a live Approve button — and §T.43's
    // way out of it, which is a top-level link-out plus a retry. This app has no login page and no
    // LDAP form, and nothing in that notice navigates the frame.
    return <SignInNotice signInUrl={signInUrl} onRetry={retryRead} />
  }

  if (load.kind === 'not-found') {
    // V27: absent, another operator's, and one whose requester was deleted all answer alike —
    // the API gives one body for the three, and this page gives one sentence.
    return (
      <Notice
        title="Request not available"
        body="This approval request does not exist, or it is not yours to decide."
      />
    )
  }

  return (
    <Notice
      title="Could not load this request"
      body="NOA could not be reached, or answered unexpectedly. Reload this card; contact an administrator if it continues."
    />
  )
}
