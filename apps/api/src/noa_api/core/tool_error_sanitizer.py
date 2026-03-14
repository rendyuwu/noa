from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SanitizedToolError:
    error: str
    error_code: str

    def as_result(self) -> dict[str, str]:
        return {
            "error": self.error,
            "error_code": self.error_code,
        }


def sanitize_tool_error(exc: Exception) -> SanitizedToolError:
    if isinstance(exc, asyncio.TimeoutError):
        return SanitizedToolError(error="Tool timed out", error_code="timeout")

    return SanitizedToolError(
        error="Tool execution failed",
        error_code="tool_execution_failed",
    )
