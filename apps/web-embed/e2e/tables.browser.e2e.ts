import { expect, test } from '@playwright/test'

import {
  MEASURED_SANDBOX,
  SIGN_IN_URL,
  STUB_TABLE_TOTAL_ROWS,
  TABLE_TOKENS,
} from '../playwright.config'
import {
  EMBED_ORIGIN,
  HOST_RESOLVER_ARGS,
  cardBody,
  frameTable,
  framedDocument,
  hits,
  signIn,
} from './support/frame'

/**
 * The large-READ table, in the frame LibreChat actually gives it (§T.56 — V27, V38, V64, V85, V94).
 *
 * **Why a browser lane at all**, when `table-view.test.tsx` renders the same component in jsdom:
 * two of these claims are about the frame rather than about the markup. That the rows render at all
 * under `MEASURED_SANDBOX` — the string R29 measured, with `allow-forms` absent — is a claim about a
 * document served into a sandbox, and that this page issues **no POST** is a claim about what the
 * browser did, read off the upstream's own counter rather than off an exception (V80's rule: the
 * refusal is silent, so assert on the counter).
 *
 * **The sandbox is the pinned constant**, not a string this file chose. A spec that quietly widened
 * it would be measuring a frame LibreChat does not serve.
 *
 * The 401 case reaches §T.43's way out, which this surface reuses rather than reimplements:
 * a link *and* the same address as text, because the render site whose sandbox omits `allow-popups`
 * opens the link silently. `sign-in.browser.e2e.ts` owns the two-sandbox measurement of
 * that door; what is asserted here is that this surface offers it.
 *
 * V90: no readiness wait of its own. The servers this leans on are gated on their open sockets by
 * `playwright.config.ts` (§T.40(f)), and the waits below are on the frame's own content.
 */

test.use({ launchOptions: { args: HOST_RESOLVER_ARGS } })

test.beforeEach(async ({ context }) => {
  await signIn(context)
})

test('the rows render inside the measured sandbox', async ({ page }) => {
  // The whole listing, on NOA's origin, in the frame — which is what V64 offloads it for: the model
  // was handed a summary and this address, and the body never entered the transcript.
  const table = await frameTable(page, TABLE_TOKENS.whole)

  await expect(cardBody(table).locator('h1')).toContainText('whm_list_accounts')
  await expect(cardBody(table).getByRole('columnheader', { name: 'Account' })).toBeVisible()
  await expect(cardBody(table).getByRole('cell', { name: 'acmeco' })).toBeVisible()
  await expect(cardBody(table).getByRole('cell', { name: 'beta.example' })).toBeVisible()
})

test('a capped page says so, in the frame', async ({ page }) => {
  // The number that matters is the one the page cannot compute from what it holds: 1,240 matches
  // behind two rows. A page that reported its row count as the total would read as complete here.
  const table = await frameTable(page, TABLE_TOKENS.truncated)

  await expect(
    cardBody(table).getByText(new RegExp(STUB_TABLE_TOTAL_ROWS.toLocaleString('en-US'))),
  ).toBeVisible()
  await expect(cardBody(table).getByText(/narrow the search/)).toBeVisible()
})

test('an uncapped page states its total without claiming truncation', async ({ page }) => {
  // The negative control for the spec above: without it, "a capped page says so" would pass against
  // a page that says it always, and a warning that is always on is one nobody reads.
  const table = await frameTable(page, TABLE_TOKENS.whole)

  await expect(cardBody(table).getByText(/all of them shown/)).toBeVisible()
  await expect(cardBody(table).getByText(/narrow the search/)).toHaveCount(0)
})

