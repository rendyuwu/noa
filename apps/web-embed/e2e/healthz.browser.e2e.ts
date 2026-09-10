import { expect, test } from '@playwright/test'

/**
 * Scaffold smoke check: a real browser reaches a real dev server on the
 * embed's own origin and gets the liveness answer.
 *
 * Same-origin, so it runs here. The cross-origin cookie behaviour this app
 * ultimately depends on was measured separately against LibreChat in
 * `spikes/librechat-embed-render-gate/` and is not what this spec covers.
 */

test('the embed serves /healthz to a browser on its own origin', async ({ page }) => {
  const response = await page.goto('/healthz')

  expect(response?.status()).toBe(200)
  await expect(page.locator('body')).toContainText('"status":"ok"')
})

test('an unmapped path is a 404, not a blank 200', async ({ page }) => {
  // The card renders decision controls. A route that answers 200 for anything
  // would let a mistyped id render an empty card, which the no-blank-card rule rules out for the
  // 401 case for the same reason.
  const response = await page.goto('/this-route-does-not-exist')

  expect(response?.status()).toBe(404)
})
