import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Summary } from '@/lib/approvals/summary'

import { COPY_BLOCK_IGNORE, CopySummary } from './copy-summary'

/**
 * The copy control.
 *
 * **What this lane can and cannot prove, stated so nobody mistakes green here for the real
 * answer.** jsdom implements no `document.execCommand` at all — the method is simply absent — and
 * it implements no clipboard. So every case below that copies is asserting against a stub this
 * file installed: that the component reached for `execCommand` and not for
 * `navigator.clipboard.writeText`, that it set both flavours on the copy event, and that it had a
 * selection covering the record when it did. **That both flavours actually land on a real
 * clipboard from inside the frame is a manual headed run, and it is explicitly not this lane's to
 * close.** It cannot be closed by a headless lane either: headless Chromium refuses clipboard-write
 * even at top level, so a red result there would say nothing about the frame.
 *
 * jsdom's Selection and Range *are* real, which is why the selection assertion is worth something:
 * it fails if the component stops selecting the block, and that is the half of the mechanism a DOM
 * can hold.
 */

const SUMMARY: Summary = {
  text: 'NOA approval record\n\nReference\n  Request id: 9f1c2b7e-0000-4000-8000-000000000000',
  html: '<div><p><strong>NOA approval record</strong></p><ul><li>Request id: 9f1c2b7e</li></ul></div>',
}

type Stub = {
  commands: string[]
  flavours: Record<string, string>
  /** What was selected at the moment the copy was attempted, before the component cleared it. */
  selected: string[]
  writeText: ReturnType<typeof vi.fn>
}

/**
 * Stand in for a clipboard jsdom does not have.
 *
 * `navigator.clipboard` is defined here rather than spied on for the same reason: it does not
 * exist under jsdom, so "never called" needs something that *could* have been called to be a claim
 * at all. Without it the negative assertion would pass against a component that tried and crashed.
 */
function stubClipboard({ succeeds }: { succeeds: boolean }): Stub {
  const stub: Stub = { commands: [], flavours: {}, selected: [], writeText: vi.fn() }

  document.execCommand = (command: string): boolean => {
    stub.commands.push(command)
    stub.selected.push(window.getSelection()?.toString() ?? '')
    const clipboardData = {
      setData: (type: string, value: string) => {
        stub.flavours[type] = value
      },
    }
    document.dispatchEvent(
      Object.assign(new Event('copy', { cancelable: true }), { clipboardData }),
    )
    return succeeds
  }

  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: stub.writeText },
    configurable: true,
  })

  return stub
}

function clickCopy(): void {
  fireEvent.click(screen.getByRole('button', { name: /copy summary/i }))
}

/** Found by the hook the card's own specs use, so this file breaks with them if it is dropped. */
function recordBlock(container: HTMLElement): Element {
  const block = container.querySelector('[data-noa-copy-block]')
  if (block === null) throw new Error('no record block rendered')
  return block
}

afterEach(() => {
  cleanup()
  Reflect.deleteProperty(document, 'execCommand')
  Reflect.deleteProperty(navigator, 'clipboard')
  vi.restoreAllMocks()
})

