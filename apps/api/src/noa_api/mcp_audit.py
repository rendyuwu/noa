"""`tool_runs` written from the tool path, once, for every READ (T73 — V20, V45, V47, V83).

T19 shipped the first tool with its RBAC gate and no audit trail; T19(g) recorded the hole
and named this task as the fix. This is that fix, and it is a `Middleware` for the same
reason `RbacToolMiddleware` is (V83b): **one place, not per-tool code.** A tool can forget a
call to an audit helper; it cannot forget a middleware. T20-T31 and T63 inherit the row by
existing, and the tool functions stay free of the word "audit".

**Where it sits: inside the RBAC gate.** `FastMCP._run_middleware` builds its chain over
`reversed(self.middleware)` (`fastmcp/server/server.py:513`), so the first middleware added
is the outermost. `build_mcp_server` adds RBAC first and this second, which means a *refused*
call writes no row. That is deliberate on two counts: a denial is not an execution — V45
covers what NOA did, and the refusal already has its own structured log line
(`mcp_tool_denied`) — and the gate refuses uncatalogued names, so auditing outside it would
let any caller mint `tool_runs` rows for arbitrary strings.

**READ only, here.** `risk` comes from the registration map, not from a guess, and a CHANGE
tool is skipped: its `tools/call` opens an approval gate rather than executing anything
(T33), and V46's row is written by the executor that runs after approval (T38). Recording
the gate call as a CHANGE run would put a row in the audit trail for a change that has not
happened and may be denied. The map is why this is a decision rather than an accident — see
`noa_api.mcp_tools.registry`.

**Fail closed on the opening write.** If the `STARTED` row cannot be committed, the tool
does not run and the caller gets `tool_audit_unavailable`. V45 says *every* READ writes a
row; running anyway would leave that invariant asserted by prose and held by nothing, which
is the shape B2 shipped (V69). The closing write is different: by then the tool has already
run, so a failure there is logged loudly and the row is left `STARTED` — exactly the state
T38's reaper exists to sweep. Turning a completed call into an error would be a lie in the
other direction.

**What "failed" means.** `sanitize_tool_errors` (V19) converts an exception into a
*returned* `{"ok": False, ...}` payload, so a failure normally arrives as an ordinary
result, not as a raise. Status is therefore read off `ok`, and `result_summary` gets the
`error_code` — T35 left out an `error` column precisely because a sanitized code fits here.
A raise that still escapes (argument validation, a bug above the decorator) is recorded
FAILED and re-raised unchanged: this middleware audits, it does not sanitize.

**`conversation_ref` is a label, never a scope** (DECISIONS §10.4, old V165). It arrives as
`X-Noa-Conversation-Ref` because LibreChat sends no conversation identifier in the call
itself — at pin `45cc53c4` `MCPManager.callTool` sends `params: {name, arguments}` with no
`_meta` — but its header templating does resolve `{{LIBRECHAT_BODY_CONVERSATIONID}}`
(`packages/api/src/utils/env.ts`, `ALLOWED_BODY_FIELDS`). T57 writes that into
`librechat.yaml`; absent or unusable, the column is NULL and the call proceeds. It is
sanitized before it is stored because it lands in both Postgres and structlog — the same
log-forging surface V73 closed for `x-request-id`, so it reuses that check.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Final
from uuid import UUID

import structlog
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
from mcp import types as mt

from core.auth.mcp_auth_errors import McpAuthError
from core.db.lifecycle import ToolRisk, ToolRunStatus
from core.secrets.redaction import redact_sensitive_data
from noa_api.api.request_context import sanitize_header_label
from noa_api.mcp_request_auth import current_mcp_identity
from noa_api.mcp_tools.context import McpToolContext, build_tool_run_repository
from noa_api.mcp_tools.results import tool_failure

# Custom headers come back lowercased from `get_http_headers()` and are not on its default
# exclusion list, so one lowercase spelling serves the read (R5).
CONVERSATION_REF_HEADER: Final = "x-noa-conversation-ref"

# `tool_runs.conversation_ref` is `String(255)`; a longer value would fail the INSERT and
# take the whole call down with it (fail-closed), so it is dropped to NULL instead.
MAX_CONVERSATION_REF_LENGTH: Final = 255

# `tool_runs.result_summary` is `String(2000)`. Truncation happens here rather than at the
# column so the stored value is a marked ellipsis instead of a silently clipped one.
MAX_RESULT_SUMMARY_LENGTH: Final = 2000

TRUNCATION_SUFFIX: Final = "..."

# The caller-visible refusal when the audit row cannot be written. One code, like the RBAC
# gate's: it says "NOA declined", not why NOA's database is unhappy (V8).
ERROR_AUDIT_UNAVAILABLE: Final = "tool_audit_unavailable"

MESSAGE_AUDIT_UNAVAILABLE: Final = (
    "NOA could not record this call and will not run it unaudited. Try again; contact an "
    "administrator if this continues."
)

# The opening write failed and the call was refused. An operator-visible outage.
LOG_AUDIT_START_FAILED: Final = "mcp_tool_audit_start_failed"

# The tool ran and the terminal write failed. The row is stranded in STARTED.
LOG_AUDIT_FINISH_FAILED: Final = "mcp_tool_audit_finish_failed"

# The gate let a call through that this middleware cannot attribute. Unreachable through the
# mount; logged rather than passed over, because reaching it means the chain changed.
LOG_AUDIT_IDENTITY_UNRESOLVED: Final = "mcp_tool_audit_identity_unresolved"

logger = structlog.get_logger(__name__)


def read_conversation_ref() -> str | None:
    """The grouping label on the current request, or `None` (V47).

    Sanitized, not echoed: the value is written to `tool_runs` and to structured log output,
    so it is accepted only bounded and on the character allowlist V73 established. A
    rejected value is `None` — dropping a label costs an audit filter one row's grouping,
    while refusing the call would let a malformed header from a client NOA does not control
    turn every tool off.
    """
    return sanitize_header_label(
        get_http_headers().get(CONVERSATION_REF_HEADER),
        max_length=MAX_CONVERSATION_REF_LENGTH,
    )


def redacted_args(arguments: Mapping[str, Any] | None) -> dict[str, Any]:
    """Tool arguments as they may be stored (V8, V45).

    `{}` for a call with no arguments rather than `None`, matching the column's server
    default: "no arguments" and "arguments not recorded" must not read the same in an audit
    view.
    """
    redacted = redact_sensitive_data(dict(arguments or {}))
    return redacted if isinstance(redacted, dict) else {}


def result_summary(payload: Mapping[str, Any] | None) -> str | None:
    """A bounded, redacted rendering of what the tool answered (V45, V47).

    Compact JSON so the 2000 characters hold as much of the result as possible, `default=str`
    so a stray non-serializable value degrades to its repr instead of raising inside the
    audit path. Redacted for the same reason the arguments are: this row outlives the call
    and is read by the admin audit surface (T55).
    """
    if payload is None:
        return None

    rendered = json.dumps(redact_sensitive_data(dict(payload)), separators=(",", ":"), default=str)
    if len(rendered) <= MAX_RESULT_SUMMARY_LENGTH:
        return rendered
    return rendered[: MAX_RESULT_SUMMARY_LENGTH - len(TRUNCATION_SUFFIX)] + TRUNCATION_SUFFIX


def status_for_payload(payload: Mapping[str, Any] | None) -> ToolRunStatus:
    """COMPLETED or FAILED, read off the result envelope (V20).

    `ok` is the one field every tool result carries (`noa_api.mcp_tools.results`), and
    `sanitize_tool_errors` turns an exception into `ok: False` rather than a raise — so
    without this branch every failed READ would be recorded as a success. An ambiguity
    result (V18) is `ok: False` too, and is recorded FAILED on purpose: the call did not do
    what was asked, and `result_summary` carries the `choices` that say why.

    A missing or non-boolean `ok` counts as failure. A result that cannot be read as a
    success is not evidence of one.
    """
    if not payload or payload.get("ok") is not True:
        return ToolRunStatus.FAILED
    return ToolRunStatus.COMPLETED


class ToolRunAuditMiddleware(Middleware):
    """Write one `tool_runs` row per READ tool call (V45, V47, V83b)."""

    def __init__(self, *, context: McpToolContext, tool_risks: Mapping[str, ToolRisk]) -> None:
        self._context = context
        self._tool_risks = dict(tool_risks)

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Record the call around its execution, or refuse it (V45)."""
        tool_name = context.message.name
        if self._tool_risks.get(tool_name) is not ToolRisk.READ:
            # A CHANGE tool's `tools/call` opens the approval gate and executes nothing
            # (T33); its row belongs to the post-approval executor (T38, V46). An unmapped
            # name cannot reach here — the RBAC gate outside refuses anything unregistered —
            # so this is also the fail-closed answer if that ever stops being true.
            return await call_next(context)

        try:
            identity = current_mcp_identity()
        except McpAuthError as exc:
            # Unreachable through the mount: authentication answers 401 long before dispatch
            # and the RBAC gate reads the same identity to permit the call at all. If the
            # chain ever changes, an unattributable run is refused rather than written
            # against nobody.
            logger.error(LOG_AUDIT_IDENTITY_UNRESOLVED, tool=tool_name, error_code=exc.error_code)
            return self._refuse()

        started = await self._start_run(
            tool_name=tool_name,
            user_id=identity.user_id,
            arguments=context.message.arguments,
        )
        if started is None:
            return self._refuse()

        try:
            result = await call_next(context)
        except BaseException as exc:
            # Includes `ToolError` raised above `sanitize_tool_errors` (argument validation),
            # and cancellation. Recorded, then re-raised untouched: shaping the error is
            # V19's job and it belongs to the decorator on the tool (R25), not here.
            await self._finish_run(started, ToolRunStatus.FAILED, type(exc).__name__)
            raise

        await self._finish_run(
            started,
            status_for_payload(result.structured_content),
            result_summary(result.structured_content),
        )
        return result

    # --- Internals ---

    def _refuse(self) -> ToolResult:
        """The caller-visible answer when NOA will not run a call it cannot record.

        Same envelope and same `is_error=True` as the RBAC gate's refusal, so a model sees
        one shape whichever gate closed (`noa_api.mcp_rbac`).
        """
        return ToolResult(
            structured_content=tool_failure(ERROR_AUDIT_UNAVAILABLE, MESSAGE_AUDIT_UNAVAILABLE),
            is_error=True,
        )

    async def _start_run(
        self,
        *,
        tool_name: str,
        user_id: UUID,
        arguments: Mapping[str, Any] | None,
    ) -> UUID | None:
        """Commit the `STARTED` row, or `None` if it could not be written (V45).

        Committed before the tool runs, in its own session, so the evidence survives a
        process that dies mid-call. `Exception` rather than a driver-specific error: every
        way this can fail ends in "no audit row", and the call is refused for all of them.
        """
        try:
            async with self._context.session_factory() as session:
                repository = build_tool_run_repository(self._context, session)
                tool_run_id = await repository.start_run(
                    tool_name=tool_name,
                    requested_by_user_id=user_id,
                    risk=ToolRisk.READ,
                    conversation_ref=read_conversation_ref(),
                    args=redacted_args(arguments),
                )
                await repository.commit()
                return tool_run_id
        except Exception as exc:
            logger.error(
                LOG_AUDIT_START_FAILED,
                tool=tool_name,
                cause=type(exc).__name__,
                detail=str(exc),
            )
            return None

    async def _finish_run(
        self,
        tool_run_id: UUID,
        status: ToolRunStatus,
        summary: str | None,
    ) -> None:
        """Move the row to its terminal state (V20, V47).

        Swallows its own failure, unlike `_start_run`. The tool has already run by now, so
        refusing the caller would misreport a call that happened; the row stays `STARTED`,
        which is a state the schema defines and T38's reaper resolves, and the log line
        names it.
        """
        try:
            async with self._context.session_factory() as session:
                repository = build_tool_run_repository(self._context, session)
                await repository.finish_run(
                    tool_run_id=tool_run_id, status=status, result_summary=summary
                )
                await repository.commit()
        except Exception as exc:
            logger.error(
                LOG_AUDIT_FINISH_FAILED,
                tool_run_id=str(tool_run_id),
                status=status.value,
                cause=type(exc).__name__,
                detail=str(exc),
            )


__all__ = [
    "CONVERSATION_REF_HEADER",
    "ERROR_AUDIT_UNAVAILABLE",
    "LOG_AUDIT_FINISH_FAILED",
    "LOG_AUDIT_IDENTITY_UNRESOLVED",
    "LOG_AUDIT_START_FAILED",
    "MAX_CONVERSATION_REF_LENGTH",
    "MAX_RESULT_SUMMARY_LENGTH",
    "MESSAGE_AUDIT_UNAVAILABLE",
    "ToolRunAuditMiddleware",
    "read_conversation_ref",
    "redacted_args",
    "result_summary",
    "status_for_payload",
]
