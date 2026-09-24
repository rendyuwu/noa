'use client'

import { type JSX, useRef, useState } from 'react'

import type { Summary } from '@/lib/approvals/summary'

import shared from '@/components/notice.module.css'
import styles from './copy-summary.module.css'

/**
 * What a card spec passes as `ignore` to keep this block out of a text query.
 *
 * **Both halves are required and the second is the one that does the work.** Testing Library runs
 * `node.matches(ignore)` against the candidate node itself, never against its ancestors, and the
 * text a card asserts on lives in an `<li>` inside the block rather than on the block. So
 * `[data-noa-copy-block]` alone excludes nothing at all — measured both ways, and held by a case in
 * `copy-summary.test.tsx` that fails if either half is dropped.
 *
 * **It carries `script, style` itself, so pass it directly**: `{ ignore: COPY_BLOCK_IGNORE }`.
 * Supplying `ignore` REPLACES Testing Library's default of `script, style` instead of adding to it,
 * so a constant holding only the block selector would have to be wrapped at every call site and
 * would silently re-admit script and style text wherever somebody forgot. The default lives in the
 * value instead: there is then no way to spell this correctly-but-not-quite.
 */
export const COPY_BLOCK_IGNORE =
  'script, style, [data-noa-copy-block], [data-noa-copy-block] *'

/**
 * One button that puts the whole approval record on the clipboard, in both flavours.
 *
 * **`document.execCommand('copy')` is deprecated and it is the deliberate choice here. Do not
 * modernise it — the modern call is refused on this surface and replacing it ships a button that
 * does nothing.** Measured on the pinned host: `navigator.clipboard.writeText` is refused in a
 * cross-origin frame by Permissions-Policy, while `execCommand` over a script-made selection works
 * at both of the sandbox strings the card is rendered under and carries `text/plain` and
 * `text/html` together. No image reaches the clipboard by any route, which is why the evidence
 * leaves as text at all.
 *
 * **Two measurement hygiene notes, recorded because they cost a day each.** Headless Chromium
 * refuses clipboard-write even at top level, so a red result in a headless lane says nothing about
 * the frame. And `permissions.query({ name: 'clipboard-write' })` answers `granted` inside frames
 * where the write is still refused — so that query is recorded *beside* an attempt and is never
 * read as the answer to whether copying works.
 *
 * **The selected block is off-screen but laid out** (`position: fixed; left: -10000px`). Three
 * things forced that shape at once: a hidden `<textarea>` yields `text/plain` only and loses the
 * rich flavour; `display: none` has no layout, so it cannot be inside a selection range at all;
 * and the rendered card no longer shows the absolute timestamps or the identifiers, so the
 * selection cannot simply be the card.
 *
 * Off-screen is a rendering state and not a hiding one, so the block is hidden from assistive
 * technology explicitly while it is there — see the attribute below. It sits in the card's header,
 * ahead of the card and repeating most of it, which is a wall of text to hear before reaching the
 * thing it describes.
 *
 * **Both flavours are set explicitly on the copy event rather than left to the browser's
 * serialisation of the selection.** What a browser derives as plain text from a DOM selection
 * depends on layout, so the `text` half of the builder's output would otherwise never be used and
 * the two flavours could drift. The selection is still required: `execCommand('copy')` with none
 * is a no-op and never fires the event.
 *
 * **A refusal is visible.** `execCommand` answers `false` rather than throwing when it declines,
 * and a copy that silently does nothing is the same failure this codebase refuses everywhere else.
 * On failure the block comes on-screen so the operator can select it by hand — the escape hatch
 * ships beside the door, it is not the door's error message.
 */
export function CopySummary({
  summary,
  disabled = false,
}: {
  summary: Summary
  disabled?: boolean
}): JSX.Element {
  const block = useRef<HTMLDivElement>(null)
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')

  return (
    <div className={styles.copy}>
      <div className={shared.actions}>
        <button
          type="button"
          className={shared.button}
          disabled={disabled}
          onClick={() => setState(copyBothFlavours(block.current, summary) ? 'copied' : 'failed')}
        >
          Copy summary
        </button>
      </div>

      {state === 'idle' ? null : (
        <p className={state === 'failed' ? styles.failed : styles.copied} role="status">
          {state === 'failed'
            ? 'This browser refused the copy. The full record is below — select it and copy it by hand.'
            : 'Summary copied, as plain text and as formatted text.'}
        </p>
      )}

      {/*
       * The builder escapes every API-supplied value on its way into this string
       * (`lib/approvals/summary.ts`), which is the reason this is safe to inject and the reason
       * that escaping is not optional there.
       *
       * `data-noa-copy-block` is a test hook and it is load-bearing, so do not drop it. This block
       * is a second copy of facts the card also renders, and Testing Library does not filter on
       * visibility — so a card spec asserting "this value is not printed here" matches the copy
       * block and reads as a pass. Those specs pass `COPY_BLOCK_IGNORE`, declared at the top of
       * this file; read its docstring before writing a selector by hand, because the bare
       * attribute form excludes nothing and the wrapper around it is not optional either.
       *
       * A CSS-module class cannot serve as the hook: the hash moves, and the `exposed` swap below
       * means there would be two of them to match.
       */}
      <div
        ref={block}
        data-noa-copy-block
        // Hidden from assistive technology while it is off-screen, and only while. The block
        // repeats most of the card underneath it, unlabelled and ahead of it in the document, so
        // announcing it means hearing the record before reaching the card. Once a refused copy
        // brings it on screen it is the escape hatch and has to be readable, so the flag lifts
        // with the same state that reveals it.
        aria-hidden={state === 'failed' ? undefined : true}
        className={state === 'failed' ? styles.exposed : styles.offscreen}
        dangerouslySetInnerHTML={{ __html: summary.html }}
      />
    </div>
  )
}

/**
 * Select the block, put both flavours on the clipboard, and say whether it took.
 *
 * A plain function rather than an effect: this runs inside the click, and a clipboard write
 * outside a user gesture is refused by every browser that implements the gesture rule.
 */
function copyBothFlavours(block: HTMLElement | null, summary: Summary): boolean {
  const selection = window.getSelection()
  // `execCommand` is absent under jsdom and could be withdrawn by a browser one day. Absent is a
  // refusal, not a crash: the operator gets the visible failed state and the block to copy by hand.
  if (block === null || selection === null || typeof document.execCommand !== 'function') {
    return false
  }

  function carry(event: ClipboardEvent): void {
    if (event.clipboardData === null) return
    event.clipboardData.setData('text/plain', summary.text)
    event.clipboardData.setData('text/html', summary.html)
    event.preventDefault()
  }

  const range = document.createRange()
  range.selectNodeContents(block)
  selection.removeAllRanges()
  selection.addRange(range)
  document.addEventListener('copy', carry)

  try {
    return document.execCommand('copy')
  } catch {
    // A refusal that throws rather than answering `false`. Same outcome for the operator.
    return false
  } finally {
    document.removeEventListener('copy', carry)
    // The selection is script-made and off-screen; leaving it in place leaves the document in a
    // state the operator never asked for and cannot see.
    selection.removeAllRanges()
  }
}
