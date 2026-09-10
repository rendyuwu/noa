// @vitest-environment node
import { type ChildProcess, spawn } from 'node:child_process'
import http from 'node:http'
import net from 'node:net'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterAll, beforeAll, describe, expect, it } from 'vitest'

/**
 * The proxy hop, from a server that is actually running.
 *
 * `src/lib/proxy/http.test.ts` proves what the primitives compute and
 * `src/app/api/[...path]/route.test.ts` proves what the handler passes to `fetch`. Neither answers
 * the question this lane exists for: does a request the BROWSER makes to `/api/*` reach the API
 * with the operator's cookie on it, and does the `Set-Cookie` come back? Next sits between those
 * two facts — a route file in the wrong place, a `runtime` that cannot stream, a header the
 * framework rewrites — and every one of those looks correct from inside the process (the
 * framing-header lane exists for the same reason).
 *
 * Upstream is a stub, not the real API: what is asked here is the hop, and a real API would make
 * Postgres or LDAP being down read as a broken proxy. It records what arrived, which is what makes
 * "the Authorization header never got there" an assertion about the wire rather than about a
 * mock.
 *
 * Own lane (`pnpm test:server`, `vitest.server.config.ts`): it boots a dev server, which does not
 * belong in the unit lane's runtime.
 */

const APP_ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const NEXT_BIN = path.join(APP_ROOT, 'node_modules', '.bin', 'next')

const BOOT_TIMEOUT_MS = 180_000
const REQUEST_TIMEOUT_MS = 180_000

/** What the stub upstream saw, in order. One record per request it answered. */
type Seen = {
  method: string
  url: string
  authorization: string | null
  cookie: string | null
  body: string
}

let server: ChildProcess
let baseUrl: string
let serverOutput = ''
let upstream: http.Server
let seen: Seen[] = []

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
 * Readiness gate: a TCP connect, one layer below the routes under test.
 *
 * Pointing this at `/api/...` — the subject — would turn a proxy that throws into a *timeout*, and
 * a timeout names nothing: every cause produces the same stall. The socket is the subject's floor.
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

/**
 * The stub API. Answers three shapes the panel actually uses and records what arrived.
 *
 * `/auth/login` sets the session cookie with the `Domain` attribute the shared-domain session depends on; `/auth/me`
 * answers 401 so the status that drives the session-expiry flow is exercised as itself; anything
 * else is a 500 carrying a request id.
 */
function startUpstream(port: number): Promise<http.Server> {
  const listener = http.createServer((request, response) => {
    const chunks: Buffer[] = []
    request.on('data', (chunk: Buffer) => chunks.push(chunk))
    request.on('end', () => {
      seen.push({
        method: request.method ?? '',
        url: request.url ?? '',
        authorization: request.headers.authorization ?? null,
        cookie: request.headers.cookie ?? null,
        body: Buffer.concat(chunks).toString(),
      })

      const url = (request.url ?? '').split('?')[0]

      if (url === '/auth/login') {
        response.writeHead(200, {
          'content-type': 'application/json',
          'x-request-id': 'req-login',
          'set-cookie': [
            'noa_session=stub.jwt.value; Domain=.noa.internal; Path=/; HttpOnly; SameSite=Lax',
            'noa_hint=1; Path=/',
          ],
        })
        response.end(JSON.stringify({ user: { id: 'u1', email: 'operator@noa.internal' } }))
        return
      }

      if (url === '/auth/me') {
        response.writeHead(401, {
          'content-type': 'application/json',
          'x-request-id': 'req-me',
        })
        response.end(JSON.stringify({ error_code: 'not_authenticated', request_id: 'req-me' }))
        return
      }

      response.writeHead(500, {
        'content-type': 'application/json',
        'x-request-id': 'req-boom',
      })
      response.end(JSON.stringify({ error_code: 'internal_error', request_id: 'req-boom' }))
    })
  })

  return new Promise((resolve, reject) => {
    listener.on('error', reject)
    listener.listen(port, '127.0.0.1', () => resolve(listener))
  })
}

beforeAll(async () => {
  const upstreamPort = await freePort()
  upstream = await startUpstream(upstreamPort)

  const port = await freePort()
  baseUrl = `http://127.0.0.1:${port}`

  // `detached` so the whole group can be signalled: `next dev` runs a child of its own, and killing
  // only the parent leaves a compiler holding the port.
  server = spawn(NEXT_BIN, ['dev', '-p', String(port), '-H', '127.0.0.1'], {
    cwd: APP_ROOT,
    detached: true,
    stdio: ['ignore', 'pipe', 'pipe'],
    // A real environment variable wins over the repo-root `.env` (`config/root-env.ts`), which is
    // what lets this run point the proxy at its own stub instead of whatever a developer configured.
    env: { ...process.env, NOA_API_URL: `http://127.0.0.1:${upstreamPort}` },
  })
  server.stdout?.on('data', (chunk: Buffer) => (serverOutput += chunk.toString()))
  server.stderr?.on('data', (chunk: Buffer) => (serverOutput += chunk.toString()))
  server.on('exit', (code) => (serverOutput += `\n[dev server exited: ${code}]`))

  await waitForPort(port, Date.now() + BOOT_TIMEOUT_MS)
}, BOOT_TIMEOUT_MS)

