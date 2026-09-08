// @vitest-environment node
import { type ChildProcess, spawn } from 'node:child_process'
import net from 'node:net'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterAll, beforeAll, describe, expect, it } from 'vitest'

/**
 * The framing header on the wire, from a server that is actually running (§T.49, V41).
 *
 * The unit specs prove what the config object says. Whether Next then puts that header on a
 * response is a different question, and it is the one the control exists to answer: a `headers()`
 * entry that is never applied looks identical from inside the process. §T.45 measured the same
 * mechanism for the embed, but upstream — or sibling — provenance is not evidence a control works
 * here (V69's lesson, B2), so this app proves its own.
 *
 * Own lane (`pnpm test:server`, `vitest.server.config.ts`): it boots a dev server, which does not
 * belong in the unit lane's runtime.
 */

const APP_ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const NEXT_BIN = path.join(APP_ROOT, 'node_modules', '.bin', 'next')

// Every kind of response this app produces: a route handler, a redirect from a page, a page inside
// the protected shell, a page outside it, the same-origin API proxy, and a path that routes to
// nothing. One `headers()` entry on `/(.*)` has to cover all six — a page-shaped pattern would
// leave `/healthz`, `/api/*` and the 404 bare.
//
// `/login` and `/api/auth/me` joined the list at §T.50, and they are the two this app most needs
// covered: the login route is the address `NOA_SIGN_IN_URL` sends an operator to from inside a
// LibreChat frame, so it is the one page an attacker has a reason to try to frame, and the proxy is
// the surface that would carry an operator's cookie if anyone succeeded.
const PATHS = [
  '/healthz',
  '/',
  '/admin/users',
  '/login',
  '/api/auth/me',
  '/this-route-does-not-exist',
]

const BOOT_TIMEOUT_MS = 180_000
const REQUEST_TIMEOUT_MS = 180_000

let server: ChildProcess
let baseUrl: string
let serverOutput = ''

/** An OS-assigned free port, so a developer's own `next dev` on :3000 is not in the way. */
async function freePort(): Promise<number> {
  return await new Promise((resolve, reject) => {
    const probe = net.createServer()
    probe.on('error', reject)
    probe.listen(0, '127.0.0.1', () => {
      const address = probe.address()
      if (address === null || typeof address === 'string') {
        reject(new Error('could not read a port from the probe socket'))
        return
      }
      probe.close(() => resolve(address.port))
    })
  })
}

/**
 * Readiness gate: a TCP connect, one layer below the routes under test (V90).
 *
 * Pointing this at `/healthz` — or at any route — would turn a missing header, a failed render or a
 * broken route into a *timeout*, and a timeout names nothing. The socket is the subject's floor:
 * once the port accepts, every later failure is an assertion about a response.
 */
async function waitForPort(port: number, deadline: number): Promise<void> {
  for (;;) {
    const connected = await new Promise<boolean>((resolve) => {
      const socket = net.connect({ port, host: '127.0.0.1' })
      socket.once('connect', () => {
        socket.destroy()
        resolve(true)
      })
      socket.once('error', () => {
        socket.destroy()
        resolve(false)
      })
    })
    if (connected) return

    if (Date.now() > deadline) {
      throw new Error(`dev server never opened port ${port}. Output so far:\n${serverOutput}`)
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
}

beforeAll(async () => {
  const port = await freePort()
  baseUrl = `http://127.0.0.1:${port}`

  // `detached` so the whole group can be signalled: `next dev` runs a child of its own, and killing
  // only the parent leaves a compiler holding the port.
  server = spawn(NEXT_BIN, ['dev', '-p', String(port), '-H', '127.0.0.1'], {
    cwd: APP_ROOT,
    detached: true,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: process.env,
  })
  server.stdout?.on('data', (chunk: Buffer) => (serverOutput += chunk.toString()))
  server.stderr?.on('data', (chunk: Buffer) => (serverOutput += chunk.toString()))
  server.on('exit', (code) => (serverOutput += `\n[dev server exited: ${code}]`))

  await waitForPort(port, Date.now() + BOOT_TIMEOUT_MS)
}, BOOT_TIMEOUT_MS)

afterAll(() => {
  if (server?.pid !== undefined && server.exitCode === null) {
    try {
      process.kill(-server.pid, 'SIGTERM')
    } catch {
      server.kill('SIGTERM')
    }
  }
})

async function get(pathname: string): Promise<Response> {
  return await fetch(`${baseUrl}${pathname}`, {
    // `/` redirects to `/home` (§T76 — the role-aware dispatcher). Followed, the redirect's own
    // headers would never be looked at, and the redirect is a response this app sends.
    redirect: 'manual',
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  })
}

describe('§T.49 — every response carries the framing header', () => {
  it('answers at all — the server under test is the one being measured', async () => {
    // Without this the suite below could pass against a server that errors on everything: an error
    // page carries the config headers too. `/healthz` returning its own body is the proof that the
    // app booted and is serving its own routes (V87).
    const response = await get('/healthz')

    expect(response.status).toBe(200)
    expect(await response.text()).toBe('{"status":"ok"}')
  }, REQUEST_TIMEOUT_MS)

  it.each(PATHS)("sends frame-ancestors 'none' on %s", async (pathname: string) => {
    const headers = (await get(pathname)).headers

    expect(headers.get('content-security-policy'), `no CSP on ${pathname}`).toBe(
      "frame-ancestors 'none'",
    )
    // §T.49: none is needed, and one added later would be honoured instead of this header by any
    // client that reads X-Frame-Options first.
    expect(headers.get('x-frame-options'), `X-Frame-Options on ${pathname}`).toBeNull()
  }, REQUEST_TIMEOUT_MS)

  it('covers a redirect and a 404, not only the pages', async () => {
    // Names the two responses a page-shaped `source` pattern would miss, so a narrowed pattern
    // fails as itself rather than as a header assertion somewhere in the list above.
    const root = await get('/')
    expect(root.status).toBe(307)
    // §T76 moved the target from `/admin/users` to the role-aware dispatcher. It stays a SERVER
    // redirect: making `/` a client page to do the dispatch would delete the only redirect this
    // lane covers, and the header assertions above would stop being exercised on one.
    // Resolved against the base so the assertion holds whether Next answers with a relative path
    // or an absolute URL — what is pinned is the destination, not the header's spelling.
    expect(new URL(root.headers.get('location') ?? '', baseUrl).pathname).toBe('/home')
    expect((await get('/this-route-does-not-exist')).status).toBe(404)
  }, REQUEST_TIMEOUT_MS)
})
