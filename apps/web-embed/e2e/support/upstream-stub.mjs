import { createServer } from 'node:http'

/**
 * Stand-in for the NOA API, for the browser checks (the proxy route and the card page).
 *
 * The e2e questions here are about *this app*: does a browser cookie on the embed origin reach the
 * API through the proxy, and does a decision leave a frame whose sandbox omits `allow-forms`. A
 * real API would drag Postgres, LDAP and a session mint into a Playwright run and turn any of their
 * failures into a red proxy test.
 *
 * It records what it was asked for, so a refusal the proxy makes can be told apart from a refusal
 * the upstream makes. Without `/__hits` the "login is not proxied" assertion would pass just
 * as well against a proxy that forwarded the request to an upstream that happened to 404 — a
 * compare that proves nothing. The approval specs lean on the same counter from the other side: a decision POST that
 * *did* arrive is what makes "a form submit did not" mean something.
 */

const PORT = Number(process.env.UPSTREAM_STUB_PORT ?? 8099)

/** @type {Record<string, number>} */
const hits = {}

// Card ids the approval specs address, one per outcome the page renders. Handed in by
// `playwright.config.ts`, which is also where the specs read them from — one source, so a spec
// cannot ask about a state this stub does not serve.
const PENDING_ID = process.env.STUB_PENDING_ID ?? ''
const DECIDED_ID = process.env.STUB_DECIDED_ID ?? ''
const UNAUTHORIZED_ID = process.env.STUB_UNAUTHORIZED_ID ?? ''
const NOT_FOUND_ID = process.env.STUB_NOT_FOUND_ID ?? ''

// The one card that MOVES between reads (approve runs async, state in DB). Everything else this stub serves is a fixed
// state; this id is how the browser lane can watch a run reach a terminal one.
const POLLING_ID = process.env.STUB_POLLING_ID ?? ''

// The card whose SESSION comes back (the 401 card): 401 on the first read, a PENDING card after that. Its
// own id because `UNAUTHORIZED_ID` answers 401 forever — that is the state the 401 card renders, and a retry
// can never escape it. This id is the operator who went and signed in.
const RECOVERS_ID = process.env.STUB_RECOVERS_ID ?? ''
const RUN_RESULT = process.env.STUB_RUN_RESULT ?? ''
const RECEIPT_AFTER = process.env.STUB_RECEIPT_AFTER ?? ''

/**
 * The card whose values REFLOW, for the oscillation the self-sizing could have shipped.
 *
 * `.factValue` is monospace with `word-break: break-word`, so its line count depends on the width
 * of the card's content box — and that width changes by the width of a scrollbar when the frame
 * grows past the content. That is the loop: taller frame, no overflow, scrollbar removed, box
 * wider, wrapped lines fit, measurement drops, frame shrinks, scrollbar back.
 *
 * The lengths are staggered on purpose, so at least one value sits near a line boundary at the
 * frame's width instead of all of them wrapping comfortably in the middle of a line.
 */
const REFLOW_ID = process.env.STUB_REFLOW_ID ?? ''

function reflowArguments() {
  const unit = 'noa-reflow-probe-value-'
  const argument = {}
  for (const [index, length] of [74, 116, 158, 200].entries()) {
    argument[`payload_${index}`] = unit.repeat(12).slice(0, length)
  }
  return argument
}

/**
 * The two cards whose receipt carries a DELTA — what the change did, as the runner stated it.
 *
 * Their own ids because every other card here answers `receipt: null` or a receipt with no delta,
 * and a layout claim about the delta section cannot be made against a card that has none.
 *
 * Two of them, because the two claims need opposite fixtures. The account change is the ordinary
 * one: an identity, a verification state and a single field that moved, which is the card that has
 * to be worth one screenshot. The firewall change is the widest answer any runner produces — two
 * backend rows, a source that went silent, a resolved expiry and a capped reading — with names
 * long enough that a narrow frame has to wrap them.
 */
const DELTA_ACCOUNT_ID = process.env.STUB_DELTA_ACCOUNT_ID ?? ''
const DELTA_FIREWALL_ID = process.env.STUB_DELTA_FIREWALL_ID ?? ''

/** A finished run and the receipt it wrote, for the two delta cards. */
function deltaCardBody(id, toolName, delta, after) {
  return {
    ...cardBody(id, { pending: false }),
    tool_name: toolName,
    run: {
      tool_run_id: TOOL_RUN_ID,
      status: 'COMPLETED',
      result_summary: RUN_RESULT,
      created_at: '2026-08-08T09:30:00+00:00',
      completed_at: '2026-08-08T09:30:12+00:00',
    },
    receipt: {
      ok: true,
      before: { suspended: false, domain: 'acme.example' },
      after,
      error_code: null,
      delta,
    },
  }
}

/** `GET` reads per id, so the polling card can answer differently the second time. */
const reads = {}

