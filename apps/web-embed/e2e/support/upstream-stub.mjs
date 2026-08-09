import { createServer } from 'node:http'

/**
 * Stand-in for the NOA API, for the browser checks (§T.44, §T.41).
 *
 * The e2e questions here are about *this app*: does a browser cookie on the embed origin reach the
 * API through the proxy, and does a decision leave a frame whose sandbox omits `allow-forms`. A
 * real API would drag Postgres, LDAP and a session mint into a Playwright run and turn any of their
 * failures into a red proxy test.
 *
 * It records what it was asked for, so a refusal the proxy makes can be told apart from a refusal
 * the upstream makes. Without `/__hits` the "login is not proxied" assertion (V42) would pass just
 * as well against a proxy that forwarded the request to an upstream that happened to 404 — V87's
 * shape. The approval specs lean on the same counter from the other side: a decision POST that
 * *did* arrive is what makes "a form submit did not" mean something.
 */

const PORT = Number(process.env.UPSTREAM_STUB_PORT ?? 8099)

/** @type {Record<string, number>} */
const hits = {}

// Card ids the approval specs address, one per outcome the page renders (§T.41). Handed in by
// `playwright.config.ts`, which is also where the specs read them from — one source, so a spec
// cannot ask about a state this stub does not serve (V66).
const PENDING_ID = process.env.STUB_PENDING_ID ?? ''
const DECIDED_ID = process.env.STUB_DECIDED_ID ?? ''
const UNAUTHORIZED_ID = process.env.STUB_UNAUTHORIZED_ID ?? ''
const NOT_FOUND_ID = process.env.STUB_NOT_FOUND_ID ?? ''

// The one card that MOVES between reads (§T.42, V29). Everything else this stub serves is a fixed
// state; this id is how the browser lane can watch a run reach a terminal one.
const POLLING_ID = process.env.STUB_POLLING_ID ?? ''
const RUN_RESULT = process.env.STUB_RUN_RESULT ?? ''
const RECEIPT_AFTER = process.env.STUB_RECEIPT_AFTER ?? ''

/** `GET` reads per id, so the polling card can answer differently the second time. */
const reads = {}

const CSRF = process.env.STUB_CSRF ?? 'v1.1786000000.stub-signature'
const TOOL_RUN_ID = '5c2f1a90-0000-4000-8000-000000000001'

/** The card body the API's `GET /action-requests/{id}` sends (§I.embed). */
function cardBody(id, { pending }) {
  return {
    action_request_id: id,
    tool_name: 'whm_suspend_account',
    status: pending ? 'PENDING' : 'APPROVED',
    conversation_ref: '1f0c2e5a-7b41-4d2e-9a3c-0b5d8e6f4a12',
    requester: { email: 'operator@noa.internal', librechat_user_id: 'librechat-user-1' },
    arguments: { server_ref: 'alpha', account: 'acmeco' },
    // The before-state the card exists to show (C9, V17) — a value, so a spec asserting it is
    // rendered is asserting against something rather than against an empty object.
    evidence: { suspended: false, domain: 'acme.example' },
    created_at: '2026-08-08T09:00:00+00:00',
    expires_at: '2126-08-08T10:00:00+00:00',
    decided_at: pending ? null : '2026-08-08T09:30:00+00:00',
    run: pending
      ? null
      : {
          tool_run_id: TOOL_RUN_ID,
          status: 'STARTED',
          result_summary: null,
          created_at: '2026-08-08T09:30:00+00:00',
          completed_at: null,
        },
    // What the change did, once something recorded it (V46). `null` everywhere but the polling
    // card's terminal read: a receipt lands with the run's terminal write, not with the decision.
    receipt: null,
    // `null` once nothing may be decided (V39): no live token for a card with no door.
    csrf: pending ? CSRF : null,
  }
}

function json(response, status, body, extraHeaders = {}) {
  response.writeHead(status, {
    'content-type': 'application/json',
    'x-request-id': 'stub-request-id',
    ...extraHeaders,
  })
  response.end(JSON.stringify(body))
}

/**
 * A document that probes what its own sandbox permits.
 *
 * Served from *this* origin so both probes are same-origin: no CORS to configure, and nothing
 * between the sandbox and the counter. It fires a `fetch` POST and submits a native `<form>` POST
 * to two different paths, so `/__hits` says which of the two the browser actually performed.
 *
 * This is V80's mechanism under test rather than asserted (V84c): the card ships a `fetch` because
 * a form submit dies silently in this sandbox, and a spec that only checked the `fetch` worked
 * would never notice if that premise stopped being true.
 */
