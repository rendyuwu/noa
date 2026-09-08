import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Dialog } from '@gio/bigsu-ui'

import { looksLikeTokenPlaintext } from '@/lib/admin/tokens/token-plaintext'
import type { MintedToken } from '@/lib/admin/tokens/types'

import { MintedTokenPanel } from './minted-token-panel'

// There is no precedent in this package for mocking `@gio/bigsu-ui` — 0 of the
// 24 vi.mock calls in src touch it — so the mechanism is spelled out once here
// and reused: keep EVERY real export (the components under test are in this
// module) and replace only `bigsuToast`, with all four of its methods spied. A
// partial mock that dropped the components would fail the render, and a mock
// that spied only `.success` would let a plaintext ride out on `.danger`.
const toast = vi.hoisted(() => ({
  success: vi.fn(),
  warning: vi.fn(),
  danger: vi.fn(),
  info: vi.fn(),
}))

vi.mock('@gio/bigsu-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@gio/bigsu-ui')>()
  return { ...actual, bigsuToast: toast }
})

const PLAINTEXT = `noa_${'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ'}`

const minted: MintedToken = {
  plaintext: PLAINTEXT,
  token: {
    id: '33333333-3333-4333-8333-333333333333',
    user_id: '44444444-4444-4444-8444-444444444444',
    token_prefix: 'noa_abcdefgh',
    label: 'LibreChat on my laptop',
    librechat_user_id: null,
    last_used_at: null,
    last_ldap_check_at: null,
    expires_at: null,
    created_at: '2026-09-01T09:00:00Z',
  },
}

function renderPanel() {
  const onDone = vi.fn()
  render(
    <Dialog open onOpenChange={() => {}}>
      <MintedTokenPanel minted={minted} onDoneAction={onDone} />
    </Dialog>,
  )
  return { onDone, field: screen.getByLabelText('Token') as HTMLInputElement }
}

const toastCalls = () => [
  ...toast.success.mock.calls,
  ...toast.warning.mock.calls,
  ...toast.danger.mock.calls,
  ...toast.info.mock.calls,
]

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  Reflect.deleteProperty(navigator, 'clipboard')
})

describe('MintedTokenPanel', () => {
  it('renders the plaintext in exactly one place, in a readOnly monospace field', () => {
    const { field } = renderPanel()

    expect(field.value).toBe(PLAINTEXT)
    expect(field).toHaveAttribute('readonly')
    expect(field.className).toContain('font-mono')

    // Exactly one site: one input carries it, and no text node does — a value
    // rendered as text would be selectable, screen-readable and copied by any
    // "select all" on the page.
    const carriers = Array.from(document.querySelectorAll('input')).filter((input) =>
      looksLikeTokenPlaintext(input.value),
    )
    expect(carriers).toHaveLength(1)
    expect(looksLikeTokenPlaintext(document.body.textContent)).toBe(false)
  })

  it('keeps the field out of any form, unnamed, and out of autofill', () => {
    const { field } = renderPanel()

    // R2: inside a <form>, Enter re-fires submit or performs a native GET that
    // puts the credential in the query string, the address bar and the history.
    expect(field.closest('form')).toBeNull()
    expect(field.getAttribute('name')).toBeNull()
    expect(field).toHaveAttribute('autocomplete', 'off')
  })

  it('shows the value with the prefix and a shown-once warning', () => {
    renderPanel()

    expect(screen.getByTestId('minted-token-prefix')).toHaveTextContent(minted.token.token_prefix)
    expect(screen.getByRole('note')).toHaveTextContent(/shown once/i)
    expect(screen.getByRole('note')).toHaveTextContent(/cannot be recovered/i)
  })

  it('falls back to selecting the field when there is no clipboard', () => {
    // R4: jsdom stubs no clipboard, so ABSENT is the default and this is the
    // path that runs unless a test provides one. Assert the premise, or the
    // fallback silently stops being the covered branch the day jsdom adds one.
    expect(navigator.clipboard).toBeUndefined()

    const { field } = renderPanel()
    fireEvent.click(screen.getByRole('button', { name: /copy token/i }))

    expect(field.selectionStart).toBe(0)
    expect(field.selectionEnd).toBe(PLAINTEXT.length)
    expect(toast.warning).toHaveBeenCalledTimes(1)
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('copies through the clipboard when one exists', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })

    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: /copy token/i }))

    // The operator's own clipboard is the ONE sink the value is allowed to
    // reach — it is the whole point of the affordance.
    expect(writeText).toHaveBeenCalledWith(PLAINTEXT)
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1))
    expect(toast.success.mock.calls[0]?.[1]).toEqual({
      description: minted.token.token_prefix,
    })
  })

  it('re-selects the field and warns when the clipboard write is refused', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('denied'))
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })

    const { field } = renderPanel()
    fireEvent.click(screen.getByRole('button', { name: /copy token/i }))

    await waitFor(() => expect(toast.danger).toHaveBeenCalledTimes(1))
    expect(field.selectionEnd).toBe(PLAINTEXT.length)
  })

  it('never hands the plaintext to any toast method, on any path', async () => {
    // Reachability control for this spy set: the success toast DOES carry the
    // prefix, so a spy seeing nothing cannot be what makes the assertion pass.
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })

    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: /copy token/i }))
    await waitFor(() => expect(toast.success).toHaveBeenCalled())

    expect(looksLikeTokenPlaintext(toastCalls())).toBe(false)
    expect(JSON.stringify(toastCalls())).toContain(minted.token.token_prefix)
  })

  it('hands control back to the host on Done', () => {
    const { onDone } = renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    expect(onDone).toHaveBeenCalledTimes(1)
  })
})