const CSRF = process.env.STUB_CSRF ?? 'v1.1786000000.stub-signature'
const TOOL_RUN_ID = '5c2f1a90-0000-4000-8000-000000000001'

// The large-READ table surface's tokens, one per outcome that page renders. Same rule as
// the card ids above: handed in by `playwright.config.ts`, which is where the specs read them.
// No token for the whole-listing case: any token this stub does not recognise is served as one,
// the way any unrecognised card id is served PENDING.
const TABLE_TRUNCATED_TOKEN = process.env.STUB_TABLE_TRUNCATED_TOKEN ?? ''
const TABLE_UNAUTHORIZED_TOKEN = process.env.STUB_TABLE_UNAUTHORIZED_TOKEN ?? ''
const TABLE_NOT_FOUND_TOKEN = process.env.STUB_TABLE_NOT_FOUND_TOKEN ?? ''

// Served only to a request that carried a cookie. The separator for "the operator's session
// reached the API through the page's own read": every other token here answers 200 regardless, so
// that spec would pass with the cookie dropped.
const TABLE_NEEDS_COOKIE_TOKEN = process.env.STUB_TABLE_NEEDS_COOKIE_TOKEN ?? ''
const TABLE_TOTAL_ROWS = Number(process.env.STUB_TABLE_TOTAL_ROWS ?? 1240)

/**
 * The listing whose size started the self-sizing work: every matched row on one page.
 *
 * A real count rather than a round one. At the row height `table.module.css` produces — 0.8125rem
 * type on a 1.5 line-height with `--noa-space-2` of vertical padding, about 36px — this is on the
 * order of 15,000px of natural height, which the host would apply verbatim. What the browser lane
 * asks of it is that the request stays bounded and the rows keep scrolling inside the frame.
 */
const TABLE_LONG_TOKEN = process.env.STUB_TABLE_LONG_TOKEN ?? ''
const TABLE_LONG_ROWS = Number(process.env.STUB_TABLE_LONG_ROWS ?? 438)

/** Two rows, whatever the total says — a capped page holds fewer rows than it matched. */
const TABLE_ROWS = [
  { user: 'acmeco', domain: 'acme.example' },
  { user: 'betaco', domain: 'beta.example' },
]

function longTableRows() {
  return Array.from({ length: TABLE_LONG_ROWS }, (_unused, index) => ({
    user: `account-${String(index).padStart(4, '0')}`,
    domain: `site-${String(index).padStart(4, '0')}.example`,
  }))
}

/** The body the API's `GET /tables/{token}` sends (the embed app's contract: summary plus URL, capped read ships its bound). */
function tableBody(token, { truncated, rows = TABLE_ROWS }) {
  return {
    token,
    tool_name: 'whm_list_accounts',
    columns: [
      { key: 'user', label: 'Account' },
      { key: 'domain', label: 'Primary domain' },
    ],
    rows,
    // The count before the cut when capped, and the rows themselves when not: the two states the
    // cap's bound separates, served as two tokens so a spec can assert the page tells them apart.
    total_rows: truncated ? TABLE_TOTAL_ROWS : rows.length,
    stored_rows: rows.length,
    truncated,
    created_at: '2026-08-09T09:00:00+00:00',
    expires_at: '2126-08-10T09:00:00+00:00',
  }
}

