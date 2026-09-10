import { expect, test } from '@playwright/test'

import { UPSTREAM_ORIGIN } from '../playwright.config'

/**
 * The session hop, in a real browser (§T.44, V40).
 *
 * This is the one claim jsdom cannot make. The unit specs prove the proxy forwards whatever
 * `Cookie` header it is handed; what a browser decides to *send* to its own origin is a
 * different question, and the whole decision path rests on the answer.
 *
 * The cookie here is scoped to `localhost`, not `.noa.internal`. A browser will not accept the
 * real domain for a localhost document, and asserting it anyway would be a check that cannot
 * fail. The registrable-domain scoping is the API's setting (`AUTH_SESSION_COOKIE_DOMAIN`) and is
 * asserted API-side; what belongs here is that the browser's cookie for this origin survives the
 * hop to the API at all.
 */

// The stub's own address, off the config that starts it rather than repeated here. It was a
// literal until the harness ports became overridable, at which point the copy pointed at whatever
// else happened to be on 8099 and this file's `/__hits` reads came back as somebody's HTML.
const UPSTREAM = UPSTREAM_ORIGIN
const ID = '9f1c2b7e-0000-4000-8000-000000000000'

type EchoedRequest = { status: number; body: { cookie: string | null; authorization: string | null } }

test('a cookie on the embed origin reaches the API through the proxy', async ({ page, context }) => {
  await context.addCookies([
    { name: 'noa_session', value: 'e2e.session.value', domain: 'localhost', path: '/' },
  ])

  // Any document on this origin will do; the subject is the in-page fetch, not the page.
  await page.goto('/healthz')

  const seen = await page.evaluate(async (): Promise<EchoedRequest> => {
    const response = await fetch('/api/auth/me')
    return { status: response.status, body: await response.json() }
  })

  expect(seen.status).toBe(200)
  expect(seen.body.cookie).toContain('noa_session=e2e.session.value')
})

test('the proxy strips Authorization — this origin relays no bearer token', async ({ page }) => {
  // MCP tokens are LibreChat's to send. A browser that sets the header must
  // not have it forwarded, or the embed origin becomes a relay for one.
  await page.goto('/healthz')

  const seen = await page.evaluate(async (): Promise<EchoedRequest> => {
    const response = await fetch('/api/auth/me', {
      headers: { authorization: 'Bearer noa_live_token' },
    })
    return { status: response.status, body: await response.json() }
  })

  expect(seen.status).toBe(200)
  expect(seen.body.authorization).toBeNull()
})

test('an allowed decision POST reaches the API', async ({ page }) => {
  // The separating case. Without it the refusals below pass just as well against a
  // proxy that forwards nothing at all.
  await page.goto('/healthz')

  const status = await page.evaluate(async (path: string) => {
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ reason: 'typed by the operator', csrf: 'v1.0.sig' }),
    })
    return response.status
  }, `/api/action-requests/${ID}/approve`)

  // 202, which is what the real endpoint answers (V29: the decision is durable, the change has
  // not run yet) and what the stub mirrors since §T.41. The status is passed through untouched, so
  // this is also the assertion that the proxy does not normalise one.
  expect(status).toBe(202)

  const hits = await (await page.request.get(`${UPSTREAM}/__hits`)).json()
  expect(hits[`POST /action-requests/${ID}/approve`]).toBe(1)
})

test('the login route answers 404 and the API records no hit', async ({ page }) => {
  // V42: this app has no login page, no LDAP form and no credential handling. The
  // hit count is the assertion that matters — a 404 produced upstream would look
  // identical from the browser and mean the opposite.
  await page.goto('/healthz')

  const status = await page.evaluate(async () => {
    const response = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email: 'operator@noa.internal', password: 'hunter2' }),
    })
    return response.status
  })

  expect(status).toBe(404)

  const hits = await (await page.request.get(`${UPSTREAM}/__hits`)).json()
  expect(hits['POST /auth/login']).toBeUndefined()
})

test('the admin surface is not reachable from the frameable origin', async ({ page }) => {
  // V41: the admin app answers `frame-ancestors 'none'`. Proxying `/admin/*` here
  // would hand that surface to anything running inside the LibreChat frame.
  await page.goto('/healthz')

  const status = await page.evaluate(async () => (await fetch('/api/admin/users')).status)

  expect(status).toBe(404)

  const hits = await (await page.request.get(`${UPSTREAM}/__hits`)).json()
  expect(hits['GET /admin/users']).toBeUndefined()
})
