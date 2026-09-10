import { headers } from 'next/headers'

import { resolveFrameTargetOrigin } from '@/lib/embed/frame-origin'
import { resolveSignInUrl } from '@/lib/sign-in'
import { loadResultTable } from '@/lib/tables/detail'

import { TableView } from './table-view'

/**
 * The large-READ table surface (the embed app's contract: id-only URL, requester-match,
 * an explicit 401, summary plus table URL, and a capped read that ships its bound).
 *
 * A READ whose answer is a listing parks its rows and answers the model with a summary and this
 * page's address (`noa_api.mcp_tools.table_surface`). Zero tokens for the table body, and the rows
 * are read here, behind the operator's own session, rather than in a transcript a LibreChat
 * administrator can read.
 *
 * **This half is the server's, and it is the authenticated one.** The incoming `Cookie` header goes
 * with the read (`lib/tables/detail.ts`), so the HTML that reaches the frame is already the
 * operator's table: no empty box while a client fetch decides who they are, and no browser-reachable
 * API route to add to the proxy's allowlist — which is why that count is still four.
 *
 * **Read-only.** No decision controls, no reason box, no CSRF token, no `<form>`. The
 * approval card is where a change is authorised; this page shows what a READ found and nothing else.
 *
 * **The sign-in address is resolved here** (the 401 card's rule, reused), not in the component that renders
 * it: it comes from a server-side variable with no `NEXT_PUBLIC_*` twin, so the page reads it and
 * passes it down. Per request rather than in `next.config.ts`, because `output: 'standalone'` never
 * runs that config at runtime — the framing header is baked there on purpose, and this is not.
 *
 * **The frame origin rides down the same way**, for the sizing message this surface posts
 * (`components/frame-sizer.tsx`). Unlike the sign-in address it has a build-time twin — the
 * `frame-ancestors` header — that it must agree with; `lib/embed/frame-origin.ts` says why the
 * request-time read is wrapped and what a disagreement looks like from an operator's side.
 */

// Reading `headers()` already opts this route out of prerendering; saying so as well means a future
// edit that stops reading them cannot quietly make one operator's rows cacheable for the next
// request. Same declaration the card page and the proxy route carry.
export const dynamic = 'force-dynamic'

export default async function ResultTablePage({
  params,
}: {
  params: Promise<{ token: string }>
}) {
  const { token } = await params
  const cookie = (await headers()).get('cookie')

  return (
    <TableView
      initial={await loadResultTable(token, { cookie })}
      signInUrl={resolveSignInUrl(process.env)}
      frameOrigin={resolveFrameTargetOrigin(process.env)}
    />
  )
}
