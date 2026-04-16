from __future__ import annotations

from collections.abc import Iterator

import pytest
from structlog.contextvars import clear_contextvars


@pytest.fixture(autouse=True)
def clear_structlog_contextvars() -> Iterator[None]:
    clear_contextvars()
    yield
    clear_contextvars()
