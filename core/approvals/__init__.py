"""The approval gate's persistence layer (T33-T39 — V22, V23, V32, V33).

`action_requests` is not a cache of a decision held somewhere else: the row **is** the
authorization (T34). V23 says "may this run?" is answered from `action_requests.status`
every time, never from an LLM claim and never from a tool argument, and that only means
anything if exactly one layer writes and reads that column. This package is that layer.

Two halves, split by who calls them:

- `repository` — the SQL. `SQLActionRequestRepository` owns its session and commits, unlike
  the `core.servers` repositories that flush into a caller's transaction. Same split and
  same reason as `core.audit.tool_runs` (T73): the MCP tool path runs outside FastAPI's
  dependency graph, so there is no request transaction to join.
- `errors` — the refusals, as `NoaError` subclasses, so `sanitize_tool_errors` (V19) hands
  the model a named code rather than a generic failure and `noa_api.api.errors` can map the
  same classes to a status when T37's endpoints raise them.

Nothing here decides anything. Opening a request (T33) is `noa_api.mcp_tools.change_gate`,
because it needs the caller's identity and the in-process preflight evidence; deciding one
is T37, under a row lock, from a cookie POST (V22, V28).
"""
