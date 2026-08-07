"""Audit trails (C12, V14).

Landed: `admin_events` — audit events for admin changes (T9, V14).

Tool-run audit has a table but no writer: `tool_runs` exists as of T35 (V20, V45-V47) and
T73 wires the write into the MCP tool path. Action receipts (T36, V46) have neither yet.
Both live beside this rather than inside it because they are written by the MCP path, not
the admin path, and they persist to their own tables.
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
