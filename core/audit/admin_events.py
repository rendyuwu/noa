"""Audit events for admin changes (V14, T9).

V14 has two clauses and they are enforced in different places. "Permission updates take
effect immediately" is enforced by `AuthorizationService` holding no cache — every check
re-reads the row. "Admin changes produce audit events" is enforced here: every mutating
operation on that service records one event before returning.

The sink is a Protocol, not a class the service constructs, for a reason that is about to
matter. §T has no task creating an admin `audit_log` table (T35 creates `tool_runs`,
which is a different artifact — V46 names both), so today's concrete sink writes structlog
events. When that table lands, a `SQLAdminAuditSink` implements this same Protocol and
nothing in the engine changes. The alternative — logging inline from the service — would
make that swap a rewrite of six call sites.

`record()` is async even though the structlog implementation never awaits. A SQL sink
will, and a synchronous Protocol would force every caller to change when it arrives.

V8: an event payload carries identifiers, role names and tool names. Never a password,
never a token, never a hash. `metadata` is written by this module's callers only — nothing
funnels request bodies into it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

# Event type strings, verbatim from `noa-old` (`admin_*` in its route handlers, C13). The
# admin panel ported in T48 and any log-based alerting key on these, so they are stable
# API, not free text.
EVENT_ROLE_CREATED = "admin_role_created"
EVENT_ROLE_DELETED = "admin_role_deleted"
EVENT_ROLE_TOOLS_UPDATED = "admin_role_tools_updated"
EVENT_USER_STATUS_UPDATED = "admin_user_status_updated"
EVENT_USER_DELETED = "admin_user_deleted"
EVENT_USER_ROLES_UPDATED = "admin_user_roles_updated"

# The log event name every sink writes under, so a query filters on one key and reads
# `event_type` for the specific change.
LOG_EVENT = "admin_audit"


@dataclass(frozen=True)
class AdminAuditEvent:
    """One admin change, as recorded.

    `actor_email` is nullable because a change can originate outside a request — the
    bootstrap admin path (V7) has no acting operator. Recording `None` says "NOA did
    this"; omitting the event would say nothing happened.

    `target` is the thing changed, as a display string (a user id or a role name), so a
    reader does not have to know which metadata key each event type uses.
    """

    event_type: str
    actor_email: str | None
    target: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


class AdminAuditSink(Protocol):
    """Where admin audit events go (V14).

    One method on purpose: a sink that also queried would tempt the engine into reading
    its own audit trail, and "may this run?" is answered from the domain tables, never
    from the log (V23 in spirit).
    """

    async def record(self, event: AdminAuditEvent) -> None: ...


class StructlogAdminAuditSink:
    """`AdminAuditSink` writing structured log events.

    Chosen for T9 because it is the only sink available: no admin `audit_log` table
    exists yet, and inventing one here would create a table with no reader and no §T row.
    Structured logs satisfy V14's "produce audit events" literally and are queryable in
    whatever the deployment ships (T60).

    Known limit, recorded rather than papered over: log retention is not database
    retention. When the audit table lands, this stays useful as a second destination but
    stops being the system of record.
    """

    def __init__(self, logger: Any | None = None) -> None:
        self._logger = logger or structlog.get_logger(__name__)

    async def record(self, event: AdminAuditEvent) -> None:
        """Emit `event` as one structured log line."""
        self._logger.info(
            LOG_EVENT,
            event_type=event.event_type,
            actor_email=event.actor_email,
            target=event.target,
            **event.metadata,
        )


__all__ = [
    "EVENT_ROLE_CREATED",
    "EVENT_ROLE_DELETED",
    "EVENT_ROLE_TOOLS_UPDATED",
    "EVENT_USER_DELETED",
    "EVENT_USER_ROLES_UPDATED",
    "EVENT_USER_STATUS_UPDATED",
    "LOG_EVENT",
    "AdminAuditEvent",
    "AdminAuditSink",
    "StructlogAdminAuditSink",
]
