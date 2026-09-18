"""Audit trails.

Six modules, and the shapes differ deliberately:

- `admin_events` — audit events for admin changes. A sink, written into structlog,
  no table.
- `tool_runs` — one `tool_runs` row per MCP tool execution (the schema built the table, the
  tool-run writer writes to it; risk and status kept separate, queryable in admin audit). A
  repository with its own transaction, because an audit row that rolls back with the request
  that wrote it is not an audit row.
- `receipts` — one `action_receipts` row per approved CHANGE: what it actually did (the receipt
  table, written by the approved-change executor; run-plus-receipt in one commit). Two callers
  can reach one finished run — the executor and the reaper — so the insert is idempotent on the
  receipt table's `UNIQUE (action_request_id)` rather than trusting them not to collide.
- `summaries` — how a tool result becomes a `result_summary`: bounded, redacted, compact JSON,
  with the run's terminal status read off the envelope's `ok`. Written for the READ path
  and shared with the approved-change executor, because both record the same field from the
  same shape — one helper, not two.

- `tool_run_reads` — the *reader* over the same table: the admin audit list and
  detail. Split from `tool_runs` by what it can do — no `commit`, no statement that is not a
  `SELECT` — because the two are reached from opposite sides of the cookie/CSRF boundary. It is
  what turns the tool-run trail's "queryable in admin audit" from prose into a statement.
- `cursor` — keyset continuation tokens for that list, ported from `noa-old`, copied not imported.
- `errors` — the audit surface's two refusals, `NoaError` subclasses so `noa_api.api.errors`
  maps them and the routes raise instead of shaping a response.

The first two are separate because they are written from different paths — the admin API and
the MCP tool path — and only one of them persists. `receipts` is not written from either: its
callers sit behind the cookie/CSRF boundary, on a background task rather than a request.
"""
