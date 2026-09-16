'use client'

import { useRouter, useSearchParams } from 'next/navigation'
import { BiznetGioWordmark } from '@gio/bigsu-app-shell'
import { Button, ErrorState, FormField, Input } from '@gio/bigsu-ui'
import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { z } from 'zod'

import { loginErrorMessage, type LoginMessage } from '@/app/login/login-messages'
import { setStoredUser, type AuthUser } from '@/lib/auth/auth-store'
import { getApiUrl, jsonOrThrow } from '@/lib/auth/fetch-helper'
import { sanitizeReturnTo } from '@/lib/auth/return-to'
import { submitOnEnter } from '@/lib/forms/submit-on-enter'

/**
 * LDAP sign-in — admin auth/session plumbing, the admin API's `/auth/login` contract.
 *
 * BIGSU's standard entry is an SSO hand-off; this scoped email/password form exists because NOA
 * authenticates operators against LDAP. The Biznet Gio wordmark is used verbatim, the page
 * keeps one primary action, and FastAPI stays the source of truth — nothing here decides anything
 * about the session beyond posting the credential and reading the verdict.
 *
 * **The primary action is a `type="button"` click handler, not a form submit, and that is the whole
 * point of this file.** `NOA_SIGN_IN_URL` points at this route, and one of the two
 * places an operator arrives from is a click on the embed 401 card's link-out. MEASURED: that
 * tab is top-level, but it inherits the frame's sandbox, and `allow-forms` is absent at both
 * of LibreChat's render sites. A sandboxed document never even fires the `submit` event — the
 * form submission algorithm returns at the sandbox check, before the event — so a login built on
 * `<form onSubmit>` plus a submit button is silently inert in exactly the tab NOA sent the operator
 * to. Nothing throws and nothing appears; the button just does nothing. The embed's fetch-not-form
 * rule is the same shape one origin over.
 *
 * The `<form>` element stays, with an `onSubmit` that routes to the SAME handler (reuse over
 * duplication, one definition), for whatever still reaches it — a password manager that calls
 * `requestSubmit()`, say. It carries no `action`, so there is no non-JS path that would post a
 * credential anywhere.
 *
 * **Enter is `onKeyDown`, and it has to be.** This file used to claim that Enter worked here for an
 * operator who copied the address into a fresh tab. It did not, in any tab. MEASURED in Chromium
 * 151 against this page: Enter in the email field and Enter in the password field each produced
 * zero `submit` events and zero requests, while clicking the button posted once. The rule is the
 * browser's: with no submit button associated with it, a form carrying more than one field that
 * blocks implicit submission has nothing for Enter to click, and this form carries two. The jsdom
 * spec below could not catch it, because `fireEvent.submit` dispatches the event directly and skips
 * implicit submission entirely. `submitOnEnter` routes Enter to the same handler in JS, which also
 * means it works in the sandboxed tab, where a hidden submit button would be just as inert as a
 * visible one.
 */

const schema = z.object({
  email: z.string().min(1, 'Email is required.').email('Enter a valid email address.'),
  password: z.string().min(1, 'Password is required.'),
})

type LoginValues = z.infer<typeof schema>
type LoginResponse = { user?: AuthUser | null }

/** What `session.ts` puts in `?reason=` when it sends an operator here (`ClearAuthReason`). */
const ARRIVAL_NOTICE: Record<string, string> = {
  session_expired: 'Your session has expired. Please sign in again.',
  logged_out: 'You have been signed out.',
}

export function LoginForm() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const notice = ARRIVAL_NOTICE[searchParams.get('reason') ?? '']
  const [formError, setFormError] = useState<LoginMessage | null>(null)

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: '', password: '' },
  })

  const signIn = handleSubmit(async (values) => {
    setFormError(null)
    try {
      // Same-origin proxy only — never the backend origin, never `NOA_API_URL` (which is
      // server-only anyway). A plain `fetch`, not `fetchWithAuth`: a 401 here means the credential
      // was refused, not that a session expired, so it must not start the clear-and-redirect flow
      // that would bounce this page back to itself.
      const response = await fetch(`${getApiUrl()}/auth/login`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(values),
      })
      const payload = await jsonOrThrow<LoginResponse>(response)

      // Presentation cache only (`auth-store.ts`). The authority is the httpOnly `noa_session`
      // cookie the response just set plus the `/auth/me` re-read the protected layout
      // runs on arrival.
      setStoredUser(payload.user ?? null)
      router.push(sanitizeReturnTo(searchParams.get('returnTo')))
    } catch (error) {
      // The password is never logged and never rendered; the codes this maps carry no secret.
      setFormError(loginErrorMessage(error))
    }
  })

  return (
    <main className="flex min-h-dvh items-center justify-center bg-app px-4 py-10">
      <div className="w-full max-w-md">
        <div className="mb-8 flex flex-col items-center gap-4 text-center">
          <BiznetGioWordmark />
          <div>
            <h1 className="text-2xl font-semibold text-text-primary">Sign in to NOA</h1>
            <p className="mt-1 text-sm text-text-secondary">
              Use your Biznet Gio LDAP email and password.
            </p>
          </div>
        </div>

        {notice ? (
          <div
            role="status"
            className="mb-4 rounded-lg border border-border-default bg-status-info-soft px-4 py-3 text-sm text-text-secondary"
          >
            {notice}
          </div>
        ) : null}

        {/*
         * Three triggers, one handler: the button's click, Enter in a field, and whatever still
         * dispatches a submit. None of them is a submit button.
         */}
        <form
          noValidate
          onSubmit={signIn}
          onKeyDown={submitOnEnter(signIn)}
          aria-busy={isSubmitting}
          className="flex flex-col gap-4"
        >
          <FormField label="Email" required errorText={errors.email?.message}>
            <Input
              type="email"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              placeholder="e.g. operator@biznetgio.com"
              {...register('email')}
            />
          </FormField>

          <FormField label="Password" required errorText={errors.password?.message}>
            <Input type="password" autoComplete="current-password" {...register('password')} />
          </FormField>

          {/*
           * `type="button"`, deliberately. A submit button is refused without a sound in
           * a sandbox-inheriting tab, and this is the tab the embed's 401 link-out opens.
           */}
          <Button
            type="button"
            variant="primary"
            size="lg"
            loading={isSubmitting}
            className="w-full"
            onClick={() => void signIn()}
          >
            Sign in
          </Button>

          {formError ? (
            <ErrorState title={formError.title} description={formError.description} />
          ) : null}
        </form>
      </div>
    </main>
  )
}
