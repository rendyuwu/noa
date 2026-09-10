import { ApiError } from '@/lib/auth/fetch-helper'

// Operator-facing copy for the admin API's `/auth/login` contract.
//
// The raw backend `detail` is never echoed on the credential path. The no-internal-text-in-a-body
// rule already keeps the submitted value out of the response body API-side; this is the browser
// half of the same rule: a precise
// "which field was wrong" message is a credential-enumeration aid, so every
// invalid-credential failure — wrong password, unknown account, LDAP says disabled — answers with
// one deliberately vague pair. No password, token or cookie ever reaches this copy.

export type LoginMessage = { title: string; description: string }

const INVALID: LoginMessage = {
  title: 'Sign-in failed',
  description: 'Your email or password is incorrect.',
}

const FALLBACK: LoginMessage = {
  title: 'Sign-in failed',
  description: 'Something went wrong signing you in. Try again.',
}

export const loginErrorMessage = (error: unknown): LoginMessage => {
  if (error instanceof ApiError) {
    switch (error.errorCode) {
      case 'invalid_credentials':
        return INVALID
      case 'user_pending_approval':
        // A new LDAP user is provisioned `is_active=False`. The account exists and the
        // credentials were right, so this one is allowed to be specific — it names a state an
        // administrator has to clear, not a field an attacker can probe.
        return {
          title: 'Account pending approval',
          description:
            'Your account is awaiting administrator approval. You can sign in once it is activated.',
        }
      case 'login_rate_limited':
        // 429 with `Retry-After`. The wait is stated without the number, which is a server
        // detail this copy would have to keep in step with.
        return {
          title: 'Too many attempts',
          description: 'Too many sign-in attempts. Wait a moment before trying again.',
        }
      case 'authentication_service_unavailable':
        // The fail-closed side of an unreachable LDAP: nobody can sign in. Distinct from bad
        // credentials, because retrying the password is not the remedy.
        return {
          title: 'Sign-in unavailable',
          description: 'The sign-in service is temporarily unavailable. Try again shortly.',
        }
    }

    // An unrecognised 401 is still a refused credential. Falling through to the generic copy would
    // read as a transport fault and invite a retry that cannot succeed.
    if (error.status === 401) return INVALID
  }

  return FALLBACK
}
