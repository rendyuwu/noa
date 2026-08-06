"""Audit trails (C12, V14).

Landed: `admin_events` — audit events for admin changes (T9, V14).

To come: tool-run audit (`tool_runs`, T35, V45-V47) and action receipts (T36, V46). They
live beside this rather than inside it because they are written by the MCP path, not the
admin path, and they persist to their own tables.
"""

from core.audit.admin_events import (
    AdminAuditEvent,
    AdminAuditSink,
    StructlogAdminAuditSink,
)

__all__ = [
    "AdminAuditEvent",
    "AdminAuditSink",
    "StructlogAdminAuditSink",
]
