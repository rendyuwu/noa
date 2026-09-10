# librechat-embed-render-gate

The rig that answered the render gate's live-run items. Verdict and captured numbers live in
[`docs/spikes/librechat-embed-render-gate.md`](../../docs/spikes/librechat-embed-render-gate.md);
this file is how to run it again.

It is committed because the render path is re-verified on **every** LibreChat bump. A prose description
of a rig is not a rig — the point of keeping the scripts is that the next re-verification is a
command rather than an afternoon.

## What it is

| File | Role |
|---|---|
| `verify_librechat_pin.sh` | E1: the three required config keys are still in the YAML and in `docs/integrations/librechat.md`, the clone is the pin, and the shipped `@mcp-ui/client` bytes still map `text/uri-list` → iframe `src` with `allow-same-origin` and without `allow-forms` |
| `noa_embed_probe_server.py` | NOA's real app plus a probe MCP mount and the NOA-origin frame page that measures whether the session cookie arrives |
| `mint_mcp_token.py` | mints the bearer token LibreChat authenticates with; no `/admin` or `/me` route exists yet |
| `browser_probe.mjs` | E2-E6: drives LibreChat in Chromium, reads the verdicts out of the frame, writes `evidence/` |
| `resize_probe.mjs` + `probe_pages/` | the standalone surface probe: iframe height, clipboard write, download — no LibreChat, no Postgres, no Mongo (see below) |
| `librechat.yaml` | the MCP server entry, including the three settings the sole MCP client forces (`startup: false`, `requiresOAuth: false`, and NOA's host in `mcpSettings.allowedDomains`) — each closes a silent failure, and the operator-facing version of it is [`docs/integrations/librechat.md`](../../docs/integrations/librechat.md) |
| `harness.env` | NOA settings for the run. Development values, no key material |

## Prerequisites

```bash
# Postgres (the repo's dev container) and a Mongo for LibreChat
docker compose up -d postgres
docker run -d --name noa-t59-mongo -p 27017:27017 mongo:7

# hostnames — same registrable domain is what makes the Lax cookie same-site in the frame
echo '127.0.0.1 noa.internal chat.noa.internal embed.noa.internal' | sudo tee -a /etc/hosts
# remove with: sudo sed -i '/noa.internal chat.noa.internal embed.noa.internal/d' /etc/hosts

# LibreChat at the pin
git clone https://github.com/danny-avila/LibreChat.git ~/noa/librechat-embed-render-gate
cd ~/noa/librechat-embed-render-gate
git checkout 45cc53c40b47645b887c3bb996168e06aaa83f4c
npm ci && npm run frontend        # the client bundle IS the render path under test
```

A model the agent can use. The recorded run used a local Anthropic-compatible proxy on
`127.0.0.1:42069` with `claude-sonnet-5` and any API key, wired through LibreChat's
`ANTHROPIC_REVERSE_PROXY`. Any endpoint LibreChat can drive tool calls with will do — the
model only has to call one tool.

LibreChat `.env` values that matter (the rest is stock):

```
HOST=0.0.0.0
PORT=3080
MONGO_URI=mongodb://127.0.0.1:27017/LibreChat
DOMAIN_CLIENT=http://chat.noa.internal:3080
DOMAIN_SERVER=http://chat.noa.internal:3080
CONFIG_PATH=<repo>/spikes/librechat-embed-render-gate/librechat.yaml
ALLOW_REGISTRATION=true
SEARCH=false
SESSION_COOKIE_SECURE=false          # plain-HTTP rig: LibreChat will not set its refresh cookie otherwise
ANTHROPIC_API_KEY=local-any
ANTHROPIC_REVERSE_PROXY=http://127.0.0.1:42069
ANTHROPIC_MODELS=claude-sonnet-5
NOA_MCP_TOKEN=<paste from mint_mcp_token.py>
```

## Run

```bash
# 1. schema + token
cd apps/api && set -a && . ../../spikes/librechat-embed-render-gate/harness.env && set +a
uv run alembic upgrade head && cd ../..
uv run python spikes/librechat-embed-render-gate/mint_mcp_token.py operator@noa.internal dev-bypass
#    ^ prints the plaintext once. Paste it into LibreChat's .env as NOA_MCP_TOKEN.
#    Mint a FRESH one per run: the first MCP call binds it to a LibreChat user id (TOFU),
#    and a token bound to a previous run's user is a 401.

# 2. NOA (real /auth and /mcp, plus the probe mount)
uv run python spikes/librechat-embed-render-gate/noa_embed_probe_server.py

# 3. LibreChat
cd ~/noa/librechat-embed-render-gate && npm run backend

# 4. the run
spikes/librechat-embed-render-gate/verify_librechat_pin.sh
cd spikes/librechat-embed-render-gate
ln -sfn ~/noa/librechat-embed-render-gate/node_modules node_modules   # or npm install here
node browser_probe.mjs
```

`browser_probe.mjs` exits non-zero on any failed check and writes `evidence/` either way. On a
green run it prints `all checks green`.

## Reading a failure

- **`iframe[sandbox]` never appears** — the model never called the tool, or LibreChat never
  connected. Check `.runtime/jsonrpc.log`: no `tools/list` means the connection never
  authenticated (a stale bound token, or `startup:`/`requiresOAuth:` missing from the config).
- **`sandbox` gained `allow-forms`** — the JS-`fetch`-only rule loses its premise.
  `verify_librechat_pin.sh` fails first and says so; re-measure every render site before touching
  the rule.
- **the frame's `/auth/me` is 401 while the control is also 401** — the discriminator is dead,
  so the run proves nothing. Check the cookie domain and that NOA and the frame share a
  registrable domain.
- **`allowedDomains` errors at LibreChat boot** — `mcpSettings.allowedAddresses` will not take
  CIDRs or loopback literals at this pin; use the domain list.

`.runtime/` (logs, the minted token, the JSON-RPC method log) and `evidence/` screenshots are
scratch; `evidence/probe-results.json` from the recorded run is kept as the artifact behind the
report.

## Standalone surface probe

`resize_probe.mjs` answers four questions about a NOA page rendered inside an mcp-ui iframe:

1. **Height.** `@mcp-ui/client` writes `height: 100%` inline and then overwrites it from a
   `ui-size-change` message. What is the height before any message, and what does the host do with
   a number the framed document picks?
2. **Clipboard.** Does `navigator.clipboard.write([new ClipboardItem({'image/png': blob})])`
   succeed from inside the frame, at the sandbox strings this pin emits and with no `allow`
   attribute — which is what LibreChat renders?
3. **Download.** Does a blob-URL anchor with `download` actually download from inside it?
4. **Everything else that gets bytes out.** `extraction_probe.mjs`, one stage alongside the other
   three: the legacy `document.execCommand('copy')` over a script selection, `clipboard.writeText`,
   a copy of a selection containing an `<img>` and which clipboard FLAVOURS arrive, the zero-code
   path (the operator drag-selects with the mouse and presses Ctrl+C), an `<img>` dragged onto the
   host document, and `target="_blank"` at both sandbox strings plus what the opened tab inherited.

It is separate from `browser_probe.mjs` on purpose. The full rig needs Postgres, Mongo, a built
LibreChat and a model endpoint; these answers come from the shipped `@mcp-ui/client` bundle
plus Chromium's own frame policy, so running four services to ask them would make the answers
hostage to services that have nothing to do with the question. It mounts the **real**
`UIResourceRenderer` with the props LibreChat passes — so the sandbox strings it reports are
measured, not assumed — and then re-uses those measured strings on hand-built frames to ask the
clipboard and download questions. The same three checks are wired into `browser_probe.mjs` as E6
so the next full re-verification records them against the live app.

The fourth stage (`extraction_probe.mjs`, recorded as `stages.extraction` and
`stages.extraction-verdicts`) exists because measurements 2 and 3 answer about two specific APIs
and not about the question underneath them — whether a document in the frame can hand bytes to the
operator with nothing changed on the host side. It asks the six remaining ways, at the same
measured sandbox strings, and it never reads an answer off a return value: `execCommand('copy')`
returns `true` in Chromium whenever the editing command dispatched, which is not the claim "the
clipboard changed". So every clipboard step **seeds** the clipboard with a sentinel, verifies the
sentinel is what is there, acts, and then **reads the clipboard back from a permitted context** —
two readers where both exist, a top-level page at the host origin holding `clipboard-read` and
`xclip` talking to the X server outside the browser entirely, with the reader that answered
recorded beside the answer. The `clipboard-read` grant goes to the reader's origin only, never to
the framed document's, so it cannot be mistaken for the thing that let a frame write. Its own
controls: an unframed page runs the identical steps (where it fails too, the posture is named
unable to answer); `writeText` being refused in the same frame where `execCommand` succeeds is what
shows the reader is not simply reporting "changed" every time; the missing `image/png` flavour is
paired with an unframed `clipboard.write` that puts one there, so the absence is a measurement and
not a blind reader; the drag-out claim is paired with a drop target inside the frame and with a
same-origin frame carrying the same sandbox string; and the popout claim is paired with the string
that lacks `allow-popups` (0 tabs, and the click is shown to have reached the anchor, so the
refusal is silent) and with an unsandboxed frame whose popup must be able to download.

Two ordering notes that are load-bearing rather than incidental. The drag runs **last**: a
cross-origin drag the harness cannot complete leaves Chromium believing one is still in flight and
swallows the next synthesised click, which is why the first version of this stage reported popouts
that never opened at a sandbox string granting `allow-popups`. The stage measures that
(`pageStillTakesClicksAfterDrag`) and refuses to convert a zero host-side drop count into a verdict
where it is false — a cross-origin iframe is also a separate renderer process, so the zero cannot
be told apart from a synthesis that never crossed the boundary, and it is filed as an open question
instead. This is the one place the two run modes disagree and both are worth reading:
`--headed` records the subject page as no longer taking clicks after its own cross-origin drag and
therefore reports `crossOriginAttribution: NOT ANSWERED`, while headless records the same zero
with the page still responsive and therefore attributes it. Everything else in the stage matches
across the two modes and across both permission postures. And every in-page clipboard call is
raced against an in-**page** timer: `page.evaluate` has no timeout, and an awaited clipboard
promise that never settles hangs the run with nothing to read afterwards.

```bash
cd spikes/librechat-embed-render-gate
npm install                       # playwright, esbuild, react, react-dom, @mcp-ui/client
npx playwright install chromium   # only if Chromium is missing

node resize_probe.mjs             # headless; writes evidence/resize-clipboard-download-probe.json
xvfb-run -a node resize_probe.mjs --headed   # writes ...-probe-headed.json
```

Flags: `--headed` (also selects the `-headed` artifact name), `--keep-open` (leaves the browser
and the static server up for poking at). `PROBE_PORT` overrides the port, default `4599`.

No external services. It builds `probe_pages/harness_entry.js` into `.runtime/probe_bundle.js`
with esbuild, serves `probe_pages/` on one listener, and reaches it under two hostnames so the
frame is cross-origin to the host the way it is in the live rig: `http://localhost:<port>` is the
host document, `http://127.0.0.1:<port>` is the framed document. Both are potentially-trustworthy
origins, so a refusal is attributable to the frame policy rather than to the scheme —
`isSecureContext` is recorded per frame to keep that honest. Exits non-zero on any failed check
and writes the artifact either way.

**Run headed if you care about the clipboard answer.** A headless Chromium refuses
`clipboard-write` even to a top-level page, which makes the unframed control fail alongside the
framed subject and leaves the comparison unable to attribute anything. The probe detects this: it
marks that permission posture `clipboardDiscriminates: false` and names it under
`surface-verdicts.clipboard.unableToAnswerIn` rather than reporting agreement. It runs the whole
matrix twice, once with nothing granted and once with
`context.grantPermissions(['clipboard-read','clipboard-write'])`, and records both, because
Playwright's ability to pre-grant is exactly what would turn "a real operator's browser refuses
this" into "the harness asked nicely".

Reading a failure:

- **`esbuild produced no warnings` fails, or the build throws** — `@mcp-ui/client`, `react`,
  `react-dom` or `esbuild` is not resolvable from this directory. `node_modules` here is normally
  a symlink; `npm install` in this directory also works.
- **`the initial height and the resized height differ` fails** — initial and resized heights came
  out equal, so the resize measurement has no discriminating power and nothing it reports counts.
- **`control: the host DID receive the frame message` fails** — the resize-disabled control's
  unchanged height proves nothing, because the message never arrived. Check the frame loaded.
- **`control: ui-size-change from the host's own window does NOT move …` fails** — the package
  stopped checking that the message came from its own iframe, so any script on the host page can
  resize the frame and the positive measurement no longer means what it says.
- **`at least one clipboard posture discriminates` fails** — every unframed control failed too;
  re-run headed.
- **`extraction: at least one posture discriminates` fails** — no reader could verify a seeded
  sentinel, or the unframed page could not displace one. Run headed (`xclip` needs a display and is
  the reader that answers in the no-grant posture) and read
  `stages.extraction.<posture>.readersMeasured`.
- **`extraction: at least one posture synthesised a real drag` fails** — the in-frame control drop
  never landed, so no drag happened and the host-side count means nothing either way.
- **`control — a tab opened from an UNSANDBOXED cross-origin frame CAN download` fails** — the
  inheritance measurement lost its negative control, so "the opened tab cannot download" no longer
  separates inheritance from Chromium's own rules for popups.
- **the sandbox-string checks fail** — this pin's `@mcp-ui/client` emits something other than
  `allow-scripts allow-same-origin` / `allow-popups allow-scripts allow-same-origin`. That is a
  premise change, not a probe failure: `verify_librechat_pin.sh` fails first and says so.
