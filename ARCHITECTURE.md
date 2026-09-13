# NOA — architecture notes

## Scope of this file

Design choice that live longer than one task. Why choose. What break choice open again.

Start with dep pin for MCP stack, then topology, module wall, then rest of design note. File say WHY of choice, not repeat rule. `DECISIONS.md` hold number.

## MCP protocol era — a negotiated set, not a digit

NOA no pick protocol version. **Client** pick. NOA job: cover whatever client ask.

**Client pick. No talk client out of it.** TypeScript SDK `Client.connect` send `protocolVersion: LATEST_PROTOCOL_VERSION` — module constant, no constructor option, no override knob. Server answer version outside client list? Client throw `Server's protocol version is not supported`.

**LibreChat is that client, at exact version.** Only MCP client. At pinned commit its `package-lock.json` resolve `@modelcontextprotocol/sdk` to exactly 1.29.0 — not just `^1.29.0` range in `packages/api/package.json` — and `connection.ts` build stock `Client` with no protocol argument — client pick era, NOA serve set. That SDK `LATEST_PROTOCOL_VERSION` is `2025-11-25`, read at tag `v1.29.0`, byte-confirm against published npm tarball. So `2025-11-25` is era this deploy really negotiate.

**NOA serve set.** `mcp==1.29.0` answer `initialize` with version client ask, when version in `SUPPORTED_PROTOCOL_VERSIONS` — `2024-11-05`, `2025-03-26`, `2025-06-18`, `2025-11-25` — else fall back to own `LATEST_PROTOCOL_VERSION`, `2025-11-25`. Fallback no dead end: `2025-11-25` sit in TypeScript client list, so handshake still finish. Every era in set is handshake era, so `Mcp-Session-Id` always in play — session not optional here.

Real property is **set**, not one digit: servable set cover what v1.x client can ask. Digit in prose = claim about other tribe dependency. That claim go stale before (see *Re-open triggers*).

## `2026-07-28` is out of reach, and buys nothing

Sessionless `2026-07-28` era in neither SDK list at 1.29.0. Reach it = SDK bump both side, not config flip — that bound future path here.

Its special surface also missing: `Mcp-Method` / `Mcp-Name` header and `ttlMs` / `cacheScope` cache hint grep to zero hit in both SDK at 1.29.0. So staleness rule have nothing to bound today; stale-catalog risk held by execution-time RBAC re-check, not by cache-hint ceiling that no exist yet.

## A revoked grant stays visible in LibreChat until the connection is rebuilt

**This one face operator. Read as operational fact, not design note.**

Admin remove tool from role in NOA — operator chat client keep *showing* tool. Tool no vanish from model tool list. Tool no vanish on new conversation. Tool vanish when MCP connection rebuilt — LibreChat restart, or whatever that deploy do to reconnect MCP server.

**Call it no work. That is the guarantee.** Revocation authoritative moment it commit. NOA re-resolve permission from database on every `tools/call`, so tool shown but revoked answer `tool_not_permitted` and never run. Shown ≠ permitted. What operator see in stale catalog is label, not authorization.

**Why it stay visible.** NOA DO emit protocol `notifications/tools/list_changed` on permission change, to every connected session of every hit operator. LibreChat ignore it. Measured at pin `45cc53c4`, two way that agree:

- **On wire, NOA side work.** With session standalone GET stream open, emit put `{"method":"notifications/tools/list_changed","jsonrpc":"2.0"}` on that stream.
- **In LibreChat, nothing happen.** Same emit inside chat turn draw no request at all — turn JSON-RPC method read `initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call`, then stop. Source agree: `connection.ts:1852` register handler for `ResourceListChangedNotificationSchema` only, and `ToolListChangedNotificationSchema` have zero hit tree-wide.

Ignore it harmless — no error, no disconnect — so NOA keep emit: protocol-correct, cost near nothing, later client may honour it. But buy nothing today, and honest word on consequence is paragraph at top of this section, not "permission changes propagate immediately".

Also nothing to bound staleness with. Cache hint (`ttlMs`, `cacheScope`) come with `2026-07-28` era, out of reach at this pin — see section above — so ceiling on how long client may show revoked tool no exist to configure. Staleness rule say so plain, and lean on execution-time re-check instead.

**What to tell operator who report it.** Tool they see but no can call = not broken grant, not NOA fault. Their client catalog stale, their next call get refused, as should be. Need clean list — demo, audit walkthrough? Rebuild MCP connection.

Enforced by `apps/api/tests/test_mcp_tool_list_changed.py`. It assert refusal behind production write path and on purpose assert *nothing* about whether client refetch: that be claim about other tribe code, red on upstream whim — and upstream provenance no evidence control work. Inert host-key pin is shape that failure take.

## `fastmcp==3.4.5`, not 4.x

Server dep pinned exact. Bound deliberate both direction.

FastMCP 4.x beta-only — `4.0.0b1`, install only with `--pre`. It remove server-initiated sampling and roots, drop 3.x compat shim, and move `fastmcp.server.auth.*`, where `TokenVerifier` subclass live. So 4.x move break token verification — one thing standing between unauthenticated caller and every tool — in trade for nothing this deploy use.

3.4.5 declare `requires-python >=3.10`, which satisfy `>=3.11,<3.13` Python window (upper bound is `pgpy` 0.6.0 import removed stdlib `imghdr`).

## Re-open triggers

Three. Loudest first.

1. **LibreChat ship `@modelcontextprotocol/sdk` v2.** This open era question proper: v2 client first one that can ask for thing NOA never asked. Even then `2026-07-28` is explicit opt-in on client side, not automatic move.

2. **Bump *inside* 1.x move live era silent.** This the quiet one. No config change, no error, nothing in NOA fail — negotiated era just become different string, because client send its SDK constant and server echo it. Only thing between that and broken handshake: servable set stay wide enough. Practical rule: **never assert specific era anywhere but test.** Prose that name digit as *the* era = claim about dependency that can change without touching this repo. That is exactly how old `2025-06-18` claim survive in four file until someone read SDK source.

3. **Any LibreChat pin bump** re-read dep version and who pick era, and separately re-run render-path gate — renderer and protocol move on same bump, for different reason.

Source to re-read on any of three: `mcp/shared/version.py` and `mcp/types.py` for server set; `src/types.ts` and `src/client/index.ts` at SDK tag for what client send; LibreChat `package-lock.json` and `packages/api/src/mcp/connection.ts` for which SDK really resolve.

## Where these claims are enforced

Prose not the control. Test are:

- `apps/api/tests/test_pins.py` — pinned version, servable set, and two file above agree with installed SDK both direction (era added to set leave doc incomplete; era removed leave doc wrong).
- `apps/api/tests/test_mcp_mount.py` — negotiation against real mount, three case: era LibreChat client ask, older v1.x client era, and unsupported ask falling back to version client still accept.
- `apps/api/tests/test_mcp_tool_list_changed.py` — stale-catalog section above: who NOA notify on permission change, that emit reach real `ServerSession`, and that revoked tool refused at execution while client captured catalog still name it.

Harness in `apps/api/tests/support/mcp_mount.py` ask for what LibreChat ask, on purpose. Old version of these test only LOOK like proof of era, because harness itself ask for it — self-fulfilling assertion, same shape as inert host-key pin.