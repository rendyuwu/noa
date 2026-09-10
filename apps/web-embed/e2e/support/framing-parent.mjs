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
 * `45cc53c4` — `allow-scripts allow-same-origin`, with `allow-forms` absent — which is
 * the whole premise V80 rests on. Both parameters default to §T.45's fixed frame, so those specs
 * are unaffected by the addition.
 *
 * **It also resizes the frame on request, because the real host does.** `@mcp-ui/client` listens for
 * a `ui-size-change` message from the framed document and applies the height it carries to the
 * iframe's inline style — verbatim, with no clamp of its own (measured at the same pin:
 * `spikes/librechat-embed-render-gate/resize_probe.mjs`, and 20000px was honoured). Two details of
 * that behaviour are copied rather than approximated, because a harness that is more forgiving than
 * the host proves nothing: the message is accepted **only** from the frame's own `contentWindow`
 * (the package gates on `event.source`), and every accepted payload is recorded so a spec can count
 * them instead of guessing whether a run settled.
 *
 * `?height` pins the box the frame opens with — the small fixed one is the state the embed's
 * self-sizing exists to escape — and `?autosize=0` makes this parent ignore the message entirely,
 * which is the surface's degraded path: a host that never grew the frame.
 */

const PORT = Number(process.env.FRAMING_PARENT_PORT ?? 8110)
const FRAMED_URL = process.env.FRAMING_PARENT_TARGET ?? 'http://localhost:3001/healthz'

/** The box §T.45's fixed frame opens with, unchanged so the specs that predate `?height` are. */
const DEFAULT_FRAME_HEIGHT = 640

/** Only `src` and `sandbox` are honoured, and both are attribute values, so both are escaped. */
function attribute(value) {
  return value.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')
}

/** A pixel count, or the default. Never interpolated as text — see `attribute` above. */
function pixels(raw, fallback) {
  const value = Number(raw)
  return Number.isInteger(value) && value > 0 && value < 100_000 ? value : fallback
}

const server = createServer((request, response) => {
  const url = new URL(request.url ?? '/', `http://127.0.0.1:${PORT}`)
  const src = url.searchParams.get('src') ?? FRAMED_URL
  const sandbox = url.searchParams.get('sandbox')
  const height = pixels(url.searchParams.get('height'), DEFAULT_FRAME_HEIGHT)
  const autosize = url.searchParams.get('autosize') !== '0'

  response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' })
  response.end(
    `<!doctype html><meta charset="utf-8"><title>framing parent</title>` +
      `<iframe id="card" src="${attribute(src)}" width="480" height="${height}"` +
      (sandbox === null ? '' : ` sandbox="${attribute(sandbox)}"`) +
      `></iframe>` +
      `<script>
        window.__sizePosts = [];
        var frame = document.getElementById('card');
        window.addEventListener('message', function (event) {
          // The source gate the package itself applies. A harness that accepted a size change from
          // anywhere would let a spec pass against a document that never posted one.
          if (event.source !== frame.contentWindow) return;
          var data = event.data;
          if (!data || data.type !== 'ui-size-change') return;
          window.__sizePosts.push(data.payload);
          // The inline style, which is where the measured host writes it.
          if (${autosize ? 'true' : 'false'}) frame.style.height = data.payload.height + 'px';
        });
      </script>`,
  )
})

server.listen(PORT, '127.0.0.1', () => {
  process.stdout.write(`framing parent listening on http://127.0.0.1:${PORT}\n`)
})
