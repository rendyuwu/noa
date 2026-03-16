from __future__ import annotations

import asyncio
from dataclasses import dataclass

from noa_api.core.tools.argument_validation import ToolArgumentValidationError
from noa_api.core.tools.result_validation import ToolResultValidationError


@dataclass(frozen=True, slots=True)
class SanitizedToolError:
    error: str
    error_code: str
    details: tuple[str, ...] | None = None

    def as_result(self) -> dict[str, object]:
        result: dict[str, object] = {
            "error": self.error,
            "error_code": self.error_code,
        }
        if self.details:
            result["details"] = list(self.details)
        return result


def sanitize_tool_error(exc: Exception) -> SanitizedToolError:
    if isinstance(exc, ToolArgumentValidationError):
        return SanitizedToolError(
            error=exc.error,
            error_code=exc.error_code,
            details=exc.details,
        )

    if isinstance(exc, ToolResultValidationError):
        return SanitizedToolError(
            error=exc.error,
            error_code=exc.error_code,
            details=exc.details,
        )

    if isinstance(exc, asyncio.TimeoutError):
        return SanitizedToolError(error="Tool timed out", error_code="timeout")

    return SanitizedToolError(
        error="Tool execution failed",
        error_code="tool_execution_failed",
    )
