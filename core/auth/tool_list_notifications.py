"""Telling connected MCP clients their tool catalog moved.

The execution-time RBAC re-check requires NOA to emit `notifications/tools/list_changed` when a
permission changes. The trigger for that emit is a *write* — a role's grants replaced, a user's
roles replaced, an account disabled — and every one of those writes lives in `AuthorizationService`,
on the admin REST side of the app. The emit itself needs a live MCP session, which is on the other
side. This module is the seam between them.

Same shape as `core.audit.admin_events.AdminAuditSink`, and for the same reasons:

- **A Protocol, not a class the service constructs.** The concrete notifier holds fastmcp
  and `mcp` session objects, which belong to `apps/api` — `core/` must not import the
  transport to announce a permission change. When the emit mechanism changes (a second
  client, a fan-out through a message bus), `AuthorizationService` does not.
- **`notify` is async** even though a null implementation never awaits.
- **It takes user ids, not a role name or a user object.** The service is what knows which
  operators a write affected — a role's grant change affects its holders, a disable affects
  one account — and resolving that from a role name inside the notifier would put the same
  question in two places.

**This is best-effort, and that is a measured position rather than a shrug.** Measurement settled
what LibreChat does with the notification at pin `45cc53c4`: nothing. Zero handlers for
`ToolListChangedNotificationSchema` in the tree, and a notification NOA provably put on a
session's stream drew no `tools/list` after it. So the emit is protocol-correct, costs
almost nothing, and may be honoured by a later client — but the property that keeps a stale
catalog from being a security hole is the execution-time re-check, not this. A notifier
that fails must therefore never fail the write it followed: the permission change is
authoritative the moment it commits, and an operator's catalog display is not.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Protocol
from uuid import UUID


class ToolListChangedNotifier(Protocol):
    """Where "these operators' tool catalogs changed" goes.

    One method, and it announces rather than asks. A notifier that could also report which
    sessions exist would tempt the engine into deciding something from the answer, and "may
    this run?" is resolved from the domain tables on every call — the request row, in spirit.

    Implementations must not raise. See the module docstring: the write has already
    committed by the time this is called, so an exception here would turn a successful
    permission change into a 500 and invite the operator to repeat it.
    """

    async def notify(self, user_ids: Collection[UUID]) -> None: ...


class NullToolListChangedNotifier:
    """A `ToolListChangedNotifier` that emits nothing.

    Not a test stub — a real deployment target. `AuthorizationService` is reachable from
    processes with no MCP mount and therefore no session to notify: a future CLI, a
    migration script, and today the MCP tool path's own service instance
    (`noa_api.mcp_tools.context.build_authorization_service`), which resolves permissions
    and writes none.

    It is also the constructor default, deliberately, and that default has a cost worth
    naming: a production wiring that forgot to pass the real notifier would emit nothing and
    no test of this module would notice. That is why the wiring itself is asserted —
    `test_app_lifespan.py` checks the app's service holds the real one. The alternative, a
    required argument, would make every harness that builds an `AuthorizationService`
    construct a notifier it has no session for.
    """

    async def notify(self, user_ids: Collection[UUID]) -> None:
        """Do nothing, for any input."""


__all__ = [
    "NullToolListChangedNotifier",
    "ToolListChangedNotifier",
]
