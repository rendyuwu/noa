# LibreChat embed render gate — verdict

**Verdict: PASS.** Path A (iframe UI resource) is live at LibreChat pin
`45cc53c40b47645b887c3bb996168e06aaa83f4c`. Measured 2026-08-08 by a browser run against a
LibreChat built from that commit, talking over MCP to a NOA process, in Chromium.

This closes `§T.59`'s two owed items — (d) the live cookie-into-frame run, and (f) whether
LibreChat honours `notifications/tools/list_changed`. Items (a), (b), (c), (e) were answered
earlier by source read (`R11`–`R17`) and are re-asserted here against the installed artifacts
rather than against GitHub.

`C21` requires re-verification on every LibreChat bump. The harness is committed at
`spikes/librechat-embed-render-gate/` so that is a command, not a rebuild — see its README.

## What was measured

Rig: LibreChat at the pin (built from source, `client/dist` from its own vite build), MongoDB
7, model `claude-sonnet-5` through a local Anthropic-compatible proxy, NOA on
`noa.internal:8000` serving its real `/mcp` mount plus a probe mount, Chromium via Playwright.
Hostnames `chat.noa.internal` (parent) and `embed.noa.internal` (frame) resolve to loopback
and share the registrable domain `noa.internal`, which is the topology `V40` describes.

### (d) The frame sits on NOA's origin and the cookie rides into it

The MCP result carried `ui://noa/approval/<uuid>` with `mimeType: text/uri-list` and the NOA
URL as its body. Both of LibreChat's render sites fired, and the numbers are the point:

| Rendered by | `src` | `srcdoc` | `sandbox` |
|---|---|---|---|
| `ToolCallInfo` (tool-call panel) | `http://embed.noa.internal:8000/embed-probe/frame?id=…` | absent | `allow-scripts allow-same-origin` |
| `MCPUIResource` (the `\ui{id}` marker in assistant text) | same URL | absent | `allow-popups allow-scripts allow-same-origin` |

Inside that frame, read from the document itself:

- `window.location.origin` → `http://embed.noa.internal:8000` — NOA's origin, not an opaque one.
- `document.cookie` → empty. The session cookie is httpOnly and stays unreadable (`V6`).
- `GET /auth/me` with `credentials: 'include'` → **200**, identity `operator@noa.internal`.
- `POST /embed-probe/decide` with `credentials: 'include'` → **200**, `authenticated_as:
  operator@noa.internal`, `origin` header `http://embed.noa.internal:8000`.

The cookie recorded by the browser for that run: `noa_session`, `domain=.noa.internal`,
`httpOnly=true`, `sameSite=Lax`, `secure=false` (plain-HTTP rig). So the decision POST `V22`
requires is possible from inside the iframe, by JS `fetch`, with the operator's session.

`allow-forms` was absent from **both** sandbox strings. `V80` therefore holds live, not just
by source read: a native `<form>` submit is not available in-frame, and the approve/deny POST
must be a `fetch`.

### (d) negative control — the same page delivered as `text/html`

Returned as an inline `text/html` UI resource, the identical document rendered through
`srcdoc` with `sandbox="allow-scripts"` (and `allow-popups allow-scripts` at the other site) —
no `allow-same-origin`. From inside it:

- `window.location.origin` → `null`
- `document.cookie` → `SecurityError: … The document is sandboxed and lacks the
  'allow-same-origin' flag.`
- both requests → `TypeError: Failed to fetch`

That is `C17`'s failure mode, observed. It is also what makes the PASS above a measurement
rather than a tautology: the same probe, same server, same browser, answers 200 on one path
and cannot even read a cookie on the other.

### (f) `notifications/tools/list_changed` — LibreChat does NOT honour it

Two independent reads, agreeing:

- **On the wire.** With a session's GET stream open, calling the probe's notify tool made NOA
  emit `{"method":"notifications/tools/list_changed","jsonrpc":"2.0"}` on that stream. The
  notification is real and NOA can send it.
- **In LibreChat.** The same call from inside a chat turn produced no follow-up. The JSON-RPC
  method log for that turn reads `initialize`, `notifications/initialized`, `ping`,
  `tools/list`, `tools/call` — and nothing after. Source agrees: at this pin
  `packages/api/src/mcp/connection.ts:1852` registers a handler for
  `ResourceListChangedNotificationSchema` only; `ToolListChangedNotificationSchema` appears
  nowhere in the tree.

