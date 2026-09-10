import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useForm } from 'react-hook-form'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LoginForm } from './login-form'

/**
 * The login POST does not travel through a form submission (§T.50 — V94, R32).
 *
 * **Why this is a test and not a comment.** `NOA_SIGN_IN_URL` points at this route, and one of the
 * two ways an operator arrives is a click on the embed 401 card's link-out. R32 measured that tab:
 * top-level, but it inherits the frame's sandbox, and `allow-forms` is absent at both of
 * LibreChat's render sites. A sandboxed document returns at the sandbox check *before* the
 * `submit` event is fired, so a submit-driven login is refused with nothing an operator can see —
 * V80's failure shape, one origin over. The property that survives that is: no form submission
 * participates in the POST at all.
 *
 * **What this lane can and cannot claim.** jsdom does not implement sandboxing, so these specs do
 * not re-measure LibreChat's frame — that is R32's, in `apps/web-embed/e2e/sign-in.browser.e2e.ts`,
 * at both pinned sandbox strings. What is bound here is the half that is NOA's: the fetch is
 * reachable with the `submit` event never firing, and the control that reaches it is not a submit
 * button. A negative control pairs with it, because "zero submit events" passes just as well
 * against a button that does nothing.
 */

const nav = vi.hoisted(() => {
  const push = vi.fn()
  return { push, router: { push, replace: vi.fn(), refresh: () => {} } }
})

const search = vi.hoisted(() => ({ params: new URLSearchParams() }))

vi.mock('next/navigation', () => ({
  useRouter: () => nav.router,
  useSearchParams: () => search.params,
}))

const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })

/** Count every `submit` event that reaches the document, capture phase included. */
function countSubmits(): { count: () => number; stop: () => void } {
  let seen = 0
  const listener = () => {
    seen += 1
  }
  document.addEventListener('submit', listener, true)
  return {
    count: () => seen,
    stop: () => document.removeEventListener('submit', listener, true),
  }
}

function fillCredentials() {
  fireEvent.change(screen.getByLabelText(/email/i), {
    target: { value: 'operator@biznetgio.com' },
  })
  fireEvent.change(screen.getByLabelText(/password/i), { target: { value: 'sekret-1' } })
}

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  vi.clearAllMocks()
  search.params = new URLSearchParams()
  fetchMock = vi.fn(() =>
    Promise.resolve(jsonResponse(200, { user: { id: 'u1', email: 'operator@biznetgio.com' } })),
  )
  vi.stubGlobal('fetch', fetchMock)
  window.localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('§T.50 — the sign-in control', () => {
  it('signs in with no form submission event at all', async () => {
    const submits = countSubmits()
    render(<LoginForm />)
    fillCredentials()

    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    const [url, init] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/auth/login')
    expect((init as RequestInit).method).toBe('POST')
    expect((init as RequestInit).credentials).toBe('include')

    // The assertion R32 makes necessary: the credential reached the API without the one mechanism
    // the sandbox withholds.
    expect(submits.count()).toBe(0)
    submits.stop()
  })

  it('separates — a submit-driven login records the event this one does not', async () => {
    // The negative control. Without it, "zero submit events" is satisfied by a button that never
    // did anything, and this whole lane would pass against a broken page. The fixture is the
    // shape §T.50 rejects: one `<form onSubmit>` and a `type="submit"` button.
    const submits = countSubmits()

    function SubmitDrivenLogin() {
      const { handleSubmit } = useForm<{ email: string }>({ defaultValues: { email: 'a@b.test' } })
      return (
        <form onSubmit={handleSubmit(() => {})}>
          <button type="submit">Sign in</button>
        </form>
      )
    }

    render(<SubmitDrivenLogin />)
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    await waitFor(() => expect(submits.count()).toBe(1))
    submits.stop()
  })

  it('the sign-in control is not a submit button, and the form carries no action', () => {
    render(<LoginForm />)

    // Asserted on the published attribute rather than on behaviour: jsdom fires `submit` for a
    // submit button, so behaviour alone cannot tell this page from the fixture above.
    expect(screen.getByRole('button', { name: 'Sign in' })).toHaveAttribute('type', 'button')

    const form = document.querySelector('form')
    expect(form).not.toBeNull()
    // No non-JS fallback: an `action` would post the credential somewhere on the one path where
    // forms are inert anyway.
    expect(form).not.toHaveAttribute('action')
  })

  it('the form path routes to the same handler, once', async () => {
    // The copied-address case §T.50 names: a fresh tab has no opener to inherit a sandbox from, so
    // Enter in a field and a password manager's submit both work there. One handler serves both
    // triggers — two definitions would be two places for the endpoint to drift.
    render(<LoginForm />)
    fillCredentials()

    const form = document.querySelector('form')!
    const submitted = fireEvent.submit(form)

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(fetchMock.mock.calls[0]![0]).toBe('/api/auth/login')
    // `preventDefault` ran, so no navigation is attempted where forms do work.
    expect(submitted).toBe(false)
  })
})

describe('§T.50 — after the verdict', () => {
  it('lands on the sanitized returnTo', async () => {
    search.params = new URLSearchParams({ returnTo: '/admin/roles' })
    render(<LoginForm />)
    fillCredentials()

    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    await waitFor(() => expect(nav.push).toHaveBeenCalledWith('/admin/roles'))
  })

  it('refuses a returnTo that would leave this origin, using the shared guard', async () => {
    // `return-to.ts` owns the rule and has its own specs; what is asserted here is that this page
    // goes through it rather than trusting the query.
    search.params = new URLSearchParams({ returnTo: '//evil.example/x' })
    render(<LoginForm />)
    fillCredentials()

    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    // The fallback is `DEFAULT_RETURN_TO`, which §T76 repointed to the role-aware dispatcher:
    // landing every rejected returnTo on `/admin/users` sent non-admins to a 403.
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith('/home'))
  })

  it('shows the vague refusal on bad credentials and never navigates', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(401, {
        error_code: 'invalid_credentials',
        detail: "no such user in ou=people: bind failed for 'operator@biznetgio.com'",
        request_id: 'req-1',
      }),
    )
    render(<LoginForm />)
    fillCredentials()

    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText('Your email or password is incorrect.')).toBeInTheDocument()
    // The backend `detail` is a diagnostic, not copy: echoing it here would name which half of the
    // credential was wrong, and it carries the submitted email.
    expect(screen.queryByText(/ou=people/)).toBeNull()
    expect(document.body.textContent).not.toContain('sekret-1')
    expect(nav.push).not.toHaveBeenCalled()
  })

  it('does not post at all until both fields validate', async () => {
    render(<LoginForm />)

    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText('Email is required.')).toBeInTheDocument()
    expect(screen.getByText('Password is required.')).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('names why the operator is here when session.ts said so', async () => {
    search.params = new URLSearchParams({ reason: 'session_expired' })
    render(<LoginForm />)

    expect(screen.getByRole('status')).toHaveTextContent('Your session has expired')
  })

  it('separates — an unknown reason renders no notice', () => {
    // Otherwise a banner that always rendered would satisfy the spec above.
    search.params = new URLSearchParams({ reason: 'made-up' })
    render(<LoginForm />)

    expect(screen.queryByRole('status')).toBeNull()
  })
})
