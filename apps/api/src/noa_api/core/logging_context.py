from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from structlog.contextvars import bind_contextvars, reset_contextvars


def bind_log_context(**values: Any) -> dict[str, Any]:
    filtered = {key: value for key, value in values.items() if value is not None}
    if not filtered:
        return {}
    return bind_contextvars(**filtered)


@contextmanager
def log_context(**values: Any) -> Iterator[None]:
    tokens = bind_log_context(**values)
    try:
        yield
    finally:
        if tokens:
            reset_contextvars(**tokens)
