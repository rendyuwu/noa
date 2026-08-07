"""Audit trails (C12, V14).

Two surfaces, two shapes, and the difference is deliberate:

- `admin_events` — audit events for admin changes (T9, V14). A sink, written into structlog,
  no table.
- `tool_runs` — one `tool_runs` row per MCP tool execution (T35 built the table, T73 the
  writer; V20, V45-V47). A repository with its own transaction, because an audit row that
  rolls back with the request that wrote it is not an audit row.

They are separate because they are written from different paths — the admin API and the MCP
tool path — and only one of them persists. Action receipts (T36, V46) have neither table nor
writer yet.
"""

from core.audit.admin_events import (
    AdminAuditEvent,
    AdminAuditSink,
    StructlogAdminAuditSink,
)
from core.audit.tool_runs import SQLToolRunRepository, ToolRunRepository

__all__ = [
    "AdminAuditEvent",
    "AdminAuditSink",
    "SQLToolRunRepository",
    "StructlogAdminAuditSink",
    "ToolRunRepository",
]
