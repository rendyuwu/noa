'use client'

import { PageHeader } from '@gio/bigsu-app-shell'

import { TokensPanel } from '@/components/admin/tokens/tokens-panel'
import { useAuthUser } from '@/lib/auth/auth-context'

// /me/tokens — this deployable's only non-admin surface. It calls no auth
// hook: the protected layout already ran the single `/auth/me` revalidation and
// shares the verdict through AuthUserProvider, so a second round trip here would
// re-ask a question already answered.
//
// It gates nothing either, and that is correct rather than lax. `/me/mcp-tokens`
// reads the operator id off the session (mcp_tokens.py:205-242) — there is no id
// in the path for a client check to protect, and the only account this page can
// ever act on is the one the cookie already proves. The email is shown so the
// operator can see WHICH account the tokens they are about to mint will speak
// for, which matters when admins hold two.
export default function MyTokensRoute() {
  const user = useAuthUser()

  return (
    <>
      <PageHeader
        breadcrumb={[{ label: 'Account' }, { label: 'MCP tokens' }]}
        title="MCP tokens"
        description={`Tokens that let LibreChat authenticate to NOA as ${user.email}. Paste one into the noa MCP server's customUserVars field. A token is shown once, when it is created.`}
      />

      <TokensPanel scope={{ kind: 'self' }} />
    </>
  )
}
