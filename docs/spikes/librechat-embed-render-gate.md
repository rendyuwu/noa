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
  row lock (`V28`), no reason (`V15`). The real one is
  `POST /action-requests/{id}/approve` (`T33` writes the row, `T37` decides it) — this rig
  measured the render and cookie path only, and re-running it does not exercise that endpoint.
- **Plain HTTP.** The rig speaks HTTP, so `AUTH_SESSION_COOKIE_SECURE` is off (development
  forces it) and LibreChat needed `SESSION_COOKIE_SECURE=false` to set its own refresh cookie
  at all. A TLS deployment changes the cookie flags, not the origin or sandbox facts measured
  here.
- **One browser.** Chromium 141 (Playwright build v1194). Firefox and WebKit were not run.

## Three assertions added for the frame-sizing work

`verify_librechat_pin.sh` also asserts three properties that the embed's frame-sizing work rests
on, alongside the render-path facts measured above. They are three parts of one connective
property: LibreChat has to ASK for auto-resize, the library has to ACCEPT the ask, and the
library has to HANDLE the message that answers it. Any one going missing kills sizing, and no one
of the three checks can see the other two failing.

**`autoResizeIframe` still passed, with both axes enabled, at all three render sites.** `R12`'s
three sites (`MCPUIResource.tsx`, `ToolCallInfo.tsx`, `UIResourceCarousel.tsx`) each pass an
`autoResizeIframe: { width: true, height: true }` option into `UIResourceRenderer` at this pin
— at lines 44, 171, and 115 respectively. Frame sizing depends on that option surviving a
LibreChat bump. The three files are checked one at a time rather than with a single combined
grep, so a bump that drops the option at one site produces a failure message naming that site
instead of a generic "sizing broke somewhere."

The pattern is value-aware rather than a grep for the bare token, and has to be. At this pin the
key and its value sit on ONE line, so a token grep keeps passing when the value is flipped to
`false` and keeps passing when the whole line is commented out — both of which disable sizing
while leaving `autoResizeIframe` in the file. The pattern therefore anchors at the start of the
line, permitting only whitespace before the key (which rules out a `//` or `/*` prefix), and
requires both axes to read `true`.

Two stated bounds, both from grep being line-oriented. It misses a multi-line block comment
wrapping an otherwise unchanged line, which still matches; catching that needs a parser, not a
pattern, and the live browser run this harness supports is what would catch it. In the other
direction, it fires on a future LibreChat that merely reformats the option across several lines,
where the property is intact — read a red gate there as a stale pattern rather than a broken
render path, and answer it by re-reading the source at the new pin and re-anchoring the pattern
to it, never by loosening it back toward a bare-token grep.

**Shipped `@mcp-ui/client` bytes still handle `ui-size-change`, and still accept
`autoResizeIframe`.** `V69`'s rule — upstream provenance is not evidence a control works, so a
control needs a test against the real mechanism — applies here exactly as it does to the script's
existing `text/uri-list` and `allow-forms` checks. `V69`'s literal subject is the ported SSH
host-key control; the rule is being applied one surface over, to shipped bytes versus the source
they were published from. The real mechanism is what npm installed, so both assertions run
against `node_modules/@mcp-ui/client/dist/index.mjs`, the bytes LibreChat actually loads, not
mcp-ui's repo source.

At `5.7.0` the message constant is defined at line 131 (`UI_SIZE_CHANGE: "ui-size-change"`) and
consumed by the resize handler at lines 192–196, which reads the posted `width`/`height` off the
message and writes them onto the iframe's inline style. That write is gated on the
`autoResizeIframe` option, destructured out of the renderer's props at line 142 — the file's only
occurrence of the name. Both string literals are grepped, the same standard the existing
`text/uri-list` and sandbox-string checks already use against these bytes.

The second of the two is the connective half, and it is why the check is not redundant with the
other eleven. A library bump that renames or drops that destructure, while leaving the protocol
constant intact and LibreChat's own source untouched, leaves the `ui-size-change` check and all
three render-site checks green with the wiring between "LibreChat asks for resize" and "the
library honours the ask" silently severed. Round 1 below is exactly that mutation, and it fails
with the other eleven checks passing. The option NAME is the only stable anchor available: the
gate that consumes it is minified — at `5.7.0` the destructured binding is a single letter — so a
pattern naming the binding would break on any rebuild without the property having changed.

### Demonstrated to detect

Per this repo's rule that a bound control has to be shown to separate, not merely asserted
(`AGENTS.md`, "Test of a CONCURRENCY control" — the same principle applies to any check that is
supposed to fail on a real regression), each assertion was proven to detect by running
`verify_librechat_pin.sh` itself — not a hand-copied predicate — against a mutated tree. A
predicate that only gets exercised in isolation can pass that isolated test while a typo in the
script's own copy of the pattern lets the real thing through; running the actual script closes
that gap.

The clone is ~2.4 GB, too large to duplicate per mutation, so each round used
`cp -al /home/ubuntu/noa/librechat-embed-render-gate "$scratch/clone"` (hardlinks — the `.git`
directory comes along, so the pin-HEAD check still passes against the copy) and then replaced,
never edited in place through, the one hardlinked file under test: `sed` to a new path, `rm` the
hardlink, `mv` the new file over it. Editing through a hardlink would have written into the real
clone. `verify_librechat_pin.sh` already takes the clone directory as its first argument, so each
run pointed straight at the mutated copy with no change to the script.

