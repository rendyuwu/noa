# librechat-embed-render-gate

The rig that answered `§T.59` items (d) and (f). Verdict and captured numbers live in
[`docs/spikes/librechat-embed-render-gate.md`](../../docs/spikes/librechat-embed-render-gate.md);
this file is how to run it again.

It is committed because `C21` says re-verify on **every** LibreChat bump. A prose description
of a rig is not a rig — the point of keeping the scripts is that the next re-verification is a
command rather than an afternoon.

## What it is

| File | Role |
|---|---|
| `verify_librechat_pin.sh` | E1: the clone is the pin, and the shipped `@mcp-ui/client` bytes still map `text/uri-list` → iframe `src` with `allow-same-origin` and without `allow-forms` |
| `noa_embed_probe_server.py` | NOA's real app plus a probe MCP mount and the NOA-origin frame page that measures whether the session cookie arrives |
| `mint_mcp_token.py` | mints the bearer token LibreChat authenticates with (`T10`, `V2`); no `/admin` or `/me` route exists yet |
| `browser_probe.mjs` | E2-E5: drives LibreChat in Chromium, reads the verdicts out of the frame, writes `evidence/` |
| `librechat.yaml` | the MCP server entry, including the two settings `C24` forces (`startup: false`, `requiresOAuth: false`) |
| `harness.env` | NOA settings for the run. Development values, no key material (`C11`) |

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
#    ^ prints the plaintext once (V2). Paste it into LibreChat's .env as NOA_MCP_TOKEN.
#    Mint a FRESH one per run: the first MCP call binds it to a LibreChat user id (V3 TOFU),
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
- **`sandbox` gained `allow-forms`** — `V80`'s premise changed. `verify_librechat_pin.sh` fails
  first and says so; re-read `R13` before touching the invariant.
- **the frame's `/auth/me` is 401 while the control is also 401** — the discriminator is dead,
  so the run proves nothing. Check the cookie domain and that NOA and the frame share a
  registrable domain.
- **`allowedDomains` errors at LibreChat boot** — `mcpSettings.allowedAddresses` will not take
  CIDRs or loopback literals at this pin; use the domain list.

`.runtime/` (logs, the minted token, the JSON-RPC method log) and `evidence/` screenshots are
scratch; `evidence/probe-results.json` from the recorded run is kept as the artifact behind the
report.
