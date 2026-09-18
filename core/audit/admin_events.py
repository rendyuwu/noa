"""Audit events for admin changes.

The admin-audit rule has two clauses and they are enforced in different places. "Permission
updates take effect immediately" is enforced by `AuthorizationService` holding no cache — every
check re-reads the row. "Admin changes produce audit events" is enforced here: every mutating
operation on that service records one event before returning.

The sink is a Protocol, not a class the service constructs, for a reason that is about to
matter. Nothing has created an admin `audit_log` table (the `tool_runs` table is a different
artifact — the run-plus-receipt rule names both), so today's concrete sink writes structlog
events. When that table lands, a `SQLAdminAuditSink` implements this same Protocol and
nothing in the engine changes. The alternative — logging inline from the service — would
make that swap a rewrite of six call sites.

`record()` is async even though the structlog implementation never awaits. A SQL sink
will, and a synchronous Protocol would force every caller to change when it arrives.

Envelope discipline: an event payload carries identifiers, role names and tool names. Never a
password, never a token, never a hash. `metadata` is written by this module's callers only — nothing
funnels request bodies into it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

# Event type strings, verbatim from `noa-old` (`admin_*` in its route handlers — ported, never
# imported). The admin panel was ported over and any log-based alerting keys on these, so they
# are stable API, not free text.
EVENT_ROLE_CREATED = "admin_role_created"
EVENT_ROLE_DELETED = "admin_role_deleted"
EVENT_ROLE_TOOLS_UPDATED = "admin_role_tools_updated"
EVENT_USER_STATUS_UPDATED = "admin_user_status_updated"
EVENT_USER_DELETED = "admin_user_deleted"
EVENT_USER_ROLES_UPDATED = "admin_user_roles_updated"

# MCP token mint/revoke. No `noa-old` equivalent — it had no per-user MCP credential to mint.
# Issuing and revoking one is a change to what a bearer can reach, so it belongs in the same trail
# as
# a role edit. The payload carries the token id, the display prefix and the label;
# never the plaintext, never the digest.
# S105: these are log event names, not credentials — `token` in the name trips the check.
EVENT_MCP_TOKEN_MINTED = "admin_mcp_token_minted"  # noqa: S105
EVENT_MCP_TOKEN_REVOKED = "admin_mcp_token_revoked"  # noqa: S105

# Server inventory. `pmg_server_created` / `_deleted` / `_validated` are `noa-old`'s
# strings verbatim (its `PMGServerService._create_audit_log` was the only one of the three
# verticals that wrote real audit rows; WHM and Proxmox logged unstructured lines). The other
# nine follow that spelling rather than inventing a second one, so a query over this trail
# reads `<system>_server_<verb>` for all three tables.
#
# `_validated` is in the trail beside the three mutations for a reason worth stating: a
# validate is the only operator action that can *write* a host-key pin, and "who pinned
# this key, and when" is the question a mismatch six months later turns into.
#
# Payloads carry ids, names, hosts, ports and booleans. Never an API token, never an SSH
# credential, never a fingerprint's surrounding secret material.
EVENT_WHM_SERVER_CREATED = "whm_server_created"
EVENT_WHM_SERVER_UPDATED = "whm_server_updated"
EVENT_WHM_SERVER_DELETED = "whm_server_deleted"
EVENT_WHM_SERVER_VALIDATED = "whm_server_validated"
EVENT_PROXMOX_SERVER_CREATED = "proxmox_server_created"
EVENT_PROXMOX_SERVER_UPDATED = "proxmox_server_updated"
EVENT_PROXMOX_SERVER_DELETED = "proxmox_server_deleted"
EVENT_PROXMOX_SERVER_VALIDATED = "proxmox_server_validated"
EVENT_PMG_SERVER_CREATED = "pmg_server_created"
EVENT_PMG_SERVER_UPDATED = "pmg_server_updated"
EVENT_PMG_SERVER_DELETED = "pmg_server_deleted"
EVENT_PMG_SERVER_VALIDATED = "pmg_server_validated"

# The log event name every sink writes under, so a query filters on one key and reads
# `event_type` for the specific change.
LOG_EVENT = "admin_audit"


@dataclass(frozen=True)
class AdminAuditEvent:
    """One admin change, as recorded.

    `actor_email` is nullable because a change can originate outside a request — the
    bootstrap admin path has no acting operator. Recording `None` says "NOA did
    this"; omitting the event would say nothing happened.

    `target` is the thing changed, as a display string (a user id or a role name), so a
    reader does not have to know which metadata key each event type uses.
    """

    event_type: str
    actor_email: str | None
    target: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


class AdminAuditSink(Protocol):
    """Where admin audit events go.

    One method on purpose: a sink that also queried would tempt the engine into reading
    its own audit trail, and "may this run?" is answered from the domain tables, never
    from the log (the verdict comes from `action_requests.status`, in spirit).
    """

    async def record(self, event: AdminAuditEvent) -> None: ...


class StructlogAdminAuditSink:
    """`AdminAuditSink` writing structured log events.

    Chosen because it is the only sink available: no admin `audit_log` table
    exists yet, and inventing one here would create a table with no reader and no task behind it.
    Structured logs satisfy "admin changes produce audit events" literally and are queryable in
    whatever the deployment ships.

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
