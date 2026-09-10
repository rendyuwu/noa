import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ApprovalCardLoad } from '@/lib/approvals/card'

import { SignInNotice } from './sign-in-notice'

/**
 * The way out of a 401 (§T.43 — V38, V42).
 *
 * **What jsdom can prove is the structure, and here the structure is most of the requirement.** V42
 * is a statement about what this app does *not* contain — no login page, no LDAP form, no credential
 * handling — and §T.43 adds "no LDAP redirect inside the iframe" to it. Those are absences, and this
 * is the lane that asserts absences: no `<form>`, no input of any kind, and one anchor whose only
 * job is to open a document somewhere else.
 *
 * What jsdom cannot prove is the half that made the printed address necessary: that a
 * `target="_blank"` click opens nothing at all under the sandbox LibreChat applies at one of its two
 * render sites. A jsdom assertion about a sandbox would be a check that cannot fail, so
 * that one lives in `e2e/approvals.browser.e2e.ts` with its own negative control.
 */

const SIGN_IN = 'https://admin.noa.internal/login'

/**
 * A stand-in for the card's re-read, answering one kind per click (the last one repeats).
 *
 * `card` is deliberately not among the answers used here: that one replaces this component
 * altogether, and the swap belongs to `card-view.test.tsx`, which owns the loop.
 */
function retryStub(...kinds: ApprovalCardLoad['kind'][]) {
  const calls: string[] = []
  const onRetry = vi.fn(async (): Promise<ApprovalCardLoad> => {
    const kind = kinds[Math.min(calls.length, kinds.length - 1)] ?? 'unauthenticated'
    calls.push(kind)
    return kind === 'unavailable'
      ? { kind: 'unavailable', status: 503 }
      : ({ kind } as ApprovalCardLoad)
  })
  return { onRetry, calls }
}

function clickRetry(): void {
  fireEvent.click(screen.getByRole('button', { name: /try again/i }))
}

/** What the frame sizer this notice mounts reports it is doing, or `undefined` if none is mounted. */
function sizerState(container: HTMLElement): string | null | undefined {
  return container.querySelector('[data-noa-frame-size]')?.getAttribute('data-noa-frame-size')
}