Each round is its own fresh copy and is bounded on both sides, since "the check failed" is only
evidence if the mutation landed where it was meant to and nowhere else:

- **Before the run.** The scratch file's inode is recorded, and the round aborts if `sed` matched
  nothing — otherwise a pattern that silently failed to apply would be scored as a detection.
- **After the run.** The scratch file's inode must have CHANGED from its pre-mutation value,
  which is what proves the hardlink was broken rather than written through. The real clone's file
  inode AND its sha256 must both be UNCHANGED from values taken before that round.

Then the scratch directory is removed. Twelve rounds, all twelve detected, real clone verified
untouched in all twelve by that inode-and-sha256 comparison. Every row below is a round run in
one session, against the script as it stands here — none is carried forward from an earlier
session's table. The `ok before FAIL` column is how far each run got, out of the twelve `ok`
lines a clean run prints. Rounds 2–10 and 12 share one FAIL message, differing only in the site
it names; it is written out at round 2.

| # | File mutated | Mutation applied | Exit | `ok` before FAIL | Observed FAIL line |
|---|---|---|---|---|---|
| 1 | `node_modules/@mcp-ui/client/dist/index.mjs` | `autoResizeIframe` → `xyzUnknownOption` | 1 | 8 | `FAIL  shipped bytes no longer accept the autoResizeIframe option` |
| 2 | `MCPUIResource.tsx` | value → `{ width: false, height: false }` | 1 | 10 | `FAIL  MCPUIResource.tsx no longer passes autoResizeIframe with both axes enabled (dropped, commented out, or width/height not true)` |
| 3 | `MCPUIResource.tsx` | line commented out (`// ` prefix) | 1 | 10 | same message, `MCPUIResource.tsx` |
| 4 | `ToolCallInfo.tsx` | value → `{ width: false, height: false }` | 1 | 10 | same message, `ToolCallInfo.tsx` |
| 5 | `ToolCallInfo.tsx` | line commented out (`// ` prefix) | 1 | 10 | same message, `ToolCallInfo.tsx` |
| 6 | `UIResourceCarousel.tsx` | value → `{ width: false, height: false }` | 1 | 10 | same message, `UIResourceCarousel.tsx` |
| 7 | `UIResourceCarousel.tsx` | line commented out (`// ` prefix) | 1 | 10 | same message, `UIResourceCarousel.tsx` |
| 8 | `MCPUIResource.tsx` | `autoResizeIframe` → `xyzDisabledProp` | 1 | 10 | same message, `MCPUIResource.tsx` |
| 9 | `UIResourceCarousel.tsx` | `autoResizeIframe` → `xyzDisabledProp` | 1 | 10 | same message, `UIResourceCarousel.tsx` |
| 10 | `ToolCallInfo.tsx` | `autoResizeIframe` → `xyzDisabledProp` | 1 | 10 | same message, `ToolCallInfo.tsx` |
| 11 | `node_modules/@mcp-ui/client/dist/index.mjs` | `ui-size-change` → `xyz-size-disabled` | 1 | 7 | `FAIL  shipped bytes no longer handle the ui-size-change message` |
| 12 | `MCPUIResource.tsx` | one axis only, `height: true` → `height: false` | 1 | 10 | same message, `MCPUIResource.tsx` |

Round 1 is the one that carries an argument rather than a confirmation. Its eight passing `ok`
lines include `shipped bytes still handle ui-size-change`, and the three render-site checks sit
later in the script and would have passed too. That is the blind spot between "LibreChat asks for
resize" and "the library honours the ask", and it is visible only because the accept-the-option
check now exists.

Every round reached exactly the assertion under test and no earlier one. For the render-site
rounds all ten preceding checks passed, including every dist-bytes check (`text/uri-list`, the
sandbox strings, `ui-size-change`, `autoResizeIframe`), since those read a different, unmutated
file; the two dist-bytes rounds were caught before the script reached the render-site checks,
matching the order the checks appear in it.

Rounds 2–7 and 12 are the variants the earlier bare-token pattern let through: value flipped to
`false` on both axes, on one axis, and the line commented out. Under the value-aware pattern all
seven fail. Rounds 8, 9 and 10 are whole-token renames, which the earlier pattern already caught;
they are re-run here because the pattern changed under them, and because two of the three had
been confirmed only by their own author. Round 11 re-runs the `ui-size-change` mutation against
the restructured script, confirming that comment restructuring around that check left the
predicate intact.

With no mutation applied, the same script — same argument form, real clone — exits 0 with all
twelve `ok` lines present, including the three described here.

## Reproduce

The shipped-bytes checks above (`text/uri-list`, the sandbox strings, `ui-size-change`) read
the clone's own `node_modules/@mcp-ui/client`, so this repo's `README.md` Prerequisites —
`npm ci && npm run frontend` in the clone — must already have run; `verify_librechat_pin.sh`
fails immediately and by name (`"@mcp-ui/client not installed (run npm ci first)"`) if it has
not, distinctly from any message that says a property is gone from bytes that ARE installed.

```bash
spikes/librechat-embed-render-gate/verify_librechat_pin.sh          # E1: rig is the pin
cd spikes/librechat-embed-render-gate && node browser_probe.mjs      # E2-E5: the live run
```

Evidence from the recorded run — `probe-results.json`, `uri-list-render.png`,
`html-control-render.png` — is written to `spikes/librechat-embed-render-gate/evidence/`.
