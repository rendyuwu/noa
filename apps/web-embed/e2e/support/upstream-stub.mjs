import { createServer } from 'node:http'

/**
 * Stand-in for the NOA API, for the proxy's browser checks (§T.44).
 *
 * The e2e question is "does a browser cookie on the embed origin reach the API through this
 * app's proxy" — a question about the hop, not about FastAPI. A real API here would drag
 * Postgres, LDAP and a session mint into a Playwright run and turn any of their failures into a
 * red proxy test.
 *
 * It records what it was asked for, so a refusal the proxy makes can be told apart from a
 * refusal the upstream makes. Without `/__hits` the "login is not proxied" assertion (V42) would
 * pass just as well against a proxy that forwarded the request to an upstream that happened to
 * 404 — V87's shape.
 */

const PORT = Number(process.env.UPSTREAM_STUB_PORT ?? 8099)

/** @type {Record<string, number>} */
const hits = {}

const server = createServer((request, response) => {
  const url = new URL(request.url ?? '/', `http://127.0.0.1:${PORT}`)
  const key = `${request.method} ${url.pathname}`

  if (url.pathname === '/__hits') {
    response.writeHead(200, { 'content-type': 'application/json' })
    response.end(JSON.stringify(hits))
    return
  }

  hits[key] = (hits[key] ?? 0) + 1

  if (url.pathname === '/auth/me' && request.method === 'GET') {
    response.writeHead(200, {
      'content-type': 'application/json',
      'x-request-id': 'stub-request-id',
      // The attribute the browser must see survive the hop (V40). `.noa.internal`
      // would be rejected for a localhost document, so the shape is what is
      // asserted here; the real domain is the API's setting.
      'set-cookie': 'stub_echo=1; Path=/; SameSite=Lax',
    })
    response.end(
      JSON.stringify({
        cookie: request.headers['cookie'] ?? null,
        authorization: request.headers['authorization'] ?? null,
      }),
    )
    return
  }

  response.writeHead(200, { 'content-type': 'application/json' })
  response.end(JSON.stringify({ reached: key }))
})

server.listen(PORT, '127.0.0.1', () => {
  process.stdout.write(`upstream stub listening on http://127.0.0.1:${PORT}\n`)
})