Consequence for `T66`: emitting the notification is not harmful (the client ignores it without
erroring or dropping the connection), but it buys nothing at this pin. A revoked tool can stay
in LibreChat's per-user catalog until that connection is rebuilt, which `V74` already bounds —
`V1` re-checks permission at execution, so the stale entry is a display artifact, not an
authorization hole.

## Findings that are not about rendering

Three things the run surfaced that bear on `T57` (the `librechat.yaml` NOA entry) and were not
predicted by the source read:

1. **A header-authenticated MCP server gets marked OAuth-required at boot.** LibreChat
   inspects every startup server with no user in context, so `{{LIBRECHAT_USER_ID}}` has
   nothing to resolve against and NOA answers 401 — correctly, per `C24`/`V3`. LibreChat reads
   that 401 as "this server wants OAuth" (`requiresOAuth: true`,
   `connectionState: needs_authorization`) and stops connecting; `/api/mcp/tools` then reports
   zero tools forever. **`startup: false` plus `requiresOAuth: false` in the server entry is
   required**, not optional, and neither is a workaround for the rig: both follow directly
   from `C24`.
2. **An internal MCP host needs an explicit domain allowlist.** With no
   `mcpSettings.allowedDomains`, LibreChat enables SSRF protection and refuses a URL that
   resolves to a private or loopback address: `Domain "http://noa.internal:8000" is not
   allowed`. `allowedAddresses` will not take a CIDR or a loopback literal at this pin
   (schema: host:port pairs, private space only), so the domain list is the lever.
3. **The resource id and marker are LLM-visible.** LibreChat's parser
   (`packages/api/src/mcp/parsers.ts:183`) requires the `ui://` scheme, hashes the resource
   body into a `resourceId`, and injects `UI Resource ID: <id>` and `UI Resource Marker:
   \ui{<id>}` into the text the model sees — which is what `V71`'s `\ui{}` instruction is for.
   Nothing about the reason or the decision travels there (`C8` untouched), but it confirms the
   approval URL and id land in LibreChat's MongoDB (`V26`).

## Deviations in the harness (what this run did not exercise)

- **Token delivery.** The bearer token was written into the config from an env var rather than
  through `customUserVars.NOA_MCP_TOKEN`, because per-user vars are entered in LibreChat's UI
  and this run drives the API. Header *set* was production's: `Authorization`,
  `X-Noa-LibreChat-User: {{LIBRECHAT_USER_ID}}`, `X-Noa-Conversation-Ref:
  {{LIBRECHAT_BODY_CONVERSATIONID}}`.
- **Probe tools are not NOA tools.** They live on a second FastMCP server at
  `/mcp-embed-probe` with the same verifier and the same 401 middleware, because
  `register_mcp_tools` refuses any name outside `TOOL_CATALOG` (`V10`, `V83a`) — the guard
  that should stop a throwaway tool from entering the real registry. Consequently the run
  exercised token auth and TOFU binding (`V3`) but **not** `RbacToolMiddleware` or
  `ToolRunAuditMiddleware`.
- **`/embed-probe/decide` is not the approve endpoint.** It authenticates the caller through
  production's own `require_session_user` and answers; it implements no CSRF token (`V39`), no
  row lock (`V28`), no reason (`V15`). `T33`/`T37` build the real one.
- **Plain HTTP.** The rig speaks HTTP, so `AUTH_SESSION_COOKIE_SECURE` is off (development
  forces it) and LibreChat needed `SESSION_COOKIE_SECURE=false` to set its own refresh cookie
  at all. A TLS deployment changes the cookie flags, not the origin or sandbox facts measured
  here.
- **One browser.** Chromium 141 (Playwright build v1194). Firefox and WebKit were not run.

## Reproduce

```bash
spikes/librechat-embed-render-gate/verify_librechat_pin.sh          # E1: rig is the pin
cd spikes/librechat-embed-render-gate && node browser_probe.mjs      # E2-E5: the live run
```

Evidence from the recorded run — `probe-results.json`, `uri-list-render.png`,
`html-control-render.png` — is written to `spikes/librechat-embed-render-gate/evidence/`.
