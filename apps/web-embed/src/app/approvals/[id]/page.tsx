import { headers } from 'next/headers'

import { loadApprovalCard } from '@/lib/approvals/detail'

import { CardView } from './card-view'

/**
 * The approval card (§T.41, §T.42 — §I.embed, V27, V29, V32, V33, V34, V35, V38, V39, V80).
 *
 * One URL owns the whole lifecycle of one request (V34): this page asks the question while the
 * request is PENDING, and `CardView` keeps re-reading the row until there is nothing left to wait
 * for — so an operator who approves a change watches it finish here rather than reloading to find
 * out, and one who opens the link a second time reads the outcome rather than a dead page.
 *
 * **This half is the server's, and it is the authenticated one.** The incoming `Cookie` header goes
 * with the read (`lib/approvals/detail.ts`), so the HTML that reaches the frame is already the
 * operator's card: no empty box while a client fetch decides who they are, and the CSRF token
 * (V39) is part of the render rather than something a page could ask for without having been
 * allowed to read the request first. Everything after that first read is `CardView`'s.
 *
 * The only decision that leaves this page is a JS `fetch`, because the sandbox this frame runs
 * under omits `allow-forms` (V80, R13, R29). Nothing here is a `<form>`.
 */

// Reading `headers()` already opts this route out of prerendering; saying so as well means a
// future edit that stops reading them cannot quietly make one operator's card cacheable for the
// next request. Same declaration the proxy route carries (§T.44(g)).
export const dynamic = 'force-dynamic'

export default async function ApprovalCardPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  const cookie = (await headers()).get('cookie')

  return <CardView initial={await loadApprovalCard(id, { cookie })} />
}
