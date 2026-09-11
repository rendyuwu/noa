"""RBAC on the MCP path: one gate for every tool.

The execution-time permission re-check has two clauses and they are two different checks:

    `tools/list` RBAC-filtered per user. `tools/call` re-checks permission.

Both are here, as a fastmcp `Middleware`, rather than inside each tool. A tool cannot forget
a middleware; every registered READ, CHANGE and result tool inherits this gate by existing. It
also keeps the tools free of
identity plumbing — a tool asks "what do I do", never "may this caller do it".

**The re-check is not redundant with the filter.** The execution-time RBAC backstop accepts
that a client may hold a
stale `tools/list` — the handshake era the negotiated-per-client rule pins has no
`ttlMs`/`cacheScope` to bound it —
and leans on exactly this execution-time check as the backstop. So a tool revoked a second
ago may still be *displayed*, and calling it still fails.

**Nothing is cached, including within a request.** `AuthorizationService` re-reads the
`users` row and the grant rows on every question, which is what makes the
immediately-effective-permissions rule's
"immediately" and the disabled-account rule's "disabled → zero permissions" true against a live
session. Two DB
reads per `tools/call` is the price of not having a revocation window.

**Refusal shape.** A denial is a `ToolResult` with `is_error=True` carrying the same
`{"ok": False, "error_code", "message"}` envelope the tools use
(`noa_api.mcp_tools.results`), so a model sees one shape whether a tool refused or the gate
did. `is_error` is set because the tool did not run — that is what `isError` means — while a
tool's own structured refusal is a result it computed.

**One code, `tool_not_permitted`, for every refusal.** The admin-bypass rule asks for two
rejections — no
grant, and no such tool — and answering them differently would be an oracle: a caller could
learn which of the never-implement list's names exist behind the scenes, or which catalogued
tools are built yet, by reading which refusal came back. So the gate also carries the set of
names this server actually *registered* and refuses anything outside it. That set is not a
second permission model; it is what stops a catalogued-but-unbuilt name (the READ and CHANGE
tools are still
to come, and the `admin` bypass grants every *catalogued* tool) from falling through to
fastmcp's own `Unknown tool` error, which would answer in a different shape and say more.

**Fail closed.** If identity cannot be resolved (no access token on a request that somehow
reached a tool) or the `users` row has disappeared mid-request, the answer is zero
permissions — an empty `tools/list` and a refused call — not an exception and not a pass.
"""

from __future__ import annotations

from collections.abc import Sequence

import structlog
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import Tool, ToolResult
from mcp import types as mt

from core.auth.authorization_errors import UserNotFoundError
from core.auth.mcp_auth_errors import McpAuthError
from noa_api.mcp_audit import rebind_request_id
from noa_api.mcp_request_auth import current_mcp_identity
from noa_api.mcp_tools.context import McpToolContext, build_authorization_service
from noa_api.mcp_tools.results import tool_failure

# One structured event per refused call, so a "the model says it cannot do X" report is
# answerable from the logs. Identifiers only, never the arguments.
LOG_TOOL_DENIED = "mcp_tool_denied"

# Emitted when the gate could not identify the caller at all. Distinct from a denial: this
# one means the mount or the verifier is wrong, not that an operator lacks a role.
LOG_IDENTITY_UNRESOLVED = "mcp_tool_identity_unresolved"

ERROR_TOOL_NOT_PERMITTED = "tool_not_permitted"

MESSAGE_TOOL_NOT_PERMITTED = (
    "You do not have permission to run this tool. Ask a NOA administrator for the role "
    "that grants it."
)

logger = structlog.get_logger(__name__)


class RbacToolMiddleware(Middleware):
    """Filter `tools/list` and re-check `tools/call` against the caller's grants."""

    def __init__(self, *, context: McpToolContext, registered_tools: frozenset[str]) -> None:
        self._context = context
        self._registered_tools = registered_tools

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        """Show only what this caller may call.

        Filtered after `call_next` rather than by asking the registry directly, so tool
        transformations and any future provider still pass through fastmcp's own resolution
        first and the filter applies to what would actually have been sent.

        Rebound for the same reason `on_call_tool` is — `_permitted_tools` emits
        `mcp_tool_denied` from this path too, when the caller's row has gone away.
        """
        with rebind_request_id():
            tools = await call_next(context)
            permitted = await self._permitted_tools()
            return [tool for tool in tools if tool.name in permitted]

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Refuse before the tool runs, whatever an earlier `tools/list` said.

        Wrapped in the audit middleware's rebind because this gate is added first and is
        therefore *outermost*: that middleware's own rebind sits inside this one, so a denial
        is logged before it ever runs and `mcp_tool_denied` would carry the id of the request
        that opened the MCP session rather than the refused call's own. Same helper, not a
        second mechanism — see `noa_api.mcp_audit` for why the ambient binding names the
        wrong request, and note that the two nest harmlessly on the permitted path because
        both bind the same value and restore what they replaced.
        """
        with rebind_request_id():
            tool_name = context.message.name
            if tool_name not in await self._permitted_tools():
                logger.warning(LOG_TOOL_DENIED, tool=tool_name, error_code=ERROR_TOOL_NOT_PERMITTED)
                return ToolResult(
                    structured_content=tool_failure(
                        ERROR_TOOL_NOT_PERMITTED, MESSAGE_TOOL_NOT_PERMITTED
                    ),
                    is_error=True,
                )

            return await call_next(context)

    # --- Internals ---

    async def _permitted_tools(self) -> set[str]:
        """The caller's effective tool set, read fresh from the database.

        `get_permitted_tools` already applies the disabled-account rule (disabled → empty) and the
        admin-bypass rule (admin → every
        catalogued tool), so no policy is re-decided here — asking "is this caller an admin?"
        in two places is how the two answers drift. The one thing added is the intersection
        with what this server registered, which is about existence rather than permission
        (see the module docstring).
        """
        try:
            identity = current_mcp_identity()
        except McpAuthError as exc:
            # Unreachable through the mount: `RequireAuthMiddleware` answers 401 before a
            # tool is dispatched. Fail closed anyway — the alternative is an
            # unauthenticated tool call whenever that assumption stops holding.
            logger.error(LOG_IDENTITY_UNRESOLVED, error_code=exc.error_code, detail=exc.detail)
            return set()

        async with self._context.session_factory() as session:
            service = build_authorization_service(self._context, session)
            try:
                granted = await service.get_permitted_tools(identity.user_id)
            except UserNotFoundError:
                # The row went away between authentication and dispatch — an admin deleted
                # the operator mid-request. No row, no permissions.
                logger.warning(LOG_TOOL_DENIED, error_code=UserNotFoundError.error_code)
                return set()

        return granted & self._registered_tools


__all__ = [
    "ERROR_TOOL_NOT_PERMITTED",
    "LOG_IDENTITY_UNRESOLVED",
    "LOG_TOOL_DENIED",
    "MESSAGE_TOOL_NOT_PERMITTED",
    "RbacToolMiddleware",
]