const SANDBOX_CONTROL = `<!doctype html><meta charset="utf-8"><title>sandbox control</title>
<form id="viaForm" method="post" action="/__probe/form"><button type="submit">submit</button></form>
<script>
  window.probe = async () => {
    let fetched = 'ok'
    try {
      await fetch('/__probe/fetch', { method: 'POST' })
    } catch (error) {
      fetched = String(error)
    }
    let submitted = 'ok'
    try {
      document.getElementById('viaForm').submit()
    } catch (error) {
      submitted = String(error)
    }
    return { fetched, submitted }
  }
</script>`

const server = createServer((request, response) => {
  const url = new URL(request.url ?? '/', `http://127.0.0.1:${PORT}`)
  const key = `${request.method} ${url.pathname}`

  if (url.pathname === '/__hits') {
    json(response, 200, hits)
    return
  }

  if (url.pathname === '/__sandbox-control') {
    response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' })
    response.end(SANDBOX_CONTROL)
    return
  }

  hits[key] = (hits[key] ?? 0) + 1

  if (url.pathname === '/auth/me' && request.method === 'GET') {
    json(
      response,
      200,
      {
        cookie: request.headers['cookie'] ?? null,
        authorization: request.headers['authorization'] ?? null,
      },
      {
        // The attribute the browser must see survive the hop (V40). `.noa.internal` would be
        // rejected for a localhost document, so the shape is what is asserted here; the real
        // domain is the API's setting.
        'set-cookie': 'stub_echo=1; Path=/; SameSite=Lax',
      },
    )
    return
  }

  if (request.method === 'GET' && url.pathname.startsWith('/action-requests/')) {
    const id = url.pathname.slice('/action-requests/'.length)

    if (id === UNAUTHORIZED_ID) {
      json(response, 401, { error_code: 'session_invalid', message: 'Sign in to NOA.' })
      return
    }
    if (id === NOT_FOUND_ID) {
      json(response, 404, {
        error_code: 'action_request_not_found',
        message: 'That approval request does not exist, or it is not yours to decide.',
      })
      return
    }

    if (id === POLLING_ID) {
      reads[id] = (reads[id] ?? 0) + 1
      // STARTED for the page's own server-side read and for the first poll, terminal afterwards.
      // Two reads of headroom so a spec can assert the in-flight state without racing the poll
      // interval, and so the transition it then waits for is a real one rather than the first
      // answer it ever saw.
      const finished = reads[id] > 2

      json(response, 200, {
        ...cardBody(id, { pending: false }),
        run: {
          tool_run_id: TOOL_RUN_ID,
          status: finished ? 'COMPLETED' : 'STARTED',
          result_summary: finished ? RUN_RESULT : null,
          created_at: '2026-08-08T09:30:00+00:00',
          completed_at: finished ? '2026-08-08T09:30:12+00:00' : null,
        },
        // The receipt arrives with the terminal write and not before (§T.42(b), V46), so the
        // browser lane watches an outcome section appear rather than finding one already there.
        // Two halves, sharing no value, because "both halves render" is what is asserted.
        receipt: finished
          ? {
              ok: true,
              before: { suspended: false, domain: 'acme.example' },
              after: { suspended: true, suspended_at: RECEIPT_AFTER },
              error_code: null,
            }
          : null,
        seen_cookie: request.headers['cookie'] ?? null,
      })
      return
    }

    json(response, 200, {
      ...cardBody(id, { pending: id !== DECIDED_ID }),
      // Echoed so a spec can assert the operator's cookie reached the API through the page's
      // server-side read, not only through the browser-facing proxy.
      seen_cookie: request.headers['cookie'] ?? null,
    })
    return
  }

  if (request.method === 'POST' && url.pathname.endsWith('/approve')) {
    json(response, 202, { action_request_id: PENDING_ID, tool_run_id: TOOL_RUN_ID })
    return
  }

  if (request.method === 'POST' && url.pathname.endsWith('/deny')) {
    json(response, 200, { action_request_id: PENDING_ID, status: 'DENIED' })
    return
  }

  json(response, 200, { reached: key })
})

server.listen(PORT, '127.0.0.1', () => {
  process.stdout.write(`upstream stub listening on http://127.0.0.1:${PORT}\n`)
})
