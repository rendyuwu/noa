# LibreChat Integration Reference

Canonical reference for the operator-side configuration of NOA's **only** MCP client.
LibreChat provides transport and rendering; every authorization decision stays in NOA.

NOA does not own, generate or deploy the file this page describes. It is the operator's
`librechat.yaml`, on the operator's host, editable after we hand it over — so this page is a
**reference, not a control**. Two things make that gap smaller: every key below ships with the
failure it prevents (a key whose reason is unwritten gets deleted at the next edit), and the keys
themselves are asserted by a test rather than by discipline — see
[Where this page is bound](#where-this-page-is-bound).

Working config, standing rig and the measurements behind everything here:
`spikes/librechat-embed-render-gate/` and [`../spikes/librechat-embed-render-gate.md`](../spikes/librechat-embed-render-gate.md).

Update this file in the same change as any feature that touches the MCP surface, and re-verify it
on every LibreChat bump.

## The server entry

```yaml
version: 1.3.0

# Without an explicit allowlist LibreChat turns on SSRF protection and refuses an MCP URL that
# resolves into private address space — which every internal host does.
mcpSettings:
  allowedDomains:
    - noa-api.simondayce.my.id

mcpServers:
  noa:
    type: streamable-http
    url: https://noa-api.simondayce.my.id/mcp/
    timeout: 60000
    # Measured, not preference. Both stop a silent, total failure — see below.
    startup: false
    requiresOAuth: false
    customUserVars:
      NOA_MCP_TOKEN:
        title: NOA MCP token
        description: >-
          Your personal NOA token. Mint it in the NOA admin panel under your own account, or ask
          an administrator to mint one for you. It is shown once.
        sensitive: true
    headers:
      Authorization: "Bearer {{NOA_MCP_TOKEN}}"
      X-Noa-LibreChat-User: "{{LIBRECHAT_USER_ID}}"
      X-Noa-Conversation-Ref: "{{LIBRECHAT_BODY_CONVERSATIONID}}"
```

### `url` — the host is a deployment property, the trailing slash is not

The **host** above is one deployment's, not a fact about NOA. Deployed, NOA's API
answers at `noa-api.simondayce.my.id` (staging: `noa-api-staging.simondayce.my.id`); the local
development stack and the render-gate rig both run at `noa.internal`. Substitute your own and keep
the two occurrences in step — `url` and `mcpSettings.allowedDomains` name the same host, and
`apps/api/tests/test_librechat_config_doc.py` derives the required allowlist entry from `url`'s own
host precisely so that an allowlist naming a *different* NOA host cannot read as a covering one.

One host, not four: LibreChat talks to the API alone. `noa-admin.simondayce.my.id` and
`noa-embed.simondayce.my.id` are the browser's, and `chat.simondayce.my.id` is LibreChat's own —
which has to sit under the same registrable parent as the embed or the `noa_session` cookie never
rides into the approval iframe.

The **trailing slash** is not a deployment choice. NOA mounts the MCP sub-app at `/mcp` and the app
answers at `/mcp/`
(`MCP_MOUNT_PATH`/`MCP_APP_PATH`, `apps/api/src/noa_api/mcp_server.py`). The bare path is
not broken: Starlette answers it with a 307, which preserves method and body, so a client that
follows redirects works. It just pays a redirect on **every** call. Write the slash.

Pinned by `apps/api/tests/test_mcp_mount.py::test_the_endpoint_is_mounted_at_mcp`, which asserts
the 307 and that following it reaches the authenticated endpoint rather than a 404 — so this
paragraph cannot quietly become false.

### `startup: false` and `requiresOAuth: false` — the zero-tools failure

Measured 2026-08-08 while standing the render-gate rig up. Not preferences; each closes a
**total, silent** failure:

LibreChat inspects every startup-enabled MCP server at boot, with no user in context. So
`{{LIBRECHAT_USER_ID}}` resolves to nothing, and NOA answers 401 — correctly, because LibreChat
being the sole client makes the header mandatory on every request, and an absent header is a 401
in every token state.
LibreChat reads that 401 as *"this server wants OAuth"*, sets `requiresOAuth: true`, marks the
connection `needs_authorization`, and never connects again. `/api/mcp/tools` then reports **zero
tools for every operator**, with a valid token, and no error anywhere an operator can see: NOA is
up, LibreChat is up, the tool list is empty.

- `startup: false` defers the connection to the first per-user call, where the headers do resolve.
- `requiresOAuth: false` stops the inference outright.

This is why the config counts as part of the **auth design** rather than deployment garnish:
LibreChat being the sole client makes an unauthenticated inspection impossible by construction, so
the entry has to tell LibreChat not to attempt one.

### `mcpSettings.allowedDomains` — the SSRF guard

Without it, LibreChat enables SSRF protection and refuses the URL before any request is made. The
refusal as measured on the rig, whose host is `noa.internal` rather than the deployed one above:
`Domain "http://noa.internal:8000" is not allowed`. At the pinned commit `allowedAddresses` accepts
neither a CIDR nor a loopback literal (its schema is host:port pairs in private space), so the
domain list is the only lever. Naming an internal MCP host's domain is what a real
deployment would do anyway.

### `customUserVars.NOA_MCP_TOKEN` — one token per operator

MCP auth is a per-user NOA-minted bearer token, never OAuth. Each operator enters their
own value in LibreChat's UI; `{{NOA_MCP_TOKEN}}` in the `headers` block is substituted per call
from that per-user store (`packages/api/src/utils/env.ts`, `processSingleValue`).

At the pinned commit the schema requires `title` and `description`, and `sensitive` defaults to
masked (`packages/data-provider/src/mcp.ts`). We state `sensitive: true` explicitly regardless: a
default that flips upstream would unmask a credential in the UI, and the explicit key is one word.

Where the token comes from: an admin mints one **for** an operator
(`POST /admin/users/{user_id}/tokens`) or an operator mints their own (`POST /me/mcp-tokens`, id
taken from the session, not the request). Either way the plaintext is shown once. Self-minting is
not escalation — the token carries no scope of its own, and every permission resolves off
`users.id` per call, so an operator with no roles mints a credential that can call nothing.

Where an operator does it: the NOA admin panel, **My MCP tokens** (`/me/tokens`) — the
sidebar entry every verified operator sees, admin or not. An admin managing someone else's tokens
uses the Users table's **MCP tokens** row action (`/admin/users/{userId}/tokens`); it is the same
panel against the admin routes. Mint, give the token a label naming where it will live, then copy
the value straight into this `NOA_MCP_TOKEN` field. **The plaintext is shown once and is not
recoverable**: closing the dialog discards it, no screen shows it again, and no route can
return it. Lose it and the fix is to mint another and revoke the old one — which is exactly what
the row's **Revoke** action does.

First use **binds** the token to one LibreChat account (TOFU): the token row starts with
`librechat_user_id` NULL, the first call carrying `X-Noa-LibreChat-User` binds it, and every later
call must present the same value. One token, one operator, one LibreChat account.

### The three headers

| Header | Required | What it does |
|---|---|---|
| `Authorization: Bearer {{NOA_MCP_TOKEN}}` | yes | Identifies the operator. Blank or absent → 401 `mcp_token_missing`. |
| `X-Noa-LibreChat-User: {{LIBRECHAT_USER_ID}}` | **yes, every request** | TOFU binding. Absent → 401 `librechat_user_header_missing` in *every* token state, bound or not. Present but not the bound value → 401 `librechat_user_mismatch`. |
| `X-Noa-Conversation-Ref: {{LIBRECHAT_BODY_CONVERSATIONID}}` | no | Groups audit rows by conversation. |

`X-Noa-LibreChat-User` is mandatory because LibreChat is the only client: there is no
unbound-client path to keep open, and the two distinct 401 codes exist so a misconfigured client is
distinguishable from a stolen-and-rebound token.

`X-Noa-Conversation-Ref` is optional and is **the only way `tool_runs.conversation_ref` is ever
non-NULL**. LibreChat sends no conversation identifier on a tool call — at the pinned commit the
single `tools/call` emitter sends `params: {name, arguments}` and nothing else, with no `_meta` on
the wire — but its header templating does resolve `{{LIBRECHAT_BODY_CONVERSATIONID}}` per
call from the request body. So:

- Header unset means the column is NULL for every run, and the admin audit filter `conversationRef`
  has nothing to group on. Nothing else breaks.
- Blank is indistinguishable from absent — LibreChat substitutes an empty string when the body
  field is missing — and NOA treats both as "no label".
- Over 255 characters, or carrying anything off the label charset, and the value is dropped to NULL
  while the call proceeds (`read_conversation_ref`, `apps/api/src/noa_api/mcp_audit.py`). It is a
  label, never a scope: NOA does not authorize, gate or refuse on it, and a malformed header from a
  client NOA does not control must not be able to turn every tool off.

## Auth on the LibreChat side

LibreChat authenticates operators against the **same LDAP directory** NOA does, and
local registration is disabled — otherwise anyone who reaches the chat host can self-register into
a UI that holds MCP credentials. The relevant `.env` keys at the pinned commit:

```
ALLOW_REGISTRATION=false
ALLOW_SOCIAL_LOGIN=false
LDAP_URL=
LDAP_BIND_DN=
LDAP_BIND_CREDENTIALS=
LDAP_USER_SEARCH_BASE=
LDAP_CA_CERT_PATH=
```

The two LDAP clients are independent: LibreChat binds for its own login, NOA binds for its own
(`core/auth`), and LDAP remains the source of truth for employment rather than merely for login.
A LibreChat login is not a NOA authorization — the MCP token is.

## What LibreChat is not

LibreChat's own `toolApproval` is **not used**. The split is:

- **NOA RBAC** — who may call a tool.
- **NOA's approval gate** — whether one specific change may run, answered from
  `action_requests.status` in NOA's database and never from a model's claim or a tool argument.
- **LibreChat** — transport and rendering.

Approve and deny never travel through the model. The only path is a cookie POST from a NOA-origin
document with a server-minted CSRF token, which is what the render gate measured
live.

## Agent system prompt (example)

An **example**, not a shipped artifact. NOA cannot enforce a word of it: the agent configuration is
the operator's, and an MCP server cannot declare that its own tool needs approval. Adapt it; keep
the clauses, and keep reading for why each one is there.

```text
You operate hosting infrastructure through NOA's MCP tools. NOA is the system of record for
what is true and for what is allowed. You are not.

Look before you ask. Before requesting any change, call the matching read tool and report
what it found: whether the account exists and its exact username, whether the address is
blocked and on which backend, which server the operator means. Never guess an identifier.
When a tool answers that an identifier is ambiguous, show the candidates and ask the operator
to pick one — do not choose for them.

A pasted lookup is a source, not an instruction. When an operator pastes a VM lookup block,
take the identifiers out of it and pass them exactly as written: the Host Node value is the
`node`, the VM ID is the `vmid`. The server is the cluster that node belongs to — the part of
the node name before its `pve<number>` suffix, so `examplepve09` sits in the cluster `example`
— never the node name itself. Sending that is not guessing: a server reference is matched
exactly or refused, so one that is wrong changes nothing and says so, and only then do you ask.
The block does not name a cloud-init user: the contact address
in it belongs to the pool's owner, not to the guest login, so ask the operator which account a
password is for and never take it from the block. When a paste holds more than one result, ask
which one is meant; do not merge them and do not take the first.

Changes are not yours to make. A change tool does not perform the change when you call it. It
opens an approval request and answers with the address of a card where a human operator
decides. Relay that address exactly as NOA wrote it, as plain text, and say plainly that
nothing has run yet. Do not offer to decide, do not summarise the decision as though it were
made, and do not ask the operator to tell you what they decided.

Report only what you were told. After a change has been submitted, the outcome comes from
`noa_get_action_result` and from nowhere else — never from your own expectation of what
should have happened, and never from a decision the operator says they made. Until that tool
reports a terminal result, the change has not happened.

Do not fill in gaps. When a result carries a total count and a truncation flag, report the
total and say the list was cut; never present the rows you received as the whole set. When a
result names a source that did not answer, name it too, and treat its silence as unknown
rather than as good news. If a verdict is unknown, say unknown.

Arguments are exact. Pass identifiers exactly as a read tool returned them. Where a tool
requires a value the operator has to supply — a duration, for instance — ask for it and
convert their own words into the unit the tool documents. Do not invent a default.

When a tool result mentions a UI Resource Marker, emit that marker verbatim in your reply so
the operator's card renders in the conversation.
```

### Why each clause is there

**Look before you ask** — one workflow is one tool. The preflight inside a CHANGE call
is an internal function, and its evidence is born in-process, lives milliseconds and never crosses
the tool boundary. What the model is expected to do is the *discovery* read: `whm_search_accounts`
for an exact username, `whm_list_servers` for a server, `whm_preflight_firewall_entries` for the
firewall verdict and its evidence lines. Ambiguity gets structured candidates, never a guess
— and the tool descriptions already say each of these, so the prompt reinforces rather
than replaces them.

**A pasted lookup is a source** — and for Proxmox it is the *only* source. The clause above exists
because the "look before you ask" discovery read has no Proxmox path: `core/auth/tool_catalog.py`
exposes `whm_list_servers` and nothing equivalent for Proxmox, so the two Proxmox tools are reached
with no way to list what they could be reached against. The operator's pasted lookup block is
therefore the discovery step, and the mapping out of it is not obvious: `Host Node` is the `node`,
and the *server* is the cluster that node is a member of. Sending the node name as `server_ref`
matches no id, no name and no `base_url` host, so `resolve_server_ref` answers `host_not_found`
carrying no `choices` — a miss is not a tie — and the model has nothing left to try. Hence "ask the
operator" rather than another spelling.

The shipped `server_ref` description says the cluster-and-node half itself
(`core/servers/proxmox_ref.py`, `SERVER_REF_DESCRIPTION`), so a model reaches the tool holding it
whatever prompt it was given. What it does not say is *how* to get from `examplepve09` to
`example`: node naming is a property of one fleet, not of Proxmox, and this prompt is the
operator's own file — so the derivation is stated here, where the operator who owns the convention
also owns the text. A fleet that renames its nodes invalidates this paragraph and leaves the
shipped description true.

**"Not guessing" is load-bearing, and it was learned the hard way.** The first live run reached the
NIC tool with a node name and no clause like the one above. The model followed the shipped
description as far as "the server is the cluster that node is part of", looked for a tool that maps
one to the other, found none — correctly, there is none — and then weighed deriving the cluster
against the standing "never guess an identifier" rule in this prompt. It classed its own correct
derivation as a guess, refused it, and went hunting for a server registry, arriving at
`whm_list_servers`: the wrong system, and a call that would have answered confidently with a list
containing no Proxmox row at all. Both texts therefore carry the fact that makes the derivation
legitimate — a server reference is matched exactly or refused, so a wrong one changes nothing and
names itself — and `whm_list_servers`' own description now says it lists WHM servers only and that
nothing lists the others. "Never guess" is about identifiers NOA cannot check; this is one it
checks, and the two rules have to be told apart in the text or the model tells them apart itself.

The cloud-init sentence is there because the block looks like it answers the `username` parameter
and does not. `Email:` is the pool owner's contact address; `username` is the guest's cloud-init
user, and `proxmox_reset_vm_password` refuses outright when the VM's `ciuser` is somebody else
(`proxmox.md`, `proxmox_reset_vm_password`). A model that reads the block as complete produces a
card naming an account the VM does not have — refused, but only after an operator has read it.

**Changes are not yours to make.** Every CHANGE result is shaped by
one function (`build_change_gate_response`) and carries two blocks: the plain approval address in
the text, and the UI resource that renders it as a card. The address is *text, not markup*, on
purpose: a `target="_blank"` clicked inside the frame opens nothing at all under one of LibreChat's
two render sandboxes, silently, and a tab it opens elsewhere inherits the frame's flags. An
address a human can copy is the one door upstream cannot withhold — so the model must
relay it rather than rewrite it as a link.

**Report only what you were told** — the same rule, from the model's side. "May this run?" is answered
by a row in NOA's database, never by a claim; the read side of that is `noa_get_action_result`,
which enforces requester-match against the token identity, so a model cannot pull another
operator's action into the transcript and an unknown id and a foreign id answer identically.
The change-gate text says the same sentence to the model already; the prompt is where
"never report it as done" becomes a standing rule instead of a per-tool one.

**Do not fill in gaps** — the *model* is what reports a bounded
answer. A capped read ships its own total and truncation flag, and a model that reports the page as
the whole set authors a fabrication the tool handed it ("there are twenty accounts" on a server
with two hundred). A read across several backends names the ones that did not answer, and silence
is not evidence of absence — a broken CSF beside a clean Imunify must not become "this address is
not blocked".

**Arguments are exact** — the same discipline the schemas enforce, stated once. `duration_minutes`
on `whm_firewall_release_and_allow` deliberately has no default: how long an address stays allowed
is the operator's call, and a model-invented window would be a policy decision nobody made.

**Emit the marker** — measured, not folklore. LibreChat's parser requires the `ui://`
scheme, hashes the resource body into a `resourceId`, and injects `UI Resource ID: <id>` and
`UI Resource Marker: \ui{<id>}` into the text the model sees. Emitting the marker renders the card
inline at the `MCPUIResource` site; not emitting it leaves the card in the tool-call panel, which
still works. It costs a token to say and it is the surface with `allow-popups`.

### What the prompt does not say, and why

Nothing about writing a justification for a change. That word enters the system exactly once, typed
by an operator on the approval card at decision time, and it is required to approve *and* to deny.
No CHANGE tool schema carries such a parameter under any name, and NOA's own
model-facing text carries none either — the model-facing safety text was widened at the change
gate to bind the text NOA emits, and
`apps/api/tests/test_mcp_change_gate_response.py` asserts the absence there. A model told the field
exists is a model that can be argued into filling it, so the prompt above never mentions it, and
the test below keeps it that way.

## Where this page is bound

`apps/api/tests/test_librechat_config_doc.py` reads **this file** and the rig's working config
(`spikes/librechat-embed-render-gate/librechat.yaml`) and asserts the same predicate against both:
the three config keys, the three headers with their exact placeholders, and the trailing slash on the
MCP URL. It also extracts the example prompt above and asserts it names no justification field.
Negative controls in the same test mutate each of those and require the predicate to go red,
because a config assertion that silently stops separating is worse than none.

That makes prose here and the config the live run measured one truth rather than two. What
it cannot do is reach the operator's deployed file, which is why this page
exists at all.

`spikes/librechat-embed-render-gate/verify_librechat_pin.sh` runs that test first, then re-asserts
the pin and the shipped renderer bytes. The re-verify duty makes it a command rather than an afternoon: run it on
**every** LibreChat bump, then `browser_probe.mjs` for the live half.

## Code references

- MCP mount and server name: `apps/api/src/noa_api/mcp_server.py`
- Token verification, TOFU binding, the 401 codes: `apps/api/src/noa_api/mcp_auth.py`,
  `apps/api/src/noa_api/mcp_request_auth.py`, `core/auth/mcp_auth_errors.py`
- `X-Noa-Conversation-Ref` read and sanitization: `apps/api/src/noa_api/mcp_audit.py`
- CHANGE gate result (text block + UI resource): `apps/api/src/noa_api/mcp_tools/change_gate.py`,
  `apps/api/src/noa_api/mcp_tools/ui_resource.py`
- Approval outcome read: `apps/api/src/noa_api/mcp_tools/noa_read.py`
- Tests: `apps/api/tests/test_librechat_config_doc.py`, `test_mcp_mount.py`,
  `test_mcp_request_auth.py`, `test_mcp_tool_audit.py`, `test_mcp_change_gate_response.py`
- Rig, working YAML, re-verify script: `spikes/librechat-embed-render-gate/`
- Measurements: [`../spikes/librechat-embed-render-gate.md`](../spikes/librechat-embed-render-gate.md)
