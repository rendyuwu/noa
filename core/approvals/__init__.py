"""The approval gate's persistence layer (T33-T39 — V22, V23, V28, V32, V33, V39).

`action_requests` is not a cache of a decision held somewhere else: the row **is** the
authorization (T34). V23 says "may this run?" is answered from `action_requests.status`
every time, never from an LLM claim and never from a tool argument, and that only means
anything if exactly one layer writes and reads that column. This package is that layer.

Eight modules, and the split that matters is **who can reach which writer** — three writers,
one reader, and four modules that hold no statement at all:

- `repository` — opens a request. `SQLActionRequestRepository` writes `PENDING` and can write
  nothing else, because its caller is the MCP tool path (T33) — the path an LLM can reach.
  It owns its session and commits, unlike the `core.servers` repositories that flush into a
  caller's transaction, for the reason `core.audit.tool_runs` (T73) gives: that path runs
  outside FastAPI's dependency graph, so there is no request transaction to join.
- `decisions` — answers one. `SQLActionDecisionRepository` is the only thing that writes a
  terminal status, under `SELECT … FOR UPDATE` (V28), and it is reached only from T37's
  cookie POST (V22). Deliberately a separate class from the one above: folding them together
  would put a writer that can set `APPROVED` on the side of the boundary V22 exists to close.
  It joins the request's session, because there *is* one here.
- `expiry` — answers one that nobody answered. `SQLActionRequestExpiryRepository` (T39) can
  write exactly one terminal status, `EXPIRED`, and only for a row that is still `PENDING`
  past its deadline: the status is not a parameter and the predicate is part of the
  statement. Two callers need that and neither may hold the writer above — the background
  sweep, which has no operator behind it, and the render paths (T63's result tool, T41's
  card), neither of which is a decision. `PendingExpirySweeper` is the loop, hosted by the
  app lifespan.
- `results` — reads one back for the operator who opened it (T63). The only module here that
  writes nothing: no `commit`, no status parameter, no statement that is not a `SELECT`. The
  requester-match sits in its `WHERE`, so a request that is not the caller's is never fetched
  (V27, V76), and the one write its service needs — expiring a stale PENDING before serving
  it — is `expiry`'s, run *after* that matched read so a foreign id cannot trigger a write.
- `context` — the keys of `approval_context`, and the rule for taking the arguments off it.
  One writer (T33's gate) and three readers (a decision, a result, T41's card) over one JSONB
  column: a misspelt key there reads as an absent one, so the spelling is a constant (V66).
- `csrf` — the token that makes "the browser sent the cookie" insufficient on its own (V39).
  Shared mechanism, mint and verify in one place, so the card (T41) and the endpoint agree.
- `clock` — one definition of "now, aware, UTC", shared by the two doors that compare a row
  against its deadline, so the boundary cannot hold at one and not the other.
- `errors` — the refusals, as `NoaError` subclasses, in two trees: gate failures (the change
  was never submitted) and decision failures (a real request was refused). `NoaError` so
  `sanitize_tool_errors` (V19) hands the model a named code rather than a generic failure,
  and so `noa_api.api.errors` maps the same classes to a status.

Opening a request is `noa_api.mcp_tools.change_gate` rather than anything here, because it
needs the caller's identity and the in-process preflight evidence (C9, V17). Deciding one is
`noa_api.api.routes.action_requests`. Reading one back over MCP is
`noa_api.mcp_tools.noa_read` (T63), which supplies the caller `results` matches against.
Expiring one needs no identity at all, which is why `expiry` is the only writer here with
none in sight. Executing an approved change is T38.
"""
