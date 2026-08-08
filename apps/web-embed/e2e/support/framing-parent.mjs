import { createServer } from 'node:http'

/**
 * A page that puts the embed in an iframe, for the framing specs (§T.45).
 *
 * Stands in for LibreChat. It answers on any hostname, and the specs reach it under two of them
 * (`chat.noa.internal` and `not-chat.noa.internal`, both mapped to this loopback address by a
 * Chromium `--host-resolver-rules` flag), so the parent *origin* is the only thing that differs
 * between the allowed case and the refused one.
 *
 * A real server rather than an intercepted response, and a resolvable name rather than a fulfilled
 * one, because Chromium's Local Network Access checks block a request from an origin it cannot
 * place into an address space to a loopback address — measured: `page.route(...).fulfill` for the
 * parent made both frames fail with `ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS` before any
 * response, so the refusal spec would have passed without a CSP header existing at all (V90's
 * family: the setup step deciding the outcome).
 */

const PORT = Number(process.env.FRAMING_PARENT_PORT ?? 8110)
const FRAMED_URL = process.env.FRAMING_PARENT_TARGET ?? 'http://localhost:3001/healthz'

const server = createServer((request, response) => {
  response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' })
  response.end(
    `<!doctype html><meta charset="utf-8"><title>framing parent</title>` +
      `<iframe id="card" src="${FRAMED_URL}" width="480" height="320"></iframe>`,
  )
})

server.listen(PORT, '127.0.0.1', () => {
  process.stdout.write(`framing parent listening on http://127.0.0.1:${PORT}\n`)
})