test('the table surface carries no decision control and issues no POST (§I.embed, V80)', async ({
  page,
}) => {
  // Its own token, for the reason `pendingId` exists one surface over: the stub is one process
  // shared by every spec file, its counter is cumulative, and a spec that compared the *whole*
  // counter would go red when another file's decision POST landed between the two reads. Scoped to
  // this token, the count is exact rather than "at least".
  const token = `${TABLE_TOKENS.whole}-no-post`
  const table = await frameTable(page, token)
  await expect(cardBody(table).getByRole('table')).toBeVisible()

  // The absences, by name — the rule §T.43 established: this page has controls of its own in other
  // states, so a control *count* would go red for the right rule spelled wrongly.
  await expect(cardBody(table).getByRole('button', { name: /approve|deny/i })).toHaveCount(0)
  await expect(cardBody(table).locator('form')).toHaveCount(0)
  await expect(cardBody(table).locator('input')).toHaveCount(0)
  await expect(cardBody(table).locator('textarea')).toHaveCount(0)

  // And nothing was sent about this table. Asserted on the upstream's counter rather than on an
  // exception, because a request this page never makes throws nothing — the same reason V80's
  // control counts hits. The GET is checked too, so this cannot pass by the page never loading.
  const seen = await hits(page)
  expect(seen[`GET /tables/${token}`]).toBe(1)
  expect(Object.keys(seen).filter((key) => key.startsWith('POST ') && key.includes(token))).toEqual(
    [],
  )
})

test('the operator’s cookie reaches the API through the page’s own read', async ({
  page,
}) => {
  // The read is server-side (§T.56, §T.41(c)'s shape), so this is the assertion that the browser's
  // session actually travels with it — without it every operator would see the 401 state.
  //
  // Measured against a token the stub serves **only** to a request carrying a cookie: every other
  // token answers 200 regardless, so this spec would pass with the cookie dropped, which is a test
  // that cannot fail for the reason it exists.
  //
  // **Top-level rather than framed, and that bound is honest.** In this harness the embed is
  // `localhost:3001` and the parent is `chat.noa.internal:8110` — cross-site, so a `SameSite=Lax`
  // cookie is withheld from the framed document by the browser itself. In production both are under
  // `noa.internal` and the cookie is same-site, which is what R29 measured live against
  // LibreChat. So what this lane can prove is that the *loader* forwards the browser's cookie; that
  // it arrives in the frame is R29's measurement, not this harness's.
  await page.goto(`${EMBED_ORIGIN}/tables/${TABLE_TOKENS.needsCookie}`)

  await expect(page.getByRole('table')).toBeVisible()
  const seen = await hits(page)
  expect(seen[`GET /tables/${TABLE_TOKENS.needsCookie}`]).toBeGreaterThan(0)
})

test('without a session the same token renders the 401 state (V87’s control)', async ({
  browser,
}) => {
  // The other half of the spec above: a context that never signed in gets no table at all. Without
  // this, "the cookie arrived" is a claim about a page that renders either way.
  const context = await browser.newContext()
  const page = await context.newPage()
  try {
    await page.goto(`${EMBED_ORIGIN}/tables/${TABLE_TOKENS.needsCookie}`)

    await expect(page.locator('h1')).toContainText('Cannot authenticate here')
    await expect(page.getByRole('table')).toHaveCount(0)
  } finally {
    await context.close()
  }
})

test('a 401 renders the way out, with the address as text beside the link', async ({
  page,
}) => {
  const table = await frameTable(page, TABLE_TOKENS.unauthorized)

  await expect(cardBody(table).locator('h1')).toContainText('Cannot authenticate here')

  const link = cardBody(table).getByRole('link', { name: 'Sign in to NOA' })
  await expect(link).toHaveAttribute('href', SIGN_IN_URL)
  await expect(link).toHaveAttribute('target', '_blank')
  // The door the sandbox cannot withhold: the same address, as copyable text.
  await expect(cardBody(table).getByText(SIGN_IN_URL, { exact: false })).toBeVisible()

  // V42: no login page, no credential handling — asserted as the absence it is, in the frame.
  await expect(cardBody(table).locator('form')).toHaveCount(0)
  await expect(cardBody(table).locator('input')).toHaveCount(0)
})

test('a 404 renders one sentence for every cause, and never an empty table', async ({
  page,
}) => {
  const table = await frameTable(page, TABLE_TOKENS.notFound)

  await expect(cardBody(table).locator('h1')).toContainText('Table not available')
  await expect(cardBody(table).getByRole('table')).toHaveCount(0)
})

test('the framed document stays on its own URL (§T.43’s absence, V42)', async ({ page }) => {
  // Nothing on this surface navigates the frame: no redirect to a login page, no client-side route
  // change. Asserted after the 401 state has rendered, which is the state that would do it.
  await frameTable(page, TABLE_TOKENS.unauthorized, MEASURED_SANDBOX)
  const framed = await framedDocument(page)

  expect(framed.url()).toContain(`/tables/${TABLE_TOKENS.unauthorized}`)
})