/** The card body the API's `GET /action-requests/{id}` sends (the embed app's contract). */
function cardBody(id, { pending }) {
  return {
    action_request_id: id,
    tool_name: 'whm_suspend_account',
    status: pending ? 'PENDING' : 'APPROVED',
    conversation_ref: '1f0c2e5a-7b41-4d2e-9a3c-0b5d8e6f4a12',
    requester: { email: 'operator@noa.internal', librechat_user_id: 'librechat-user-1' },
    arguments: { server_ref: 'alpha', account: 'acmeco' },
    // The before-state the card exists to show — a value, so a spec asserting it is
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
    // What the change did, once something recorded it. `null` everywhere but the polling
    // card's terminal read: a receipt lands with the run's terminal write, not with the decision.
    receipt: null,
    // `null` once nothing may be decided: no live token for a card with no door.
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
 * This is the JS-`fetch` rule's mechanism under test rather than asserted: the card ships a `fetch` because
 * a form submit dies silently in this sandbox, and a spec that only checked the `fetch` worked
 * would never notice if that premise stopped being true.
 */
function sandboxControl(prefix) {
  return `<!doctype html><meta charset="utf-8"><title>sandbox control</title>
<form id="viaForm" method="post" action="/__probe/${prefix}form"><button type="submit">submit</button></form>
<script>
  window.probe = async () => {
    let fetched = 'ok'
    try {
      await fetch('/__probe/${prefix}fetch', { method: 'POST' })
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
}

/**
 * The same document, on counter paths of its own, for the tab the 401 card's link-out opens.
 *
 * A separate prefix rather than a second use of the paths above, because the fetch control asserts
 * `POST /__probe/form` is **undefined** — a shared counter would let one spec's probe satisfy or
 * break the other's, and this stub is one process for every spec file.
 *
 * What it is for: a tab opened from a sandboxed frame inherits the opener's sandbox flags unless
 * `allow-popups-to-escape-sandbox` is granted, absent at every render site. So the question "does
 * the link-out land somewhere an operator can actually sign in" is a question about what this
 * document is still allowed to do, and it answers it on the counter rather than in prose.
 */
const POPUP_PROBE_PREFIX = 'popup-'

const server = createServer((request, response) => {
  const url = new URL(request.url ?? '/', `http://127.0.0.1:${PORT}`)
  const key = `${request.method} ${url.pathname}`

  if (url.pathname === '/__hits') {
    json(response, 200, hits)
    return
  }

  if (url.pathname === '/__sandbox-control' || url.pathname === '/__popup-control') {
    response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' })
    response.end(
      sandboxControl(url.pathname === '/__popup-control' ? POPUP_PROBE_PREFIX : ''),
    )
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
        // The attribute the browser must see survive the hop. `.noa.internal` would be
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
    if (id === RECOVERS_ID) {
      // 401 for the page's own server-side read, a card for everything after it: the
      // operator signed in between the two, and the retry is what asks again.
      reads[id] = (reads[id] ?? 0) + 1
      if (reads[id] === 1) {
        json(response, 401, { error_code: 'session_invalid', message: 'Sign in to NOA.' })
        return
      }

      json(response, 200, {
        ...cardBody(id, { pending: true }),
        seen_cookie: request.headers['cookie'] ?? null,
      })
      return
    }

    if (id === REFLOW_ID) {
      json(response, 200, {
        ...cardBody(id, { pending: true }),
        arguments: reflowArguments(),
      })
      return
    }

    if (id === DELTA_ACCOUNT_ID) {
      json(
        response,
        200,
        deltaCardBody(
          id,
          'whm_suspend_account',
          {
            identity: { server: 'alpha', username: 'acmeco' },
            verification: 'verified',
            changed_fields: [{ field: 'suspended', old: false, new: true }],
          },
          { ok: true, suspended: true, suspended_at: RECEIPT_AFTER },
        ),
      )
      return
    }

    if (id === DELTA_FIREWALL_ID) {
      json(
        response,
        200,
        deltaCardBody(
          id,
          'whm_firewall_release_and_allow',
          {
            // The keys `whm_firewall_change.py::_release_delta` actually publishes.
            identity: { server: 'alpha', target: '203.0.113.24' },
            // No `changed_fields` key at all — one backend never answered the confirming read, so
            // there is no field diff to state. Absent, not empty: the two are different claims.
            verification: 'unavailable',
            backends: [
              {
                name: 'csf-alpha.storage-07.jakarta-dc1.internal.acme.example',
                driven: true,
                answered: true,
                verdict: 'allowed',
                error_code: null,
              },
              {
                name: 'csf-beta.storage-11.jakarta-dc2.internal.acme.example',
                driven: true,
                answered: false,
                verdict: null,
                error_code: null,
              },
            ],
            unanswered: ['csf-beta.storage-11.jakarta-dc2.internal.acme.example'],
            // Verbatim from the runner: an ISO stamp and the window it was resolved from.
            new_values: { expires_at: '2026-08-08T11:06:12+07:00', duration_minutes: 60 },
            bound: { total: 20, truncated: true },
          },
          { ok: true, verdict: 'allowed' },
        ),
      )
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
        // The receipt arrives with the terminal write and not before (run and receipt, one commit), so the
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

  if (request.method === 'GET' && url.pathname.startsWith('/tables/')) {
    const token = url.pathname.slice('/tables/'.length)

    if (token === TABLE_UNAUTHORIZED_TOKEN) {
      json(response, 401, { error_code: 'session_invalid', message: 'Sign in to NOA.' })
      return
    }
    if (token === TABLE_NEEDS_COOKIE_TOKEN && !request.headers['cookie']) {
      json(response, 401, { error_code: 'session_invalid', message: 'Sign in to NOA.' })
      return
    }
    if (token === TABLE_NOT_FOUND_TOKEN) {
      // One body for unknown, foreign, orphaned and expired alike — the API answers all
      // four this way, and the page has one sentence for the lot.
      json(response, 404, {
        error_code: 'result_table_not_found',
        message: 'That table is not available. It may have expired — run the tool again.',
      })
      return
    }

    json(response, 200, {
      ...tableBody(token, {
        truncated: token === TABLE_TRUNCATED_TOKEN,
        rows: token === TABLE_LONG_TOKEN ? longTableRows() : TABLE_ROWS,
      }),
      // Echoed for the same reason the card body echoes it: a spec can then assert the operator's
      // cookie reached the API through the page's own server-side read.
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
