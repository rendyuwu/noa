# NOA — architecture notes

## Scope of this file

Design decisions that outlive any one task, with the evidence they rest on and the condition that
re-opens them.

It starts with the dependency pins for the MCP stack (§T.70). Topology, module boundaries, and the
remaining design notes land with §T.62. `SPEC.md` stays canonical for constraints and invariants —
this file explains a decision, it does not restate the spec. `DECISIONS.md` holds the measurements.

## MCP protocol era — a negotiated set, not a digit

NOA does not pick a protocol version. The **client** does, and NOA's job is to cover whatever a
client can ask for.

**The client chooses, and cannot be talked out of it.** The TypeScript SDK's `Client.connect` sends
`protocolVersion: LATEST_PROTOCOL_VERSION` — a module constant, with no constructor option and no
override knob. If the server answers with a version outside the client's own supported list, the
client throws `Server's protocol version is not supported` (§R.26).

**LibreChat is that client, at an exact version.** It is the sole MCP client (C24). At the pinned
commit its `package-lock.json` resolves `@modelcontextprotocol/sdk` to exactly 1.29.0 — not merely
the `^1.29.0` range declared in `packages/api/package.json` — and `connection.ts` constructs a stock
`Client` with no protocol arguments (§R.11, §R.26). That SDK's `LATEST_PROTOCOL_VERSION` is
`2025-11-25`, read at tag `v1.29.0` and byte-confirmed against the published npm tarball (§R.9).
So `2025-11-25` is the era this deployment actually negotiates.

**NOA serves a set.** `mcp==1.29.0` answers `initialize` with the version the client asked for when
that version is in `SUPPORTED_PROTOCOL_VERSIONS` — `2024-11-05`, `2025-03-26`, `2025-06-18`,
`2025-11-25` — and otherwise falls back to its own `LATEST_PROTOCOL_VERSION`, `2025-11-25` (§R.8).
That fallback is not a dead end: `2025-11-25` is in the TypeScript client's supported list, so the
handshake still completes (§R.26). Every era in that set is a handshake era, so `Mcp-Session-Id` is
in play throughout — sessions are not optional here.

The operative property is therefore the **set**, not one digit: the servable set covers what a v1.x
client can ask. A digit written into prose is a claim about someone else's dependency, which is
exactly the claim that went stale before (see *Re-open triggers*).

## `2026-07-28` is out of reach, and buys nothing

The sessionless `2026-07-28` era is in neither SDK's supported list at 1.29.0. Reaching it is an SDK
bump on both sides, not a configuration flip — which is what bounds the future path in V74.

Its distinguishing surface is absent too: `Mcp-Method` / `Mcp-Name` headers and the `ttlMs` /
`cacheScope` cache hints grep to zero hits in both SDKs at 1.29.0 (§R.26). That is why V74 has
nothing to bound today; the stale-catalog risk is held by the execution-time RBAC re-check (V1)
rather than by cache-hint ceilings that do not yet exist.

## `fastmcp==3.4.5`, not 4.x

The server dependency is pinned exact, and the bound is deliberate in both directions.

FastMCP 4.x is beta-only — `4.0.0b1`, installable only with `--pre`. It removes server-initiated
sampling and roots, drops the 3.x compatibility shims, and relocates `fastmcp.server.auth.*`, which
is where the §T.11 `TokenVerifier` subclass lives. So a 4.x move breaks token verification, the one
thing standing between an unauthenticated caller and every tool, in exchange for nothing that this
deployment uses.

3.4.5 declares `requires-python >=3.10`, which satisfies C1's `>=3.11,<3.13` window (the upper bound
is `pgpy` 0.6.0 importing the removed stdlib `imghdr`).

## Re-open triggers

Three, in descending order of how loudly they announce themselves.

1. **LibreChat ships `@modelcontextprotocol/sdk` v2.** This re-opens C23 properly: a v2 client is
   the first one that can ask for something NOA has not been asked for. Even then `2026-07-28` is an
   explicit opt-in on the client side, not an automatic move.

2. **A bump *inside* 1.x moves the live era silently.** This is the quiet one. No configuration
   changes, no error appears, nothing in NOA fails — the negotiated era simply becomes a different
   string, because the client sends its SDK's constant and the server echoes it. The only thing
   between that and a broken handshake is the servable set staying wide enough. Practical rule:
   **never assert a specific era anywhere but a test.** Prose that names a digit as *the* era is a
   claim about a dependency that can change without touching this repo, and that is precisely how
   the earlier `2025-06-18` claim survived in four files until it was read against the source
   (§T.71, §R.9).

3. **Any LibreChat pin bump** re-runs §R.11 and §R.26 (dependency versions and who chooses the era),
   and independently re-runs the §T.59 render-path gate per C21 — the renderer and the protocol move
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

The harness in `apps/api/tests/support/mcp_mount.py` asks for what LibreChat asks for, deliberately.
An earlier version of these tests appeared to prove the era only because the harness requested it —
a self-fulfilling assertion of the same shape as the inert host-key control in §B.2 (V69).
