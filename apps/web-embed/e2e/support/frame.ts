import type { BrowserContext, Frame, FrameLocator, Page } from '@playwright/test'

import { CHAT_ORIGIN, MEASURED_SANDBOX, UPSTREAM_ORIGIN } from '../../playwright.config'

/**
 * Putting the approval card in the frame LibreChat actually gives it (§T.41, §T.42, §T.43).
 *
 * Shared by the browser lanes that need a framed card — the decision path (`approvals`) and the 401
 * state (`sign-in`) — because they need the *same* frame: the same parent origin, the same sandbox
 * string, the same session cookie. Two copies of this setup would be two frames free to drift apart,
 * and a spec measuring a frame LibreChat does not serve measures nothing.
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

/**
 * The box `@mcp-ui/client` was measured opening with, and the state the self-sizing exists to escape.
 *
 * Here rather than in one spec file because both frame-size lanes pin it — the table's and the
 * card's — and a second copy would be a second number free to drift from the one the sizing is
 * actually measured against.
 */
export const HOST_OPENING_HEIGHT = 150

/**
 * How the parent opens the box, for the specs that are about the box (`framing-parent.mjs`).
 *
 * Both fields default to the frame §T.45 pinned, so a spec that does not mention them measures the
 * same frame it always did: 640px tall, and resized when the document asks — which is what the
 * measured host does with a `ui-size-change`.
 */
export type FrameBox = {
  /** The height the iframe opens with, before any message. */
  height?: number
  /** `false` makes the parent ignore a size change: the degraded path, a host that never grew. */
  autosize?: boolean
}

/** The parent page LibreChat stands in for, framing `src` under one of the two measured sandboxes. */
export function parentUrl(
  src: string,
  sandbox: string = MEASURED_SANDBOX,
  box: FrameBox = {},
): string {
  const parent = new URL(CHAT_ORIGIN)
  parent.searchParams.set('src', src)
  parent.searchParams.set('sandbox', sandbox)
  if (box.height !== undefined) parent.searchParams.set('height', String(box.height))
  if (box.autosize === false) parent.searchParams.set('autosize', '0')
  return parent.toString()
}

export async function frameCard(
  page: Page,
  id: string,
  sandbox: string = MEASURED_SANDBOX,
  box: FrameBox = {},
): Promise<FrameLocator> {
  await page.goto(parentUrl(`${EMBED_ORIGIN}/approvals/${id}`, sandbox, box))
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
  box: FrameBox = {},
): Promise<FrameLocator> {
  await page.goto(parentUrl(`${EMBED_ORIGIN}/tables/${token}`, sandbox, box))
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

/**
 * Every height the framed document has asked the parent for, in order.
 *
 * Read off the parent rather than off a page event: what a spec needs to know is what the *host*
 * accepted, and the host's own gate is `event.source === iframe.contentWindow`. A count taken
 * inside the frame would include messages the host discarded.
 */
export async function sizePosts(page: Page): Promise<number[]> {
  return await page.evaluate(() =>
    ((window as unknown as { __sizePosts: { height: number }[] }).__sizePosts ?? []).map(
      (payload) => payload.height,
    ),
  )
}

/**
 * What the parent did with a posted height: the number it applied, and the box that resulted.
 *
 * Two numbers, because they differ by 4px and the reason is not this app's arithmetic: an iframe
 * carries `border: 2px inset` from the UA stylesheet, so its laid-out box is its height plus its
 * own borders. `applied` is the inline style the host writes — the same place `@mcp-ui/client` was
 * measured writing it — and is the value a round-trip claim is about. `rendered` is there so a spec
 * cannot pass against a frame that was collapsed by something else.
 */
export async function frameBox(page: Page): Promise<{ applied: number; rendered: number }> {
  return await page.evaluate(() => {
    const frame = document.getElementById('card')
    return {
      applied: Number.parseFloat(frame?.style.height ?? '') || 0,
      rendered: frame?.getBoundingClientRect().height ?? 0,
    }
  })
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
 * registrable-domain scoping is the API's setting and is asserted API-side; what belongs here
 * is that the browser's cookie for this origin reaches NOA.
 */
export async function signIn(context: BrowserContext): Promise<void> {
  await context.addCookies([
    { name: 'noa_session', value: 'e2e.session.value', domain: 'localhost', path: '/' },
  ])
}
