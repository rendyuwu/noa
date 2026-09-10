'use client'

import { useEffect, useRef, useState } from 'react'

import { type ApprovalCard, type ApprovalCardLoad, canDecide, statusLabel } from '@/lib/approvals/card'
import { fetchApprovalCard, isRunning, isStalled, isTerminal, pollIntervalMs } from '@/lib/approvals/poll'
import { buildSummary } from '@/lib/approvals/summary'
import { CARD_FRAME_POLICY } from '@/lib/embed/frame-size'
import { formatCountdown, formatDuration, formatRelative } from '@/lib/format/jakarta-time'

import { DecisionControls } from './decision-controls'
// `Fact` and `FactList` live there rather than here so one definition renders every payload block
// on this card — the arguments, the before-state and the receipt's own halves.
import { Fact, FactList, Outcome } from './outcome-view'
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
 * it. Every state below that is a section appearing (the Execution block, the receipt) is also a
 * new height, which is why the measurement is re-taken on render rather than once at mount.
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

const KNOWN_TOOL_PREFIXES = ['proxmox_', 'whm_', 'pmg_']
const TOOL_WORD_OVERRIDES: Record<string, string> = {
  csf: 'CSF',
  whm: 'WHM',
  rbac: 'RBAC',
  ldap: 'LDAP',
  vm: 'VM',
  pmg: 'PMG',
}

/**
 * A raw tool name as a readable label. The raw name stays on the card in the heading's `title`.
 *
 * **Duplicated from `apps/admin-web/src/lib/admin/audit/audit-format.ts`, deliberately, and it
 * stays duplicated.** The two web apps are independent packages with their own lockfiles, their
 * own CI and their own deploy; this one's eslint refuses any `apps/admin-web` import outright, and
 * the `core/` the two really do share is Python. Sharing these fifteen lines would cost either a
 * third npm package or the cross-app import the fence exists to forbid, and both are more than
 * the duplication is worth. Kept byte-identical to the other copy so a reader diffing the two can
 * see at a glance that they have not drifted.
 */
function humanizeToolName(value: string): string {
  const raw = value.trim()
  const withoutPrefix = KNOWN_TOOL_PREFIXES.reduce(
    (current, prefix) => (current.startsWith(prefix) ? current.slice(prefix.length) : current),
    raw,
  )
  const words = withoutPrefix
    .split(/[_-]+/)
    .filter(Boolean)
    .map((word) => {
      const lower = word.toLowerCase()
      return TOOL_WORD_OVERRIDES[lower] ?? lower.charAt(0).toUpperCase() + lower.slice(1)
    })
  return words.length === 0 ? raw : words.join(' ')
}

/**
 * A nested payload as one row per leaf, keyed by its path.
 *
 * A nested object serialised into a single row is one line of JSON in a frame this narrow, which
 * is the least readable thing on the card and the reason the arguments block gets this treatment.
 * Lists are left whole: a firewall match list runs to twenty entries, and twenty rows would cost
 * more of the one screen this card gets than the list is worth.
 */