afterAll(async () => {
  if (server?.pid !== undefined && server.exitCode === null) {
    try {
      process.kill(-server.pid, 'SIGTERM')
    } catch {
      server.kill('SIGTERM')
    }
  }
  await new Promise<void>((resolve) => upstream?.close(() => resolve()))
})

async function call(pathname: string, init: RequestInit = {}): Promise<Response> {
  return await fetch(`${baseUrl}${pathname}`, {
    redirect: 'manual',
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    ...init,
  })
}

describe('the live proxy — /api/* reaches the API, and the session rides both ways', () => {
  it('answers at all — the server under test is the one being measured', async () => {
    // Without this the specs below could pass against a server that errors on everything: an error
    // page carries the config headers too, and a 500 from the app is indistinguishable from a 500
    // the stub sent. `/healthz` serving its own body is the proof the app booted.
    const response = await call('/healthz')

    expect(response.status).toBe(200)
    expect(await response.text()).toBe('{"status":"ok"}')
  }, REQUEST_TIMEOUT_MS)

  it('carries a login POST upstream with its body, and the Set-Cookie back with Domain intact', async () => {
    seen = []
    const response = await call('/api/auth/login', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email: 'operator@noa.internal', password: 'sekret-1' }),
    })

    expect(response.status).toBe(200)
    expect(seen).toHaveLength(1)
    expect(seen[0]?.method).toBe('POST')
    expect(seen[0]?.url).toBe('/auth/login')
    expect(seen[0]?.body).toContain('operator@noa.internal')

    // `Domain=.noa.internal` is what puts this session on the same registrable domain as the
    // embed. Rewritten or dropped here, the panel would still work and the approval card would not.
    const cookies = response.headers.getSetCookie()
    expect(cookies).toContain(
      'noa_session=stub.jwt.value; Domain=.noa.internal; Path=/; HttpOnly; SameSite=Lax',
    )
    // Both values, not the comma-joined collapse a single `get()` would produce.
    expect(cookies).toContain('noa_hint=1; Path=/')
  }, REQUEST_TIMEOUT_MS)

  it('forwards the operator cookie and the query string, and never an Authorization header', async () => {
    seen = []
    const response = await call('/api/admin/audit/tool-runs?status=FAILED&limit=25', {
      headers: {
        cookie: 'noa_session=stub.jwt.value',
        authorization: 'Bearer noa_live_token',
      },
    })

    // Bearer is MCP-only and LibreChat's to send. Asserted on what ARRIVED at the upstream, so
    // this is a fact about the wire and not about a mocked `fetch`.
    expect(seen[0]?.authorization).toBeNull()
    // The negative control beside it: the credential that IS this origin's did arrive. A proxy that
    // forwarded no headers at all would satisfy the assertion above.
    expect(seen[0]?.cookie).toBe('noa_session=stub.jwt.value')
    expect(seen[0]?.url).toBe('/admin/audit/tool-runs?status=FAILED&limit=25')
    // Whatever the stub answered, the hop happened — the status is the next spec's subject.
    expect(response.status).toBe(500)
  }, REQUEST_TIMEOUT_MS)

  it('passes a 401 through as a 401, with the request id that names its log line', async () => {
    // `fetchWithAuth` keys the whole session-expiry flow off this status, and `clearAuth` is what
    // sends the operator to `/login`. A proxy that normalised it would leave an expired session
    // rendering an unexplained error state instead.
    const response = await call('/api/auth/me', { headers: { cookie: 'noa_session=expired' } })

    expect(response.status).toBe(401)
    expect(response.headers.get('x-request-id')).toBe('req-me')
    expect(await response.json()).toMatchObject({ error_code: 'not_authenticated' })
  }, REQUEST_TIMEOUT_MS)

  it('carries the PATCH and DELETE the embed’s proxy never needed', async () => {
    seen = []
    await call('/api/admin/users/42', { method: 'PATCH', body: '{"is_active":false}' })
    await call('/api/admin/users/42', { method: 'DELETE' })

    expect(seen.map((entry) => entry.method)).toEqual(['PATCH', 'DELETE'])
    expect(seen[0]?.body).toBe('{"is_active":false}')
  }, REQUEST_TIMEOUT_MS)
})
