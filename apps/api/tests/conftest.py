from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from structlog.contextvars import clear_contextvars


os.environ.setdefault("LLM_API_KEY", "test-llm-api-key")


@pytest.fixture(autouse=True)
def clear_structlog_contextvars() -> Iterator[None]:
    clear_contextvars()
    yield
    clear_contextvars()
