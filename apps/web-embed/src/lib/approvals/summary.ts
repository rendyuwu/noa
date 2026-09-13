/**
 * The whole approval, as one block of text an operator can paste into a ticket.
 *
 * **Evidence has to be able to leave the frame, and it leaves as text.** Measured on the pinned
 * host: no image reaches the clipboard from inside the frame by any route, so this block is the
 * only durable record an operator can carry out. What it carries is what a ticket is opened for a
 * year later — what the change did to which machine, when, and the one identifier an administrator
 * can look the run up by.
 *
 * **The headline states two facts and never one.** The status is the decision an operator made; the
 * corner beside it (`lib/approvals/verdict.ts`) is what the change then did, read off the
 * verification state rather than off the call's return. A receipt NOA could not verify, pasted as
 * "Approved" alone, folds a non-answer into the benign value; an approved request with no receipt
 * at all states that nothing has recorded what the change did, rather than pasting as an approval
 * over an empty result.
 *
 * **The body is the card's, byte for byte.** Both read `lib/approvals/body.ts`, so the block and
 * the screen cannot become two statements of one measurement.
 *
 * **Two flavours, one builder.** `text` and `html` ride on a single copy and must say the same
 * thing: a reader who pastes into a plain-text field and a reader who pastes into a rich one are
 * quoting the same record in a dispute. One section list rather than two writers is what keeps that
 * true — there is nowhere to add a fact to only one of them.
 *
 * **What each section says lives in `lib/approvals/summary-sections.ts`**. Split off for the
 * 300-line cap `apps/api/tests/test_config.py` enforces.
 *
 * **Every value is HTML-escaped on its way into the `html` flavour**, at one point in `renderHtml`,
 * because the component that copies it hands the string to `dangerouslySetInnerHTML`.
 */

import { cardBody } from '@/lib/approvals/body'
import type { ApprovalCard } from '@/lib/approvals/card'
import type { Section } from '@/lib/approvals/summary-sections'
import {
  evidenceSection,
  headlineSection,
  supportSection,
  timingSection,
} from '@/lib/approvals/summary-sections'

/** The one copy, in both flavours the clipboard carries. */
export type Summary = { text: string; html: string }

const TITLE = 'NOA approval record'

function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/**
 * Each section's lines with any embedded newline made into a line of its own.
 *
 * **One repair, before both flavours, because a `\n` inside a line breaks both of them.** Several
 * runner branches put one inside `message` — four of the five composing families spell it that way,
 * so the newline is the contract and the renderers are what move. Left alone, `renderText` indents
 * per array element and the second sentence lands at column 0 while everything around it sits at
 * two spaces; `renderHtml` puts it inside one `<li>`, where HTML collapses it to a space and the
 * break disappears. Splitting here fixes both at one seam, and it is the cheap repair rather than
 * teaching six runners to avoid a character they deliberately write.
 *
 * The card makes the same repair at its own seam with `white-space: pre-line`
 * (`app/approvals/[id]/card.module.css`), so the two halves of one measurement agree about where a
 * sentence ends.
 */
function splitEmbeddedLines(sections: Section[]): Section[] {
  return sections.map((section) => ({
    heading: section.heading,
    lines: section.lines.flatMap((line) => line.split('\n')),
  }))
}

function renderText(sections: Section[]): string {
  const blocks = sections.map((section) =>
    [section.heading, ...section.lines.map((line) => `  ${line}`)].join('\n'),
  )
  return [TITLE, ...blocks].join('\n\n')
}

/**
 * The same sections as markup. A list per section rather than a `<pre>`: the flavour exists so a
 * rich-text field renders it as structure, and a `<pre>` pasted into one arrives as a monospace
 * wall no better than the plain flavour beside it, which would make the second flavour pointless.
 *
 * Escaping is here, once, on the whole line. Every line is assembled from a literal label and
 * API-supplied values, so escaping the finished line covers both and there is no second place to
 * forget.
 */
function renderHtml(sections: Section[]): string {
  const blocks = sections.map((section) => {
    const items = section.lines.map((line) => `<li>${escapeHtml(line)}</li>`).join('')
    const list = items === '' ? '' : `<ul>${items}</ul>`
    return `<p><strong>${escapeHtml(section.heading)}</strong></p>${list}`
  })
  return `<div><p><strong>${escapeHtml(TITLE)}</strong></p>${blocks.join('')}</div>`
}

/** The whole record, in both flavours, from one pass over one card. */
export function buildSummary(card: ApprovalCard): Summary {
  const body = cardBody(card)
  const evidence = evidenceSection(body)
  const sections = splitEmbeddedLines([
    headlineSection(body),
    // No section at all where the gate published no heading, which is the same decision the card
    // makes from the same key: an empty heading over nothing is a renderer that looks broken.
    ...(evidence === null ? [] : [evidence]),
    timingSection(card),
    supportSection(card),
  ])

  return { text: renderText(sections), html: renderHtml(sections) }
}
