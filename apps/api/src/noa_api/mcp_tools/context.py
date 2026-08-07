"""What a tool needs to reach the database, built once at startup (T19).

The twin of `McpAuthContext` (T12), and deliberately its twin: a frozen value object with a
session factory plus repository factories that production never passes. Same reasons.

- **One world, not two configured alike.** `create_app` builds this off the same
  `AppRuntime.session_factory` the verifier and the admin routes use, so an operator
  disabled through `/admin` is refused on their next tool call by the row that write
  touched (V1) — not by a second pool that will agree eventually.
- **Injectable repositories are the test seam**, and the only one. The real
  `AuthorizationService`, the real resolver and the real tool functions run in the suite;
  only the SQL is doubled. A seam at the service level would let a test pass against a
  policy the production wiring does not have.

Separate from `McpAuthContext` rather than folded into it because the two answer different
questions and are consulted at different times: authentication resolves once per request,
inside `verify_token`, and knows nothing about tools; this is read per tool call and per
`tools/list`. Merging them would put the tool repositories behind the auth path, where a
bug in either becomes a 401.

Session lifetime is per operation, not per request: each tool, each RBAC check and each of
the two `tool_runs` writes (T73) opens one, uses it, closes it. The audit writes are the
only ones that commit, and they commit *separately* on purpose — see `core.audit.tool_runs`.
When T33 makes a CHANGE tool write `action_requests`, that write and its audit event will
share one session, and this is where that factory comes from.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from core.audit.admin_events import AdminAuditSink, StructlogAdminAuditSink
from core.audit.tool_runs import SQLToolRunRepository, ToolRunRepository
from core.auth.authorization_repository import SQLAuthorizationRepository
from core.auth.authorization_service import AuthorizationService
from core.auth.authorization_types import AuthorizationRepository
from core.servers.whm_repository import SQLWHMServerRepository, WHMServerReadRepository
from noa_api.mcp_request_auth import McpSessionFactory


@dataclass(frozen=True)
class McpToolContext:
    """Everything the MCP tool path needs, built once at startup."""

    session_factory: McpSessionFactory
    authorization_repository_factory: Callable[[AsyncSession], AuthorizationRepository] = (
        SQLAuthorizationRepository
    )
    whm_server_repository_factory: Callable[[AsyncSession], WHMServerReadRepository] = (
        SQLWHMServerRepository
    )
    tool_run_repository_factory: Callable[[AsyncSession], ToolRunRepository] = SQLToolRunRepository
    # A sink, even though the read path records nothing: `AuthorizationService` takes one,
    # and handing it a working sink rather than a stub means the day a tool records an event
    # it goes somewhere real instead of into a placeholder nobody re-checked.
    audit_sink: AdminAuditSink = field(default_factory=StructlogAdminAuditSink)


def build_mcp_tool_context(*, session_factory: McpSessionFactory) -> McpToolContext:
    """Production wiring (T13's `create_app` calls this beside `build_mcp_auth_context`)."""
    return McpToolContext(session_factory=session_factory)


def build_authorization_service(
    context: McpToolContext, session: AsyncSession
) -> AuthorizationService:
    """The RBAC engine over one session (T9).

    Constructed per check rather than held on the context, because `AuthorizationService`
    snapshots its repository and the repository holds a session. A long-lived service would
    pin one connection for the life of the process and answer every later question through
    it.
    """
    return AuthorizationService(
        repository=context.authorization_repository_factory(session),
        audit_sink=context.audit_sink,
    )


def build_tool_run_repository(context: McpToolContext, session: AsyncSession) -> ToolRunRepository:
    """The `tool_runs` writer over one session (T73, V45).

    A function rather than a bare factory call so `noa_api.mcp_audit` names one thing for
    both of its writes, and so this reads the same way as `build_authorization_service`
    beside it. Constructed per write for the same reason: the repository holds the session,
    and a long-lived one would pin a connection for the life of the process.
    """
    return context.tool_run_repository_factory(session)


__all__ = [
    "McpToolContext",
    "build_authorization_service",
    "build_mcp_tool_context",
    "build_tool_run_repository",
]
