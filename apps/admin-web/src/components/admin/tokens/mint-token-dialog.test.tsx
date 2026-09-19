import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

import { UserDetailDrawer, type UserDetailDrawerProps } from '@/components/admin/users/user-detail-drawer'
import { looksLikeTokenPlaintext } from '@/lib/admin/tokens/token-plaintext'
import { mintToken } from '@/lib/admin/tokens/tokens-api'
import type { MintedToken, TokenScope } from '@/lib/admin/tokens/types'
import type { MintOutcome } from '@/lib/admin/tokens/use-tokens'
import { ApiError } from '@/lib/auth/fetch-helper'

import { MintTokenDialog } from './mint-token-dialog'

// Every sink a minted plaintext could reach is spied here, and every spy is shown
// catching something real — the controls in the last describe block. A spy wired
// to nothing passes every not-logged assertion ever written; those controls are
// what make the negative results mean something.
const toast = vi.hoisted(() => ({
  success: vi.fn(),
  warning: vi.fn(),
  danger: vi.fn(),
  info: vi.fn(),
}))
const nav = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }))

vi.mock('@gio/bigsu-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@gio/bigsu-ui')>()
  return { ...actual, bigsuToast: toast }
})
vi.mock('next/navigation', () => ({
  useRouter: () => nav,
  usePathname: () => '/me/tokens',
}))

const SELF: TokenScope = { kind: 'self' }
const PLAINTEXT = `noa_${'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ'}`
const PREFIX = 'noa_abcdefgh'

const minted: MintedToken = {
  plaintext: PLAINTEXT,
  token: {
    id: '33333333-3333-4333-8333-333333333333',
    user_id: '44444444-4444-4444-8444-444444444444',
    token_prefix: PREFIX,
    label: 'Laptop',
    librechat_user_id: null,
    last_used_at: null,
    last_ldap_check_at: null,
    expires_at: null,
    created_at: '2026-09-01T09:00:00Z',
  },
}

let consoleError: ReturnType<typeof vi.spyOn>
let setItem: ReturnType<typeof vi.spyOn>

// The dialog is controlled by its host, so the host is modelled rather than
// faked: `open` really goes false on Done / Cancel / Escape, which is what makes
// the clearing assertions about the shipped code and not the test.
function Harness({
  mint,
  onOpenChange,
}: {
  mint: (label: string | null) => Promise<MintOutcome>
  onOpenChange?: (open: boolean) => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button onClick={() => setOpen(true)}>Open mint dialog</button>
      <MintTokenDialog
        open={open}
        onOpenChangeAction={(next) => {
          onOpenChange?.(next)
          setOpen(next)
        }}
        onMintAction={mint}
      />
    </>
  )
}

function openDialog(
  mint: (label: string | null) => Promise<MintOutcome>,
  onOpenChange?: (open: boolean) => void,
) {
  render(<Harness mint={mint} onOpenChange={onOpenChange} />)
  fireEvent.click(screen.getByRole('button', { name: 'Open mint dialog' }))
  return mint
}

async function mintWithLabel(
  mint: (label: string | null) => Promise<MintOutcome>,
  label: string,
): Promise<void> {
  fireEvent.change(screen.getByLabelText('Label'), { target: { value: label } })
  fireEvent.click(screen.getByRole('button', { name: 'Mint token' }))
  await waitFor(() => expect(mint).toHaveBeenCalled())
}

const toastCalls = () => [
  ...toast.success.mock.calls,
  ...toast.warning.mock.calls,
  ...toast.danger.mock.calls,
  ...toast.info.mock.calls,
]

const primaryButtons = () =>
  Array.from(document.querySelectorAll('button')).filter((button) =>
    button.classList.contains('bg-action-primary'),
  )

// The "cleared when the render site closes" probe. `innerHTML` is what the
// write-once display rule specifies and the stronger half — a plaintext in any attribute
// (`title`, `data-*`, `aria-label`) is in the markup and nowhere else. Not a
// SUPERSET though, so the input scan stays: the Label field is an uncontrolled
// react-hook-form input, and what is typed into one lives as a DOM property with
// no attribute to serialise (MEASURED — innerHTML blind, scan sees it). Both
// halves are pinned below; `textContent` goes — /[A-Za-z0-9_-]/ never escapes.
const plaintextInDom = (): boolean =>
  looksLikeTokenPlaintext(document.body.innerHTML) ||
  Array.from(document.querySelectorAll('input, textarea')).some((el) =>
    looksLikeTokenPlaintext((el as HTMLInputElement).value),
  )

