import { headers } from 'next/headers'

import { type ApprovalCard, canDecide, statusLabel } from '@/lib/approvals/card'
import { loadApprovalCard } from '@/lib/approvals/detail'

import { DecisionControls } from './decision-controls'
import styles from './card.module.css'

/**
 * The approval card (§T.41 — §I.embed, V27, V32, V33, V34, V35, V38, V39, V80).
 *
 * One URL owns the whole lifecycle of one request (V34): this page asks the question while the
 * request is PENDING and reports the answer afterwards, so an operator who clicks the link a
 * second time reads the outcome rather than a dead page.
 *
 * A **server** component, and the render is authenticated before any HTML exists: the incoming
 * `Cookie` header goes with the server-side read (`lib/approvals/detail.ts`), so there is no
 * moment where the frame shows an empty box while a client fetch decides whether the operator is
 * signed in — and the CSRF token (V39) is part of the render rather than something a page could
 * ask for without having been allowed to read the request first.
 *
 * The only client code is `DecisionControls`, and the decision it sends is a JS `fetch` because
 * the sandbox this frame runs under omits `allow-forms` (V80, R13, R29). Nothing on this page is
 * a `<form>`.
 */

// Reading `headers()` already opts this route out of prerendering; saying so as well means a
// future edit that stops reading them cannot quietly make one operator's card cacheable for the
// next request. Same declaration the proxy route carries (§T.44(g)).
export const dynamic = 'force-dynamic'

function Notice({ title, body }: { title: string; body: string }) {
  return (
    <main className={styles.notice}>
      <h1 className={styles.noticeTitle}>{title}</h1>
      <p className={styles.noticeBody}>{body}</p>
    </main>
  )
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
function KeyValues({ title, values }: { title: string; values: Record<string, unknown> }) {
  const entries = Object.entries(values)

  return (
    <section className={styles.section}>
      <h2 className={styles.sectionTitle}>{title}</h2>
      {entries.length === 0 ? (
        <p className={styles.empty}>Nothing recorded.</p>
      ) : (
        <dl className={styles.facts}>
          {entries.map(([key, value]) => (
            <Fact
              key={key}
              label={key}
              value={typeof value === 'string' ? value : JSON.stringify(value)}
            />
          ))}
        </dl>
      )}
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

/** What the approval started, once something has (V29, V34, V47). */
function Run({ card }: { card: ApprovalCard }) {
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
    </section>
  )
}

function Card({ card }: { card: ApprovalCard }) {
  return (
    <main className={styles.card}>
      <header className={styles.header}>
        <h1 className={styles.tool}>{card.toolName}</h1>
        <p className={styles.status}>{statusLabel(card.status)}</p>
      </header>

      <Provenance card={card} />
      <KeyValues title="Arguments" values={card.arguments} />
      {/* The in-process preflight (C9, V17): the before-state this card exists to show, and the
          one thing on the row the model is never told (`core.approvals.results`). */}
      <KeyValues title="Before state" values={card.evidence} />
      <Run card={card} />

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

export default async function ApprovalCardPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  const cookie = (await headers()).get('cookie')
  const load = await loadApprovalCard(id, { cookie })

  if (load.kind === 'card') return <Card card={load.card} />

  if (load.kind === 'unauthenticated') {
    // V38, V42: an explicit state, never a blank card and never a live Approve button. This app
    // has no login page and no LDAP form; the "Sign in to NOA" link-out that belongs beside this
    // is §T.43's, and it opens a top-level tab rather than a form in the frame.
    return (
      <Notice
        title="Cannot authenticate here"
        body="NOA does not recognise this session. Open NOA in a new tab, sign in, then reload this card."
      />
    )
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