function flattenValues(values: Record<string, unknown>, prefix = ''): Record<string, unknown> {
  const flat: Record<string, unknown> = {}

  for (const [key, value] of Object.entries(values)) {
    const path = prefix ? `${prefix}.${key}` : key
    const nested =
      typeof value === 'object' && value !== null && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : null

    // An empty object has no leaves, so it renders as itself rather than as no row at all.
    if (nested && Object.keys(nested).length > 0) Object.assign(flat, flattenValues(nested, path))
    else flat[path] = value
  }

  return flat
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
 * Provenance: when it was asked for, by whom, and how long there is left to answer.
 *
 * The requester is the identity the gate persisted at request time, not a join done now — the
 * requester FK is `SET NULL`, so a deleted operator would otherwise erase the identity from a
 * decision that was made.
 *
 * **Times are read the way an operator reads them.** "17 minutes ago" and "expires in 43 minutes"
 * are what a decision actually turns on; the exact instant is one hover away in `title` and, for
 * the operator who has to quote it to an administrator, in the copy summary. The LibreChat account
 * id and the conversation id left this block for the same reason: they identify the request to
 * NOA and to `/admin`, not to the person deciding it, and the copy summary carries both for the
 * operator who cannot open the admin panel.
 */
function Provenance({ card }: { card: ApprovalCard }) {
  return (
    <section className={styles.section}>
      <h2 className={styles.sectionTitle}>Requested</h2>
      <dl className={styles.facts}>
        <Fact label="Requested by" value={card.requester.email || 'unrecorded'} />
        <Fact label="Opened" value={formatRelative(card.createdAt)} title={card.createdAt} />
        <Fact label="Expires" value={formatCountdown(card.expiresAt)} title={card.expiresAt} />
        {card.decidedAt ? (
          <Fact label="Decided" value={formatRelative(card.decidedAt)} title={card.decidedAt} />
        ) : null}
      </dl>
    </section>
  )
}

/**
 * What the approval started, once something has.
 *
 * `stalled` is the one thing here the row does not say: the card gave up asking. It is not an
 * error — the run may still be going — so it says what is true and what to do about it, rather
 * than reporting a failure NOA has no evidence of.
 *
 * The run's own id is not on the card. It is the identifier an administrator needs and the
 * operator does not, it costs a row of a card that has to fit one screen, and the copy summary
 * carries it for the operator who has to hand it to someone. The two timestamps are one duration
 * for the same reason: "took 41s" is the fact, and both instants are still on the row in `/admin`.
 */
function Run({ card, stalled }: { card: ApprovalCard; stalled: boolean }) {
  if (card.run === null) return null

  // `null` while the run is still in flight, and also when the two stamps cannot yield a duration.
  // Both answer with when it started instead: a run in progress has no duration yet, and inventing
  // one for a pair that cannot produce it would state a measurement nobody made.
  const duration = formatDuration(card.run.createdAt, card.run.completedAt)

  return (
    <section className={styles.section}>
      <h2 className={styles.sectionTitle}>Execution</h2>
      <dl className={styles.facts}>
        <Fact label="Status" value={card.run.status} />
        {duration === null ? (
          <Fact
            label="Started"
            value={formatRelative(card.run.createdAt)}
            title={card.run.createdAt}
          />
        ) : (
          <Fact label="Duration" value={duration} title={card.run.createdAt} />
        )}
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
 * The keys the before-state block does not print.
 *
 * Two identifiers NOA needs and the operator does not (`server_id` is the machine's row, and
 * `api_username` the credential the preflight was read with), and the raw account record a WHM
 * preflight carries whole — which is the biggest single value on the card and the least readable
 * one.
 *
 * **These three are admin-only by design, and the copy summary does not carry them.** That is the
 * difference between this block and the identifiers that left the provenance and execution
 * blocks: those are what an operator quotes into a ticket, so they ride in the copied summary,
 * while these three answer an administrator's question and are reached through `/admin` (held by
 * `apps/api/tests/test_admin_action_request_routes.py`). They stay in the database and in the
 * API's body either way; what changes here is only which of the two surfaces prints them.
 */
const BEFORE_STATE_HIDDEN = new Set(['server_id', 'api_username', 'account'])

function shown(values: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(values).filter(([key]) => !BEFORE_STATE_HIDDEN.has(key)),
  )
}

function Card({
  card,
  stalled,
  frameOrigin,
}: {
  card: ApprovalCard
  stalled: boolean
  frameOrigin: string | null
}) {
  const scrollContainer = useRef<HTMLElement>(null)

  return (
    <>
      <main className={styles.card} ref={scrollContainer}>
        <header className={styles.header}>
          {/* Humanised, with the raw name in `title` and in the copy summary. The raw name is what
              an operator quotes to an administrator and what `/admin` shows, so a card that
              renders a label has to keep the name itself reachable from the same place. */}
          <h1 className={styles.tool} title={card.toolName}>
            {humanizeToolName(card.toolName)}
          </h1>
          <p className={styles.status}>{statusLabel(card.status)}</p>
          <CopySummary summary={buildSummary(card)} />
        </header>

        <Provenance card={card} />
        <KeyValues title="Arguments" values={flattenValues(card.arguments)} />
        {/* The in-process preflight: the before-state this card exists to show, and the
            one thing on the row the model is never told (`core.approvals.results`). Off the receipt
            once there is one — `receipt.before` is the copy the approved-change executor's writer
            took at execution time, so rendering both would be one payload under two headings,
            which reads as two facts.

            Nothing predicted joins it while the request is PENDING: the delta is written at
            execution time, so at this point nothing has measured what the change will do, and a
            templated after-column or sentence would be a claim with no measurement behind it. */}
        <KeyValues title="Before state" values={shown(card.receipt?.before ?? card.evidence)} />
        <Run card={card} stalled={stalled} />
        {card.receipt ? <Outcome receipt={card.receipt} /> : null}

        {canDecide(card) && card.csrf ? (
          <DecisionControls actionRequestId={card.actionRequestId} csrf={card.csrf} />
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
