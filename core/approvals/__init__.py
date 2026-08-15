"""The approval gate's persistence layer (T33-T39 — V22, V23, V28, V30, V32, V33, V39).

`action_requests` is not a cache of a decision held somewhere else: the row **is** the
authorization (T34). V23 says "may this run?" is answered from `action_requests.status`
every time, never from an LLM claim and never from a tool argument, and that only means
anything if exactly one layer writes and reads that column. This package is that layer.

Thirteen modules, and the split that matters is **who can reach which writer** — three writers
of a request's status, one writer of what an approved change *did*, two readers over one shared
row guard, and the rest holding no statement at all:

- `repository` — opens a request. `SQLActionRequestRepository` writes `PENDING` and can write
  nothing else, because its caller is the MCP tool path (T33) — the path an LLM can reach.
  It owns its session and commits, unlike the `core.servers` repositories that flush into a
  caller's transaction, for the reason `core.audit.tool_runs` (T73) gives: that path runs
  outside FastAPI's dependency graph, so there is no request transaction to join.
- `decisions` — answers one. `SQLActionDecisionRepository` is the only thing that writes a
  terminal status, under `SELECT … FOR UPDATE` (V28), and it is reached only from T37's
  cookie POST (V22). Deliberately a separate class from the one above: folding them together
  would put a writer that can set `APPROVED` on the side of the boundary V22 exists to close.
  It joins the request's session, because there *is* one here. V31's per-user cap lives here
  too (T38), counted under a per-user advisory lock in that same transaction — an approval is
  what starts a change, so the bound on concurrent changes belongs where one starts.
- `execution` + `execution_host` — **runs** an approved one (T38). The executor re-reads the
  authorization rather than trusting the two identifiers it was handed (V23), dispatches to a
  `ChangeRunner`, and writes the terminal `tool_runs` status and the `action_receipts` row in
  one commit (V46). `execution_host` is V30's shape: one in-process asyncio task per approved
  change, its own session, cancelled by the lifespan at shutdown. It writes no
  `action_requests` column at all — the authorization is read there and answered nowhere else.
- `reaper` — resolves what nobody finished (T38, V30). Runs left `STARTED` past a deadline
  become `FAILED` with a summary saying the outcome is *unknown*, plus a receipt when they
  belong to a request. `APPROVED`-with-no-run is **detected and logged, never repaired**: the
  decision path cannot produce that pair (T37 writes both in one transaction), a deleted run
  row can, and in that case the change may well have completed — so inventing a failed run
  would put a claim in the audit trail nothing observed. **One pass is bounded** by
  `APPROVAL_STRANDED_RUN_REAP_BATCH_SIZE` and reports what it left, so a pass stays one short
  transaction and a capped pass never reads like a complete one (V85, V92).
- `expiry` — answers one that nobody answered. `SQLActionRequestExpiryRepository` (T39) can
  write exactly one terminal status, `EXPIRED`, and only for a row that is still `PENDING`
  past its deadline: the status is not a parameter and the predicate is part of the
  statement. Two callers need that and neither may hold the writer above — the background
  sweep, which has no operator behind it, and the render paths (T63's result tool, T41's
  card), neither of which is a decision. `PendingExpirySweeper` is the loop, hosted by the
  app lifespan.
- `reads` — the row guard both readers share. The requester-matched `SELECT` (V27) and V32's
  check-on-read ordering live here in one spelling each, because the two surfaces below must
  guard a row identically while rendering different things (V66). Writes nothing itself.
- `results` — reads one back *for a model* (T63): `noa_get_action_result`. Structurally
  narrower than the card — no `reason`, no `evidence`, no requester identity, and nowhere to
  put any of them (V76, C8, V17).
- `card` — reads one back *for the operator in front of it* (T41): the approval card's
  provenance, before-state and evidence (V33, V35). Same guard, wider projection, and still no
  `reason`: that column is written by a decision, not read by a render.
- `context` — the keys of `approval_context`, and the rules for taking the arguments and the
  preflight evidence off it. One writer (T33's gate) and four readers (a decision, a result,
  T41's card, T38's executor) over one JSONB column: a misspelt key there reads as an absent
  one, so the spelling is a constant (V66).
- `csrf` — the token that makes "the browser sent the cookie" insufficient on its own (V39).
  Shared mechanism, mint and verify in one place, so the card (T41) and the endpoint agree.
- `clock` — the re-export of `core.clock`, one definition of "now, aware, UTC", shared by the
  doors that compare a row against its deadline so the boundary cannot hold at one and not the
  other. It moved out of this package at T56, when a fourth reader appeared with no business
  importing the approval gate to read a clock (`core.results.tables`).
- `errors` — the refusals, as `NoaError` subclasses, in two trees: gate failures (the change
  was never submitted) and decision failures (a real request was refused). `NoaError` so
  `sanitize_tool_errors` (V19) hands the model a named code rather than a generic failure,
  and so `noa_api.api.errors` maps the same classes to a status.

Opening a request is `noa_api.mcp_tools.change_gate` rather than anything here, because it
needs the caller's identity and the in-process preflight evidence (C9, V17). Deciding one is
`noa_api.api.routes.action_requests`. Reading one back over MCP is
`noa_api.mcp_tools.noa_read` (T63), which supplies the caller `results` matches against; over
HTTP it is the `GET` on that same decision router (T41), which supplies `card`'s.
Expiring one needs no identity at all, which is why `expiry` is the only writer of a *status*
here with none in sight — and `execution` and `reaper` are the same shape one table over: an
executor has an authorization instead of a caller, and a reaper has neither.

What performs a change is not here either, and cannot be: a `ChangeRunner` is integration code
that reaches WHM, Proxmox or PMG, and it is registered in `noa_api.mcp_tools.change_runners`
(the WHM account pair at T22/T23; T25-T29 to come).
"""
