import type { BrowserContext, Frame, FrameLocator, Page } from '@playwright/test'

import { CHAT_ORIGIN, MEASURED_SANDBOX, UPSTREAM_ORIGIN } from '../../playwright.config'

/**
 * Putting the approval card in the frame LibreChat actually gives it (§T.41, §T.42, §T.43).
 *
 * Shared by the browser lanes that need a framed card — the decision path (`approvals`) and the 401
 * state (`sign-in`) — because they need the *same* frame: the same parent origin, the same sandbox
 * string, the same session cookie. Two copies of this setup would be two frames free to drift apart,
 * and a spec measuring a frame LibreChat does not serve measures nothing (V66).
 *
 * Not a `*.e2e.ts` file, so Playwright's `testMatch` leaves it alone.
 */

/** The embed's own origin during a browser run. Its port is part of the contract (§T.44). */
export const EMBED_ORIGIN = 'http://localhost:3001'

/**
 * DNS only, for `test.use({ launchOptions: { args: HOST_RESOLVER_ARGS } })`.
 *
 * `chat.noa.internal` is the origin the dev server was told to allow framing from (§T.45), and
 * Chromium's Local Network Access checks are why the parent has to be a resolvable real server
 * rather than an intercepted response (V90's family).
 */
export const HOST_RESOLVER_ARGS = [
  '--host-resolver-rules=MAP chat.noa.internal 127.0.0.1, MAP not-chat.noa.internal 127.0.0.1',
]

/**
 * A PENDING card id unique to one test.
 *
 * The stub serves any id it does not recognise as PENDING, so a suffix is all this needs. **Every
 * test that counts hits needs one**: the stub is a single process shared by every spec file and
 * Playwright runs files in parallel, so its counter is cumulative and cannot be reset without racing
 * another file. A per-test id makes each count exact instead of "at least".
 */
export function pendingId(suffix: string): string {
  return `9f1c2b7e-0000-4000-8000-0000000${suffix.padStart(5, '0')}`
}

/** The parent page LibreChat stands in for, framing `src` under one of the two measured sandboxes. */
export function parentUrl(src: string, sandbox: string = MEASURED_SANDBOX): string {
  const parent = new URL(CHAT_ORIGIN)
  parent.searchParams.set('src', src)
  parent.searchParams.set('sandbox', sandbox)
  return parent.toString()
}

export async function frameCard(
  page: Page,
  id: string,
  sandbox: string = MEASURED_SANDBOX,
): Promise<FrameLocator> {
  await page.goto(parentUrl(`${EMBED_ORIGIN}/approvals/${id}`, sandbox))
  return page.frameLocator('#card')
}

/**
 * The same frame, around the large-READ table surface (§T.56).
 *
 * The table is served into the frame LibreChat gives *this* app — same parent origin, same sandbox
 * string, same session cookie as the card — because a surface measured in a frame LibreChat does
 * not serve is measured under a premise nothing holds (V66, the reason `frameCard` is shared).
 */
export async function frameTable(
  page: Page,
  token: string,
  sandbox: string = MEASURED_SANDBOX,
): Promise<FrameLocator> {
  await page.goto(parentUrl(`${EMBED_ORIGIN}/tables/${token}`, sandbox))
  return page.frameLocator('#card')
}

/**
 * The framed document itself, for the reads that have to run inside it.
 *
 * `allow-same-origin` keeps the frame same-origin with *its own* origin, which is not the parent's —
 * so `contentWindow` from the parent is a `SecurityError`, and anything a spec needs from in there is
 * evaluated in the frame.
 */
export async function framedDocument(page: Page): Promise<Frame> {
  const element = await page.waitForSelector('#card')
  const frame = await element.contentFrame()
  if (frame === null) throw new Error('#card has no content frame')
  return frame
}

/** What the stub upstream was asked for, by `METHOD path`. */
export async function hits(page: Page): Promise<Record<string, number>> {
  const response = await page.request.get(`${UPSTREAM_ORIGIN}/__hits`)
  return (await response.json()) as Record<string, number>
}

/** The card's own DOM. Scoped so Next's dev-mode overlay is not counted as part of the card. */
export function cardBody(card: FrameLocator) {
  return card.locator('main')
}

/**
 * The session the card is read with.
 *
 * Scoped to `localhost`, not `.noa.internal`: a browser will not accept the real domain for a
 * localhost document, and asserting it anyway would be a check that cannot fail. The
 * registrable-domain scoping is the API's setting (V40) and is asserted API-side; what belongs here
 * is that the browser's cookie for this origin reaches NOA.
 */
export async function signIn(context: BrowserContext): Promise<void> {
  await context.addCookies([
    { name: 'noa_session', value: 'e2e.session.value', domain: 'localhost', path: '/' },
  ])
}
