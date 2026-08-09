"""Audit trails (C12, V14, V46).

Four modules, and the shapes differ deliberately:

- `admin_events` — audit events for admin changes (T9, V14). A sink, written into structlog,
  no table.
- `tool_runs` — one `tool_runs` row per MCP tool execution (T35 built the table, T73 the
  writer; V20, V45-V47). A repository with its own transaction, because an audit row that
  rolls back with the request that wrote it is not an audit row.
- `receipts` — one `action_receipts` row per approved CHANGE: what it actually did (T36 built
  the table, T38 the writer; V46). Two callers can reach one finished run — the executor and
  the reaper — so the insert is idempotent on T36's `UNIQUE (action_request_id)` rather than
  trusting them not to collide.
- `summaries` — how a tool result becomes a `result_summary`: bounded, redacted, compact JSON,
  with the run's terminal status read off the envelope's `ok`. Written for the READ path (T73)
  and shared with the executor at T38, because both record the same field from the same shape
  (V66).

The first two are separate because they are written from different paths — the admin API and
the MCP tool path — and only one of them persists. `receipts` is not written from either: its
callers sit behind the approval boundary V22 draws, on a background task rather than a request.
"""

from core.audit.admin_events import (
    AdminAuditEvent,
    AdminAuditSink,
    StructlogAdminAuditSink,
)
from core.audit.receipts import ActionReceiptRepository, SQLActionReceiptRepository
from core.audit.summaries import result_summary, status_for_payload
from core.audit.tool_runs import SQLToolRunRepository, ToolRunRepository

__all__ = [
    "ActionReceiptRepository",
    "AdminAuditEvent",
    "AdminAuditSink",
    "SQLActionReceiptRepository",
    "SQLToolRunRepository",
    "StructlogAdminAuditSink",
    "ToolRunRepository",
    "result_summary",
    "status_for_payload",
]
