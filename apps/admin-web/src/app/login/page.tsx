import { Suspense } from 'react'

import { LoginForm } from '@/app/login/login-form'

// The sign-in route (§T.50). Outside `(protected)` on purpose: that layout runs the `/auth/me`
// gate, and a login page behind it would 401 its way back to itself.
//
// The form reads `returnTo` / `reason` from the URL via `useSearchParams`, which the App Router
// requires a Suspense boundary for.
export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  )
}
