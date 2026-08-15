import { describe, expect, it } from 'vitest'

import { GET, dynamic } from './route'

describe('GET /healthz', () => {
  it('answers 200 with the liveness body', async () => {
    const response = GET()

    expect(response.status).toBe(200)
    await expect(response.json()).resolves.toEqual({ status: 'ok' })
  })

  it('is never cached — a liveness answer held from a previous boot is a lie', () => {
    // Asserted on the module's own export rather than on a response header,
    // because the export is the knob Next reads. Checking `Cache-Control` here
    // would test the test harness instead of the route's configuration.
    expect(dynamic).toBe('force-dynamic')
  })
})