describe('SignInNotice', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('names the state, offers a way out, and no decision control', () => {
    // Never a blank card and never a live Approve button: the operator is told what is wrong and
    // given the two things that can fix it.
    const { onRetry } = retryStub('unauthenticated')
    render(<SignInNotice signInUrl={SIGN_IN} onRetry={onRetry} frameOrigin={null} />)

    expect(screen.getByRole('heading').textContent).toContain('Cannot authenticate here')
    expect(screen.getByRole('link', { name: /sign in to noa/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /try again/i })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /approve|deny/i })).toBeNull()
    expect(screen.queryByLabelText(/why is this change/i)).toBeNull()
  })

  it('opens the sign-in in a new top-level document, never in the frame (V42, §T.43)', () => {
    const { onRetry } = retryStub('unauthenticated')
    render(<SignInNotice signInUrl={SIGN_IN} onRetry={onRetry} frameOrigin={null} />)

    const link = screen.getByRole('link', { name: /sign in to noa/i })

    expect(link.getAttribute('href')).toBe(SIGN_IN)
    // The whole of "no LDAP redirect inside the iframe": a link that targets this frame would put
    // someone else's login page on NOA's card URL.
    expect(link.getAttribute('target')).toBe('_blank')
    // The tab this opens carries the operator's session; it has no business holding a handle back.
    expect(link.getAttribute('rel')).toContain('noopener')
    expect(link.getAttribute('rel')).toContain('noreferrer')
  })

  it('prints the address as text too, for the sandbox that blocks the link', () => {
    // `allow-popups` is absent at one of LibreChat's two render sites, and there the click above
    // opens nothing and says nothing. The printed address is the door that does not depend on it.
    const { onRetry } = retryStub('unauthenticated')
    const { container } = render(<SignInNotice signInUrl={SIGN_IN} onRetry={onRetry} frameOrigin={null} />)

    expect(container.textContent).toContain('copy this address')
    // Twice on the card on purpose: once as the link's target, once as text to copy.
    expect(screen.getByText(SIGN_IN)).toBeTruthy()
  })

  it('renders no link at all when none is configured, and still says what to do', () => {
    // The separating case, and the reason `resolveSignInUrl` returns `null` rather than a default: a
    // link to a host nobody deployed reads as an action that was refused (`canDecide`'s judgement).
    const { onRetry } = retryStub('unauthenticated')
    const { container } = render(<SignInNotice signInUrl={null} onRetry={onRetry} frameOrigin={null} />)

    expect(container.querySelector('a')).toBeNull()
    expect(container.textContent).not.toContain('copy this address')
    // Still an explicit state with a way out: the sentence, and the retry.
    expect(screen.getByRole('heading').textContent).toContain('Cannot authenticate here')
    expect(screen.getByRole('button', { name: /try again/i })).toBeTruthy()
  })

  it('carries no form, no input and nothing that submits', () => {
    const { onRetry } = retryStub('unauthenticated')
    const { container } = render(<SignInNotice signInUrl={SIGN_IN} onRetry={onRetry} frameOrigin={null} />)

    expect(container.querySelector('form')).toBeNull()
    expect(container.querySelector('input')).toBeNull()
    expect(container.querySelector('textarea')).toBeNull()
    expect(container.querySelector('[type="submit"]')).toBeNull()
    expect(screen.getByRole('button', { name: /try again/i }).getAttribute('type')).toBe('button')
  })

  it('re-reads on Try again, exactly once per click', async () => {
    const { onRetry } = retryStub('unauthenticated')
    render(<SignInNotice signInUrl={SIGN_IN} onRetry={onRetry} frameOrigin={null} />)

    clickRetry()
    await vi.waitFor(() => expect(screen.getByRole('status')).toBeTruthy())

    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('says so when the session is still not recognised', async () => {
    // A button with no feedback reads as a broken one. The answer that leaves this notice on screen
    // is the answer that has to be reported.
    const { onRetry } = retryStub('unauthenticated')
    render(<SignInNotice signInUrl={SIGN_IN} onRetry={onRetry} frameOrigin={null} />)

    clickRetry()

    await vi.waitFor(() =>
      expect(screen.getByRole('status').textContent).toContain('still does not recognise'),
    )
  })

  it('reports an unreachable NOA as its own answer, not as a refused session', async () => {
    // The separating case for the note above: "could not be asked" and "asked and refused" send an
    // operator to different people.
    const { onRetry } = retryStub('unavailable')
    render(<SignInNotice signInUrl={SIGN_IN} onRetry={onRetry} frameOrigin={null} />)

    clickRetry()

    await vi.waitFor(() =>
      expect(screen.getByRole('status').textContent).toContain('could not be reached'),
    )
  })

  it('holds the button while a read is in flight, then gives it back', async () => {
    // One read per click. Without this a held click queues reads against an endpoint that is
    // answering 401 as fast as it can.
    let release: (load: ApprovalCardLoad) => void = () => {}
    const onRetry = vi.fn(
      () =>
        new Promise<ApprovalCardLoad>((resolve) => {
          release = resolve
        }),
    )
    render(<SignInNotice signInUrl={SIGN_IN} onRetry={onRetry} frameOrigin={null} />)

    clickRetry()

    const button = screen.getByRole('button', { name: /checking/i })
    expect(button.hasAttribute('disabled')).toBe(true)

    // A second click while it is held changes nothing.
    fireEvent.click(button)
    expect(onRetry).toHaveBeenCalledTimes(1)

    release({ kind: 'unauthenticated' })
    await vi.waitFor(() => expect(screen.getByRole('button', { name: /try again/i })).toBeTruthy())
  })

  it('clears a stale note before the next answer lands', async () => {
    // Otherwise the note from the previous attempt sits under a read that is still running, which
    // reads as this attempt's answer.
    let release: (load: ApprovalCardLoad) => void = () => {}
    const onRetry = vi
      .fn<() => Promise<ApprovalCardLoad>>()
      .mockResolvedValueOnce({ kind: 'unauthenticated' })
      .mockImplementationOnce(
        () =>
          new Promise<ApprovalCardLoad>((resolve) => {
            release = resolve
          }),
      )
    render(<SignInNotice signInUrl={SIGN_IN} onRetry={onRetry} frameOrigin={null} />)

    clickRetry()
    await vi.waitFor(() => expect(screen.getByRole('status')).toBeTruthy())

    clickRetry()
    expect(screen.queryByRole('status')).toBeNull()

    release({ kind: 'unauthenticated' })
    await vi.waitFor(() => expect(screen.getByRole('status')).toBeTruthy())
  })

  it('measures itself, so the printed address is not left below the fold', () => {
    // This state's way out is the address as TEXT, because the sandbox at one of LibreChat's two
    // render sites withholds the link. At the 150px box the host opens with, that address
    // began below the fold — reachable by scrolling, and off screen all the same. So the notice asks
    // for a frame it fits in, like the card and the table do.
    //
    // What jsdom can hold is the wiring: a sizer is mounted, and the origin the page resolved is the
    // origin it was given. Whether the frame that results puts the address on screen is a layout
    // claim and lives in `e2e/frame-size-card.browser.e2e.ts`; the arithmetic and the message live
    // in `frame-sizer.test.tsx`.
    const { onRetry } = retryStub('unauthenticated')
    const { container } = render(
      <SignInNotice signInUrl={SIGN_IN} onRetry={onRetry} frameOrigin="https://chat.example" />,
    )

    expect(sizerState(container)).toBe('measuring')
  })

  it('leaves the notice exactly as it was when no origin is trustworthy', () => {
    // The separating case: `null` is a supported state, not a failure. Nothing is posted and the
    // notice still scrolls itself, which is what it did before it measured anything.
    const { onRetry } = retryStub('unauthenticated')
    const { container } = render(
      <SignInNotice signInUrl={SIGN_IN} onRetry={onRetry} frameOrigin={null} />,
    )

    expect(sizerState(container)).toBe('no-target-origin')
    expect(screen.getByText(SIGN_IN)).toBeTruthy()
  })
})
