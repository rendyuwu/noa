"""Tool result shapes and the error boundary in front of the model.

Every exposed tool answers with the same envelope, ported from `noa-old`:

    {"ok": True,  ...payload}
    {"ok": False, "error_code": "...", "message": "...", "choices": [...]}

`ok` is the only field a caller has to branch on, `error_code` is the stable machine string
and `message` is what an operator reads. `choices` appears only on an ambiguity.

**Why a decorator and not middleware.** The sanitize-to-a-code rule says a raw exception must not
reach the LLM, with two named mappings. That cannot be enforced from a FastMCP middleware, and the
reason is in the installed `fastmcp==3.4.5` rather than in any doc: `FastMCP.call_tool` runs the
middleware chain *around* `call_tool(run_middleware=False)`, and the raw-exception handling lives
inside that inner call (`fastmcp/server/server.py`). By the time a middleware sees the failure it is
already a `ToolError` and the original type is gone, so `RuntimeError` and `TimeoutError` are no
longer distinguishable. The mapping has to sit closer to the tool than fastmcp's own handler does,
which means on the tool.

Worse, that inner handler is not a safety net either: `mask_error_details` **defaults to
`False`**, and the unmasked branch raises `ToolError(f"Error calling tool {name!r}: {e}")` —
`str(e)` verbatim, in front of the model. `noa_api.mcp_server` therefore also sets
`mask_error_details=True`, as a second line for anything raised outside a decorated tool
(argument validation, a bug in the registry). The decorator is the first line and the one
the sanitize-to-a-code rule actually names.

`except Exception` is deliberately broader than the sanitize-to-a-code rule's two classes. That
rule names the two mappings that must be exact; it does not license a third exception type
reaching the model because nobody predicted it. `BaseException` — `CancelledError`,
`KeyboardInterrupt` — is *not* caught: a cancelled request has no caller left to answer, and
swallowing that would
turn a shutdown into a hang.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

import structlog
from fastmcp.tools import ToolResult

from core.errors import NoaError

# One structured event per tool failure, so the cause an operator cannot see is still
# recoverable from the logs. `error_code` says which mapping fired.
LOG_TOOL_FAILED = "mcp_tool_failed"

# The sanitize-to-a-code rule's two named mappings, verbatim.
ERROR_TOOL_EXECUTION_FAILED = "tool_execution_failed"
ERROR_TIMEOUT = "timeout"

# The `error_code` a failure that carries none falls back to. Verbatim from `noa-old`, and here
# rather than beside one system's tools because every tool that resolves a `server_ref` or reads
# a client's failure dict needs the same fallback — it started in `whm_read` and `whm_firewall`
# already reached across for it, which is one import away from a second spelling.
ERROR_UNKNOWN = "unknown"

MESSAGE_TOOL_EXECUTION_FAILED = (
    "The tool failed while running. Try again; contact an administrator if this continues."
)
MESSAGE_TIMEOUT = "The tool timed out before the target system answered. Try again."

ToolPayload = dict[str, Any]

# What an exposed tool may answer with. Most answer the envelope above and let fastmcp turn it
# into structured content. A tool whose answer is a *surface* — the large-READ table,
# and the CHANGE gate when its tools land — returns content blocks itself, because the
# order of those blocks is part of what the operator is told.
ToolAnswer = ToolResult | ToolPayload

P = ParamSpec("P")

# The success type of the tool being decorated. Named so `sanitize_tool_errors` can widen a
# return type rather than flatten it: a tool that answers `ToolPayload` keeps answering
# `ToolPayload`, and one that answers `ToolResult` is typed as "that, or a failure envelope".
AnswerT = TypeVar("AnswerT")

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
    to report "here are the options" when there are none.
    """
    failure: ToolPayload = {"ok": False, "error_code": error_code, "message": message}
    if choices:
        failure["choices"] = choices
    return failure


def sanitize_tool_errors(
    tool_name: str,
) -> Callable[[Callable[P, Awaitable[AnswerT]]], Callable[P, Awaitable[AnswerT | ToolPayload]]]:
    """Turn any exception out of a tool into a named structured failure.

    **The failure is always the envelope, whatever the success was.** A tool that answers with
    content blocks (the account-list's table surface) still fails as `{"ok": False, ...}`, which
    is what keeps one refusal shape in front of the model however the tool succeeds — and what
    lets
    `status_for_payload` read a failure off any tool's result.

    Three branches, narrowest first:

    - `NoaError` — already a NOA failure with a stable `error_code` and an operator-safe
      `message` (the shared request-id rule's contract). Passed through with its own code, so an
      integration-layer refusal such as `ssh_host_key_mismatch` keeps naming the thing that has
      to be fixed
      instead of collapsing into a generic failure.
    - `TimeoutError` — `timeout`. In 3.11 `asyncio.TimeoutError` *is* `TimeoutError`, so one
      branch covers both the socket and the `asyncio.timeout()` cases.
    - anything else — `tool_execution_failed`.

    The real cause is logged, never returned: `detail` and tracebacks may name hosts, paths
    and configuration, and everything past this line is transcript the LibreChat admin
    can read.
    """

    def decorate(
        tool: Callable[P, Awaitable[AnswerT]],
    ) -> Callable[P, Awaitable[AnswerT | ToolPayload]]:
        @functools.wraps(tool)
        async def sanitized(*args: P.args, **kwargs: P.kwargs) -> AnswerT | ToolPayload:
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
    "ERROR_UNKNOWN",
    "LOG_TOOL_FAILED",
    "MESSAGE_TIMEOUT",
    "MESSAGE_TOOL_EXECUTION_FAILED",
    "ToolAnswer",
    "ToolPayload",
    "sanitize_tool_errors",
    "tool_failure",
    "tool_ok",
]