describe('CopySummary', () => {
  it('copies through execCommand, over a selection covering the record', () => {
    const stub = stubClipboard({ succeeds: true })
    render(<CopySummary summary={SUMMARY} />)

    clickCopy()

    expect(stub.commands).toEqual(['copy'])
    // jsdom's Selection is real, so this is a measurement and not a stub talking to itself: the
    // component made a range over the laid-out block before asking for the copy.
    expect(stub.selected[0]).toContain('NOA approval record')
  })

  it('puts both flavours on the event, from the one builder', () => {
    const stub = stubClipboard({ succeeds: true })
    render(<CopySummary summary={SUMMARY} />)

    clickCopy()

    expect(stub.flavours['text/plain']).toBe(SUMMARY.text)
    expect(stub.flavours['text/html']).toBe(SUMMARY.html)
  })

  it('never calls navigator.clipboard.writeText', () => {
    // Measured refused in a cross-origin frame by Permissions-Policy. Reaching for it would ship a
    // button that does nothing on the one surface this app renders on.
    const stub = stubClipboard({ succeeds: true })
    render(<CopySummary summary={SUMMARY} />)

    clickCopy()

    expect(stub.writeText).not.toHaveBeenCalled()
  })

  it('reports a copy that took', () => {
    stubClipboard({ succeeds: true })
    const { container } = render(<CopySummary summary={SUMMARY} />)

    clickCopy()

    expect(screen.getByRole('status').textContent).toContain('Summary copied')
    expect(recordBlock(container).className).toContain('offscreen')
  })

  it('shows a visible failed state, and the record to copy by hand, when the copy is refused', () => {
    // `execCommand` answers `false` rather than throwing when it declines. A button that reports
    // nothing here is the silent no-op this codebase refuses everywhere else.
    stubClipboard({ succeeds: false })
    const { container } = render(<CopySummary summary={SUMMARY} />)

    clickCopy()

    expect(screen.getByRole('status').textContent).toContain('refused the copy')
    expect(recordBlock(container).className).toContain('exposed')
    expect(recordBlock(container).textContent).toContain('NOA approval record')
  })

  it('fails visibly rather than throwing where execCommand does not exist', () => {
    // jsdom's own state, and one a browser could reach by withdrawing a deprecated API. No stub
    // installed on purpose.
    const { container } = render(<CopySummary summary={SUMMARY} />)

    clickCopy()

    expect(screen.getByRole('status').textContent).toContain('refused the copy')
    expect(recordBlock(container).className).toContain('exposed')
  })

  it('says nothing before it has been asked to do anything', () => {
    const { container } = render(<CopySummary summary={SUMMARY} />)

    expect(screen.queryByRole('status')).toBeNull()
    expect(recordBlock(container).className).toContain('offscreen')
  })

  it('marks the record block so a card spec can exclude it, and hides it from screen readers', () => {
    // Both halves exist because this block is a second copy of facts the card also renders.
    // Testing Library does not filter on visibility, so a card spec asserting a value is *not*
    // printed matches this block and reads as a pass unless it can name it — hence the attribute.
    // A screen reader has the mirror-image problem: the block sits ahead of the card, unlabelled,
    // so announcing it means hearing the whole record before reaching the card it describes.
    const { container } = render(<CopySummary summary={SUMMARY} />)

    expect(recordBlock(container).getAttribute('aria-hidden')).toBe('true')
    expect(container.querySelectorAll('[data-noa-copy-block]')).toHaveLength(1)
  })

  it('lets a screen reader reach the record once a refusal puts it on screen', () => {
    // The escape hatch has to be readable by whoever has to copy it by hand.
    const { container } = render(<CopySummary summary={SUMMARY} />)

    clickCopy()

    expect(recordBlock(container).getAttribute('aria-hidden')).toBeNull()
  })

  it('keeps its text out of a card query, and only the descendant half of the selector does it', () => {
    // The reason `COPY_BLOCK_IGNORE` has two halves, held here so nobody simplifies it back.
    // Testing Library's `ignore` runs `node.matches(selector)` on the matching node itself, and
    // the text sits in an `<li>` inside the block, not on the block — so the attribute selector
    // alone excludes nothing, and a card spec using it would still find this copy and pass.
    const status = 'Status: Expired without an answer'
    render(<CopySummary summary={{ text: status, html: `<ul><li>${status}</li></ul>` }} />)

    expect(screen.queryAllByText(/expired without an answer/i, { ignore: COPY_BLOCK_IGNORE })).toEqual(
      [],
    )
    expect(
      screen.queryAllByText(/expired without an answer/i, { ignore: '[data-noa-copy-block]' }),
    ).toHaveLength(1)
  })

  it('carries the script and style default it replaces, so callers pass it bare', () => {
    // Supplying `ignore` REPLACES Testing Library's default of `script, style` rather than adding
    // to it, so the default is folded into this constant instead of being retyped at each call
    // site. Held here because deleting `script, style` from the value would otherwise re-admit
    // stylesheet text to every card query silently.
    const status = 'Status: Expired without an answer'
    render(
      <>
        <style>{`.x { content: '${status}' }`}</style>
        <CopySummary summary={{ text: status, html: `<ul><li>${status}</li></ul>` }} />
      </>,
    )

    expect(screen.queryAllByText(/expired without an answer/i, { ignore: COPY_BLOCK_IGNORE })).toEqual(
      [],
    )
    // The same query with only the block halves finds the stylesheet, which is what the folded-in
    // default is there to hide.
    expect(
      screen.queryAllByText(/expired without an answer/i, {
        ignore: '[data-noa-copy-block], [data-noa-copy-block] *',
      }),
    ).toHaveLength(1)
  })

  it('renders no form, and its button submits none', () => {
    // The sandbox the frame is rendered under omits `allow-forms`, so a native submit anywhere in
    // this tree would be a control that silently does nothing.
    const { container } = render(<CopySummary summary={SUMMARY} />)

    expect(container.querySelector('form')).toBeNull()
    expect(screen.getByRole('button').getAttribute('type')).toBe('button')
  })
})
