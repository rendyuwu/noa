import { createServer } from 'node:http'

/**
 * A page that puts a document in an iframe, for the framing and approval specs (§T.45, §T.41).
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
 *
 * **`?src` and `?sandbox` are how §T.41 borrows this** rather than standing up a second parent. The
 * approval specs frame the card under the sandbox string LibreChat was measured applying at pin
 * `45cc53c4` — `allow-scripts allow-same-origin`, with `allow-forms` absent (R13, R29) — which is
 * the whole premise V80 rests on. Both parameters default to §T.45's fixed frame, so those specs
 * are unaffected by the addition.
 */

const PORT = Number(process.env.FRAMING_PARENT_PORT ?? 8110)
const FRAMED_URL = process.env.FRAMING_PARENT_TARGET ?? 'http://localhost:3001/healthz'

/** Only `src` and `sandbox` are honoured, and both are attribute values, so both are escaped. */
function attribute(value) {
  return value.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')
}

const server = createServer((request, response) => {
  const url = new URL(request.url ?? '/', `http://127.0.0.1:${PORT}`)
  const src = url.searchParams.get('src') ?? FRAMED_URL
  const sandbox = url.searchParams.get('sandbox')

  response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' })
  response.end(
    `<!doctype html><meta charset="utf-8"><title>framing parent</title>` +
      `<iframe id="card" src="${attribute(src)}" width="480" height="640"` +
      (sandbox === null ? '' : ` sandbox="${attribute(sandbox)}"`) +
      `></iframe>`,
  )
})

server.listen(PORT, '127.0.0.1', () => {
  process.stdout.write(`framing parent listening on http://127.0.0.1:${PORT}\n`)
})
