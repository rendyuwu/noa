# NOA — architecture notes

## Scope of this file

Design decisions that outlive any one task, with the evidence they rest on and the condition that
re-opens them.

It starts with the dependency pins for the MCP stack, then topology, module boundaries, and the
remaining design notes. This file explains a decision, it does not restate a rule.
`DECISIONS.md` holds the measurements.

## MCP protocol era — a negotiated set, not a digit

NOA does not pick a protocol version. The **client** does, and NOA's job is to cover whatever a
client can ask for.

**The client chooses, and cannot be talked out of it.** The TypeScript SDK's `Client.connect` sends
`protocolVersion: LATEST_PROTOCOL_VERSION` — a module constant, with no constructor option and no
override knob. If the server answers with a version outside the client's own supported list, the
client throws `Server's protocol version is not supported`.

**LibreChat is that client, at an exact version.** It is the sole MCP client. At the pinned
commit its `package-lock.json` resolves `@modelcontextprotocol/sdk` to exactly 1.29.0 — not merely
the `^1.29.0` range declared in `packages/api/package.json` — and `connection.ts` constructs a stock
`Client` with no protocol arguments — the client picks the era, NOA serves a set. That SDK's `LATEST_PROTOCOL_VERSION` is
`2025-11-25`, read at tag `v1.29.0` and byte-confirmed against the published npm tarball.
So `2025-11-25` is the era this deployment actually negotiates.

**NOA serves a set.** `mcp==1.29.0` answers `initialize` with the version the client asked for when
that version is in `SUPPORTED_PROTOCOL_VERSIONS` — `2024-11-05`, `2025-03-26`, `2025-06-18`,
`2025-11-25` — and otherwise falls back to its own `LATEST_PROTOCOL_VERSION`, `2025-11-25`.
That fallback is not a dead end: `2025-11-25` is in the TypeScript client's supported list, so the
handshake still completes. Every era in that set is a handshake era, so `Mcp-Session-Id` is
in play throughout — sessions are not optional here.

The operative property is therefore the **set**, not one digit: the servable set covers what a v1.x
client can ask. A digit written into prose is a claim about someone else's dependency, which is
exactly the claim that went stale before (see *Re-open triggers*).

## `2026-07-28` is out of reach, and buys nothing

The sessionless `2026-07-28` era is in neither SDK's supported list at 1.29.0. Reaching it is an SDK
bump on both sides, not a configuration flip — which is what bounds the future path here.

Its distinguishing surface is absent too: `Mcp-Method` / `Mcp-Name` headers and the `ttlMs` /
`cacheScope` cache hints grep to zero hits in both SDKs at 1.29.0. That is why the staleness rule has
nothing to bound today; the stale-catalog risk is held by the execution-time RBAC re-check
rather than by cache-hint ceilings that do not yet exist.

## A revoked grant stays visible in LibreChat until the connection is rebuilt

**This one is operator-facing, so read it as an operational fact rather than a design note.**

When an admin removes a tool from a role in NOA, the operator's chat client keeps *showing* that
tool. It does not disappear from the model's list of available tools, and it does not disappear
when the operator starts a new conversation. It disappears when the MCP connection itself is
rebuilt — a LibreChat restart, or whatever that deployment does to reconnect its MCP servers.

**Calling it does not work, and that is the guarantee.** The revocation is authoritative the
moment it commits. NOA re-resolves permissions from the database on every `tools/call`, so a
tool that is displayed but revoked answers `tool_not_permitted` and never runs. Displayed is not
permitted; what an operator sees in a stale catalog is a label, not an authorization.

**Why it stays visible.** NOA does emit the protocol's `notifications/tools/list_changed` when a
permission changes, to every connected session of every affected operator. LibreChat
ignores it. That was measured at pin `45cc53c4`, two ways that agree:

- **On the wire, NOA's side works.** With a session's standalone GET stream open, the emit put
  `{"method":"notifications/tools/list_changed","jsonrpc":"2.0"}` on that stream.
- **In LibreChat, nothing happened.** The same emit inside a chat turn drew no request at all —
  the turn's JSON-RPC methods read `initialize`, `notifications/initialized`, `ping`,
  `tools/list`, `tools/call`, and stop. The source agrees: `connection.ts:1852` registers a
  handler for `ResourceListChangedNotificationSchema` only, and `ToolListChangedNotificationSchema`
  has zero hits tree-wide.

