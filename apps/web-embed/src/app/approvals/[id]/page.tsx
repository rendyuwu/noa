import { headers } from 'next/headers'

import { loadApprovalCard } from '@/lib/approvals/detail'
import { resolveFrameTargetOrigin } from '@/lib/embed/frame-origin'
import { resolveSignInUrl } from '@/lib/sign-in'

import { CardView } from './card-view'

/**
 * The approval card.
 *
 * One URL owns the whole lifecycle of one request: this page asks the question while the
 * request is PENDING, and `CardView` keeps re-reading the row until there is nothing left to wait
 * for — so an operator who approves a change watches it finish here rather than reloading to find
 * out, and one who opens the link a second time reads the outcome rather than a dead page.
 *
 * **This half is the server's, and it is the authenticated one.** The incoming `Cookie` header goes
 * with the read (`lib/approvals/detail.ts`), so the HTML that reaches the frame is already the
 * operator's card: no empty box while a client fetch decides who they are, and the CSRF token
 * is part of the render rather than something a page could ask for without having been
 * allowed to read the request first. Everything after that first read is `CardView`'s.
 *
 * The only decision that leaves this page is a JS `fetch`, because the sandbox this frame runs
 * under omits `allow-forms`. Nothing here is a `<form>`.
 *
 * **The sign-in address is resolved here** rather than in the component that renders it: it
 * comes from a server-side variable with no `NEXT_PUBLIC_*` twin, so the page reads it and passes it
 * down. Resolved per request rather than in `next.config.ts`, because `output: 'standalone'` never
 * runs that config at runtime — the framing header is baked there on purpose, and this is not.
 *
 * **The frame origin is the one value here that does have such a twin, and it is not the same kind
 * of value.** The card asks the host to size its frame (`components/frame-sizer.tsx`) and the
 * message needs a target origin naming the same deployment the `frame-ancestors` header names. Both
 * are baked at `next build` — the header out of `next.config.ts`, the message target out of
 * `NEXT_PUBLIC_NOA_LIBRECHAT_ORIGIN` — so neither can be moved by a runtime variable, and a
 * request-time read of the private name would be a second answer free to disagree with the baked
 * one. Hence `resolveFrameTargetOrigin()` takes no environment and is handed none: only a
 * `NEXT_PUBLIC_*` name gets compiled into the output, so the accessor has to name that variable
 * itself rather than receive whatever environment this page happens to hold. What is baked and
 * what is not was measured rather than assumed — `lib/embed/frame-origin.ts` records the readings,
 * along with why the read is wrapped and what an unconfigured one looks like from an operator's
 * side.
 */

// Reading `headers()` already opts this route out of prerendering; saying so as well means a
// future edit that stops reading them cannot quietly make one operator's card cacheable for the
// next request. Same declaration the proxy route carries.
export const dynamic = 'force-dynamic'

export default async function ApprovalCardPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  const cookie = (await headers()).get('cookie')

  return (
    <CardView
      initial={await loadApprovalCard(id, { cookie })}
      actionRequestId={id}
      signInUrl={resolveSignInUrl(process.env)}
      frameOrigin={resolveFrameTargetOrigin()}
    />
  )
}
