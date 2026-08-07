"""Tool result shapes and the error boundary in front of the model (T19, V18, V19).

Every exposed tool answers with the same envelope, ported from `noa-old` (C13):

    {"ok": True,  ...payload}
    {"ok": False, "error_code": "...", "message": "...", "choices": [...]}

`ok` is the only field a caller has to branch on, `error_code` is the stable machine string
and `message` is what an operator reads. `choices` appears only on an ambiguity (V18).

**Why a decorator and not middleware.** V19 says a raw exception must not reach the LLM,
with two named mappings. That cannot be enforced from a FastMCP middleware, and the reason
is in the installed `fastmcp==3.4.5` rather than in any doc: `FastMCP.call_tool` runs the
middleware chain *around* `call_tool(run_middleware=False)`, and the raw-exception handling
lives inside that inner call (`fastmcp/server/server.py`). By the time a middleware sees the
failure it is already a `ToolError` and the original type is gone, so `RuntimeError` and
`TimeoutError` are no longer distinguishable. The mapping has to sit closer to the tool than
fastmcp's own handler does, which means on the tool.

Worse, that inner handler is not a safety net either: `mask_error_details` **defaults to
`False`**, and the unmasked branch raises `ToolError(f"Error calling tool {name!r}: {e}")` —
`str(e)` verbatim, in front of the model. `noa_api.mcp_server` therefore also sets
`mask_error_details=True`, as a second line for anything raised outside a decorated tool
(argument validation, a bug in the registry). The decorator is the first line and the one
V19 actually names.

`except Exception` is deliberately broader than V19's two classes. V19 names the two
mappings that must be exact; it does not license a third exception type reaching the model
because nobody predicted it. `BaseException` — `CancelledError`, `KeyboardInterrupt` — is
*not* caught: a cancelled request has no caller left to answer, and swallowing that would
turn a shutdown into a hang.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec

import structlog

from core.errors import NoaError

# One structured event per tool failure, so the cause an operator cannot see is still
# recoverable from the logs. `error_code` says which mapping fired.
LOG_TOOL_FAILED = "mcp_tool_failed"

# V19's two named mappings, verbatim.
ERROR_TOOL_EXECUTION_FAILED = "tool_execution_failed"
ERROR_TIMEOUT = "timeout"

MESSAGE_TOOL_EXECUTION_FAILED = (
    "The tool failed while running. Try again; contact an administrator if this continues."
)
MESSAGE_TIMEOUT = "The tool timed out before the target system answered. Try again."

ToolPayload = dict[str, Any]

P = ParamSpec("P")

logger = structlog.get_logger(__name__)


def tool_ok(**payload: Any) -> ToolPayload:
    """A successful tool result.

    `ok` is written here rather than by each tool so no tool can return a payload that
    happens to omit it — a result without `ok` reads as success to a model that only checks
    for an error field.
    """
    return {"ok": True, **payload}


def tool_failure(
    error_code: str, message: str, *, choices: list[dict[str, str]] | None = None
) -> ToolPayload:
    """A refused or failed tool result.

    `choices` is omitted when empty rather than sent as `[]`: an empty list invites a model
    to report "here are the options" when there are none (V18).
    """
    failure: ToolPayload = {"ok": False, "error_code": error_code, "message": message}
    if choices:
        failure["choices"] = choices
    return failure


def sanitize_tool_errors(
    tool_name: str,
) -> Callable[[Callable[P, Awaitable[ToolPayload]]], Callable[P, Awaitable[ToolPayload]]]:
    """Turn any exception out of a tool into a named structured failure (V19).

    Three branches, narrowest first:

    - `NoaError` — already a NOA failure with a stable `error_code` and an operator-safe
      `message` (V73's contract). Passed through with its own code, so an integration-layer
      refusal such as `ssh_host_key_mismatch` keeps naming the thing that has to be fixed
      instead of collapsing into a generic failure.
    - `TimeoutError` — `timeout`. In 3.11 `asyncio.TimeoutError` *is* `TimeoutError`, so one
      branch covers both the socket and the `asyncio.timeout()` cases.
    - anything else — `tool_execution_failed`.

    The real cause is logged, never returned: `detail` and tracebacks may name hosts, paths
    and configuration (V8), and everything past this line is transcript the LibreChat admin
    can read (V26).
    """

    def decorate(
        tool: Callable[P, Awaitable[ToolPayload]],
    ) -> Callable[P, Awaitable[ToolPayload]]:
        @functools.wraps(tool)
        async def sanitized(*args: P.args, **kwargs: P.kwargs) -> ToolPayload:
            try:
                return await tool(*args, **kwargs)
            except NoaError as exc:
                return _failed(tool_name, exc, exc.error_code, exc.message)
            except TimeoutError as exc:
                return _failed(tool_name, exc, ERROR_TIMEOUT, MESSAGE_TIMEOUT)
            except Exception as exc:
                return _failed(
                    tool_name, exc, ERROR_TOOL_EXECUTION_FAILED, MESSAGE_TOOL_EXECUTION_FAILED
                )

        return sanitized

    return decorate


def _failed(tool_name: str, exc: BaseException, error_code: str, message: str) -> ToolPayload:
    """Log the cause, return the sanitized failure."""
    logger.warning(
        LOG_TOOL_FAILED,
        tool=tool_name,
        error_code=error_code,
        cause=type(exc).__name__,
        detail=str(exc),
    )
    return tool_failure(error_code, message)


__all__ = [
    "ERROR_TIMEOUT",
    "ERROR_TOOL_EXECUTION_FAILED",
    "LOG_TOOL_FAILED",
    "MESSAGE_TIMEOUT",
    "MESSAGE_TOOL_EXECUTION_FAILED",
    "ToolPayload",
    "sanitize_tool_errors",
    "tool_failure",
    "tool_ok",
]