Ignoring it is harmless — no error, no disconnect — so NOA keeps emitting: it is protocol-correct,
costs almost nothing, and a later client may honour it. But it buys nothing today, and the honest
statement of the consequence is the paragraph at the top of this section rather than "permission
changes propagate immediately".

There is also nothing to bound the staleness with. Cache hints (`ttlMs`, `cacheScope`) arrive with
the `2026-07-28` era, which is out of reach at this pin — see the section above — so a ceiling on
how long a client may display a revoked tool is not available to configure. The staleness rule says
so explicitly, and leans on the execution-time re-check instead.

**What to tell an operator who reports it.** The tool they can see but not call is not a broken
grant and not a NOA fault; their client's catalog is stale and their next call will be refused, as
it should be. If a clean list matters — a demo, an audit walkthrough — rebuild the MCP connection.

Enforced by `apps/api/tests/test_mcp_tool_list_changed.py`, which asserts the refusal behind the
production write path and deliberately asserts *nothing* about whether the client refetched: that
would be a claim about someone else's code, red on an upstream whim — and upstream provenance is no
evidence a control works. The inert host-key pin is the shape that failure takes.

## `fastmcp==3.4.5`, not 4.x

The server dependency is pinned exact, and the bound is deliberate in both directions.

FastMCP 4.x is beta-only — `4.0.0b1`, installable only with `--pre`. It removes server-initiated
sampling and roots, drops the 3.x compatibility shims, and relocates `fastmcp.server.auth.*`, which
is where the `TokenVerifier` subclass lives. So a 4.x move breaks token verification, the one
thing standing between an unauthenticated caller and every tool, in exchange for nothing that this
deployment uses.

3.4.5 declares `requires-python >=3.10`, which satisfies the `>=3.11,<3.13` Python window (the upper bound
is `pgpy` 0.6.0 importing the removed stdlib `imghdr`).

## Re-open triggers

Three, in descending order of how loudly they announce themselves.

1. **LibreChat ships `@modelcontextprotocol/sdk` v2.** This re-opens the era question properly: a v2 client is
   the first one that can ask for something NOA has not been asked for. Even then `2026-07-28` is an
   explicit opt-in on the client side, not an automatic move.

2. **A bump *inside* 1.x moves the live era silently.** This is the quiet one. No configuration
   changes, no error appears, nothing in NOA fails — the negotiated era simply becomes a different
   string, because the client sends its SDK's constant and the server echoes it. The only thing
   between that and a broken handshake is the servable set staying wide enough. Practical rule:
   **never assert a specific era anywhere but a test.** Prose that names a digit as *the* era is a
   claim about a dependency that can change without touching this repo, and that is precisely how
   the earlier `2025-06-18` claim survived in four files until it was read against the source
   by reading the SDK source.

3. **Any LibreChat pin bump** re-reads the dependency versions and who chooses the era,
   and independently re-runs the render-path gate — the renderer and the protocol move
   on the same bump but for different reasons.

Sources to re-read on any of the three: `mcp/shared/version.py` and `mcp/types.py` for the server
set; `src/types.ts` and `src/client/index.ts` at the SDK tag for what the client sends; LibreChat's
`package-lock.json` and `packages/api/src/mcp/connection.ts` for which SDK is actually resolved.

## Where these claims are enforced

Prose is not the control. The tests are:

- `apps/api/tests/test_pins.py` — the pinned versions, the servable set, and the two files above
  agreeing with the installed SDK in both directions (an era added to the set leaves the docs
  incomplete; an era removed leaves them wrong).
- `apps/api/tests/test_mcp_mount.py` — negotiation against the real mount over three cases: the era
  LibreChat's client asks for, an older v1.x client's era, and an unsupported ask falling back to a
  version the client still accepts.
- `apps/api/tests/test_mcp_tool_list_changed.py` — the stale-catalog section above: who NOA notifies
  on a permission change, that the emit reaches a real `ServerSession`, and that a revoked tool is
  refused at execution while the client's captured catalog still names it.

The harness in `apps/api/tests/support/mcp_mount.py` asks for what LibreChat asks for, deliberately.
An earlier version of these tests appeared to prove the era only because the harness requested it —
a self-fulfilling assertion of the same shape as the inert host-key pin.