// The address-bar sink as one value, so the history-write control below
// exercises the assertion's own expressions rather than a paraphrase of them.
const urlSink = () => ({
  search: window.location.search,
  hrefCarriesPlaintext: looksLikeTokenPlaintext(window.location.href),
})
const CLEAN_URL = { search: '', hrefCarriesPlaintext: false }

// A mint that runs through the REAL transport, so the reporting sink
// (fetch-helper.ts:89,129 → error-reporting.ts:69) is genuinely entered. This
// is the shape a controller wraps it in; the dialog cannot tell the difference.
async function mintThroughTransport(label: string | null): Promise<MintOutcome> {
  try {
    return { ok: true, minted: await mintToken(SELF, label) }
  } catch (error) {
    if (error instanceof ApiError) return { ok: false, message: error.detail }
    return { ok: false, message: 'Could not reach NOA.' }
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
  setItem = vi.spyOn(Storage.prototype, 'setItem')
})

afterEach(() => {
  vi.restoreAllMocks()
  // `restoreAllMocks` does not undo `stubGlobal`, and unstubbing at the end of a
  // body only runs when that body gets there — a stubbed `fetch` surviving one
  // failure turns it into a cascade that names the wrong test.
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

describe('MintTokenDialog — form step', () => {
  it('warns that the value is shown once BEFORE the mint is dispatched', () => {
    const mint = vi.fn<(label: string | null) => Promise<MintOutcome>>()
    openDialog(mint)

    expect(screen.getByRole('note')).toHaveTextContent(/shown once/i)
    expect(screen.getByRole('note')).toHaveTextContent(/cannot be recovered/i)
    expect(mint).not.toHaveBeenCalled()
  })

  it('keeps one primary Button in each of the two steps', async () => {
    const mint = vi.fn().mockResolvedValue({ ok: true, minted })
    openDialog(mint)

    // The form step: Cancel is outline, Mint token is the one primary.
    expect(primaryButtons().map((button) => button.textContent)).toEqual(['Mint token'])

    await mintWithLabel(mint, 'Laptop')
    expect(await screen.findByLabelText('Token')).toHaveValue(PLAINTEXT)
    // The minted step REPLACES it (BIGSU never nests dialogs): Copy token is
    // secondary, Done is the one primary.
    expect(primaryButtons().map((button) => button.textContent)).toEqual(['Done'])
  })

  it('trims the label and sends null for a blank one', async () => {
    const mint = vi.fn().mockResolvedValue({ ok: true, minted })
    openDialog(mint)

    await mintWithLabel(mint, '   Laptop   ')
    expect(mint).toHaveBeenCalledWith('Laptop')

    mint.mockClear()
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    fireEvent.click(screen.getByRole('button', { name: 'Open mint dialog' }))
    await mintWithLabel(mint, '   ')
    expect(mint).toHaveBeenCalledWith(null)
  })

  it('blocks an over-long label client-side, without a round trip', async () => {
    const mint = vi.fn<(label: string | null) => Promise<MintOutcome>>()
    openDialog(mint)

    fireEvent.change(screen.getByLabelText('Label'), { target: { value: 'x'.repeat(256) } })
    fireEvent.click(screen.getByRole('button', { name: 'Mint token' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/255 characters or fewer/i)
    expect(mint).not.toHaveBeenCalled()
  })

  it('surfaces a server 400 verbatim against the field', async () => {
    const message = 'Label must be 255 characters or fewer'
    const mint = vi.fn().mockResolvedValue({ ok: false, message })
    openDialog(mint)
    await mintWithLabel(mint, 'Laptop')

    expect(await screen.findByRole('alert')).toHaveTextContent(message)
    expect(toast.danger).toHaveBeenCalledWith('Could not create the token', {
      description: message,
    })
  })

  it('says nothing when a 401 is already navigating away', async () => {
    const mint = vi.fn().mockResolvedValue({ redirecting: true })
    openDialog(mint)
    await mintWithLabel(mint, 'Laptop')

    expect(toastCalls()).toHaveLength(0)
    expect(screen.getByRole('button', { name: 'Mint token' })).toBeInTheDocument()
  })
})

describe('MintTokenDialog — show-once plaintext', () => {
  it('toasts the label and the prefix, never the value', async () => {
    const mint = vi.fn().mockResolvedValue({ ok: true, minted })
    openDialog(mint)
    await mintWithLabel(mint, 'Laptop')

    await waitFor(() => expect(toast.success).toHaveBeenCalled())
    // The reachability control for this spy set: the success toast DOES
    // carry real content, so "no plaintext seen" is not "nothing seen".
    expect(toast.success).toHaveBeenCalledWith('MCP token created', {
      description: `${PREFIX} · Laptop`,
    })
    expect(looksLikeTokenPlaintext(toastCalls())).toBe(false)
  })

  it('keeps every toast clean on the failure path too', async () => {
    const mint = vi.fn().mockResolvedValue({ ok: false, message: 'Label rejected' })
    openDialog(mint)
    await mintWithLabel(mint, 'Laptop')

    await waitFor(() => expect(toast.danger).toHaveBeenCalled())
    expect(looksLikeTokenPlaintext(toastCalls())).toBe(false)
  })

  it('discards the value on Done and re-opens on an empty form step', async () => {
    const mint = vi.fn().mockResolvedValue({ ok: true, minted })
    openDialog(mint)
    await mintWithLabel(mint, 'Laptop')

    expect(await screen.findByLabelText('Token')).toHaveValue(PLAINTEXT)
    // The probe separates on the real render — without this line the assertion
    // below would read the same against a probe that sees nothing.
    expect(plaintextInDom()).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))

    await waitFor(() => expect(plaintextInDom()).toBe(false))

    fireEvent.click(screen.getByRole('button', { name: 'Open mint dialog' }))
    expect(await screen.findByRole('button', { name: 'Mint token' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Token')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Label')).toHaveValue('')
    expect(plaintextInDom()).toBe(false)
  })

  it('discards the value on Escape as well', async () => {
    const mint = vi.fn().mockResolvedValue({ ok: true, minted })
    openDialog(mint)
    await mintWithLabel(mint, 'Laptop')
    expect(await screen.findByLabelText('Token')).toHaveValue(PLAINTEXT)

    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape', code: 'Escape' })

    await waitFor(() => expect(plaintextInDom()).toBe(false))
  })

  it('never navigates or writes to storage between mint and close', async () => {
    const mint = vi.fn().mockResolvedValue({ ok: true, minted })
    openDialog(mint)

    // The window opens at dispatch and closes when the dialog does — a bounded
    // one, not "some time after a mint".
    nav.push.mockClear()
    nav.replace.mockClear()
    setItem.mockClear()

    await mintWithLabel(mint, 'Laptop')
    expect(await screen.findByLabelText('Token')).toHaveValue(PLAINTEXT)
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    await waitFor(() => expect(plaintextInDom()).toBe(false))

    expect(nav.push).not.toHaveBeenCalled()
    expect(nav.replace).not.toHaveBeenCalled()
    expect(looksLikeTokenPlaintext(setItem.mock.calls)).toBe(false)
    // The address bar (not in a URL), bounded as the control below
    // measures — this sees a client-side history write, nothing else. A native
    // GET from Enter in a form field is invisible under jsdom, so it is asserted
    // structurally instead in minted-token-panel.test.tsx:88.
    expect(urlSink()).toEqual(CLEAN_URL)
  })

  it('hands its host nothing that carries the value', async () => {
    const outcome: MintOutcome = { ok: true, minted }
    const onOpenChange = vi.fn()
    const mint = vi.fn().mockResolvedValue(outcome)
    openDialog(mint, onOpenChange)
    await mintWithLabel(mint, 'Laptop')
    expect(await screen.findByLabelText('Token')).toHaveValue(PLAINTEXT)

    // Control: the mint RETURN is the carrier, and the detector demonstrably
    // finds it there. Without this line the two assertions below would pass
    // just as happily against a detector that matches nothing.
    expect(looksLikeTokenPlaintext(outcome)).toBe(true)

    // The only things the dialog sends upward are the label and the open flag.
    expect(looksLikeTokenPlaintext(mint.mock.calls)).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
    expect(looksLikeTokenPlaintext(onOpenChange.mock.calls)).toBe(false)
  })

  it('never reaches the console on a successful mint', async () => {
    const mint = vi.fn().mockResolvedValue({ ok: true, minted })
    openDialog(mint)
    await mintWithLabel(mint, 'Laptop')
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    await waitFor(() => expect(plaintextInDom()).toBe(false))

    expect(looksLikeTokenPlaintext(consoleError.mock.calls)).toBe(false)
    expect(consoleError).not.toHaveBeenCalled()
  })
})

describe('MintTokenDialog — sink reachability controls', () => {
  it('a 500 mint IS reported, carrying the status and no plaintext', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ error_code: 'internal_error', message: 'Something went wrong' }),
          { status: 500, headers: { 'content-type': 'application/json' } },
        ),
      ),
    )

    const mint = vi.fn(mintThroughTransport)
    openDialog(mint)
    await mintWithLabel(mint, 'Laptop')
    await waitFor(() => expect(consoleError).toHaveBeenCalled())

    // The spy CAN see the reporting sink — and what it saw carries the status,
    // never anything token-shaped.
    expect(JSON.stringify(consoleError.mock.calls)).toContain('500')
    expect(looksLikeTokenPlaintext(consoleError.mock.calls)).toBe(false)
  })

  it('a mint that throws produces an Error with no plaintext in it', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    const mint = vi.fn(mintThroughTransport)
    openDialog(mint)
    await mintWithLabel(mint, 'Laptop')
    await waitFor(() => expect(consoleError).toHaveBeenCalled())

    // The realistic route into console.error is a network throw, reported by
    // fetchWithAuth before it rethrows. Nothing in it is token-shaped, and the
    // detector reads Error fields directly — `JSON.stringify(error)` is '{}'.
    expect(looksLikeTokenPlaintext(consoleError.mock.calls)).toBe(false)
    await waitFor(() => expect(toast.danger).toHaveBeenCalled())
    expect(looksLikeTokenPlaintext(toastCalls())).toBe(false)
  })

  // Read this one for what it is. Nothing under `src/components/admin/tokens` or
  // `src/lib/admin/tokens` imports `next/navigation` — only this test file mocks
  // it — so "the router was not called" holds by CONSTRUCTION. The control proves
  // the spy is wired to the module the app would navigate through, so a later
  // edit that does reach for the router shows up; it is not evidence of a risk
  // the dialog could otherwise take.
  it('the router spy sees a real navigation from the drawer link', () => {
    const props = {
      user: { id: 'member-1', email: 'grace@example.com', roles: ['member'], is_active: true },
      me: { id: 'me-1', email: 'root@example.com', is_active: true, roles: ['admin'] },
      allUsers: [{ id: 'member-1', email: 'grace@example.com', roles: ['member'], is_active: true }],
      availableRoles: ['member'],
      onCloseAction: vi.fn(),
      onSaveRolesAction: vi.fn().mockResolvedValue({ ok: true, current: true }),
      onSetActiveAction: vi.fn().mockResolvedValue({ ok: true, current: true }),
      onDeleteAction: vi.fn().mockResolvedValue({ ok: true, current: true }),
    }
    render(<UserDetailDrawer {...(props as unknown as UserDetailDrawerProps)} />)

    fireEvent.click(screen.getByRole('button', { name: 'MCP tokens' }))
    expect(nav.push).toHaveBeenCalledWith('/admin/users/member-1/tokens')
  })

  // #10 deleted the app's only localStorage writer, so that claim holds by CONSTRUCTION now.
  it('the storage setter spy sees a real write', () => {
    window.localStorage.setItem('web-bigsu:spy-control', 'v')

    expect(setItem).toHaveBeenCalled()
    expect(setItem.mock.calls[0]?.[0]).toBe('web-bigsu:spy-control')
    // …and the same spy, seeing that, saw nothing token-shaped above.
    expect(looksLikeTokenPlaintext(setItem.mock.calls)).toBe(false)
  })

  it('the URL check can fail: a history write IS seen', () => {
    try {
      // The sink-naming rule's other branch: rather than delete the URL assertion,
      // give it a control. This is the one URL sink jsdom implements, and the shape a leak
      // would take here — App Router navigations land in `window.history`, and
      // Next documents `pushState` as a supported way to set the URL. It bounds
      // the claim too: `location.assign`, writing `location.search` and a native
      // `<form method="get">` submit are all refused by jsdom (MEASURED).
      window.history.pushState({}, '', `/me/tokens?token=${PLAINTEXT}`)
      expect(urlSink()).toEqual({ search: `?token=${PLAINTEXT}`, hrefCarriesPlaintext: true })
    } finally {
      window.history.pushState({}, '', '/')
    }
    expect(urlSink()).toEqual(CLEAN_URL)
  })

  it('each DOM probe half catches what the other cannot, and it separates', () => {
    // Attribute-only: `innerHTML` sees it, an input scan cannot.
    const { rerender, unmount } = render(<div title={PLAINTEXT} />)
    expect(looksLikeTokenPlaintext(document.body.innerHTML)).toBe(true)
    expect(plaintextInDom()).toBe(true)

    // Property-only, how an uncontrolled field carries what was typed into it —
    // the dialog's own Label input. Markup does NOT hold it, so dropping the
    // input scan as redundant would blind the probe to a real carrier.
    rerender(<input />)
    screen.getByRole<HTMLInputElement>('textbox').value = PLAINTEXT
    expect(looksLikeTokenPlaintext(document.body.innerHTML)).toBe(false)
    expect(plaintextInDom()).toBe(true)

    unmount()
    expect(plaintextInDom()).toBe(false)
  })
})
