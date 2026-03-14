from __future__ import annotations

import io
import json
import logging

import structlog
from fastapi import Response
from httpx import ASGITransport, AsyncClient

from noa_api.core.logging import configure_logging
from noa_api.core.logging_context import log_context
from noa_api.main import create_app


async def test_health_includes_x_request_id_header() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.headers.get("x-request-id")


async def test_inbound_x_request_id_is_preserved() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/health", headers={"x-request-id": "req-from-client"}
        )

    assert response.status_code == 200
    assert response.headers.get("x-request-id") == "req-from-client"


async def test_uncaught_exception_returns_safe_500_envelope_with_request_id() -> None:
    app = create_app()

    @app.get("/_tests/error")
    async def error_route() -> Response:
        raise RuntimeError("boom")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_tests/error")

    assert response.status_code == 500
    request_id = response.headers.get("x-request-id")
    assert request_id
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "detail": "Internal server error",
        "error_code": "internal_server_error",
        "request_id": request_id,
    }


def test_create_app_preserves_existing_root_logging_configuration() -> None:
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level
    original_formatters = {
        id(handler): handler.formatter for handler in original_handlers
    }
    sentinel = logging.StreamHandler()
    sentinel.setLevel(logging.ERROR)
    sentinel_formatter = logging.Formatter("sentinel %(message)s")
    sentinel.setFormatter(sentinel_formatter)
    root_logger.handlers = [sentinel]
    root_logger.setLevel(logging.WARNING)

    try:
        create_app()
        create_app()

        assert root_logger.handlers == [sentinel]
        assert root_logger.level == logging.WARNING
        assert isinstance(sentinel.formatter, structlog.stdlib.ProcessorFormatter)
    finally:
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_level)
        for handler in original_handlers:
            handler.setFormatter(original_formatters[id(handler)])


def test_configure_logging_formats_existing_root_handlers_with_structlog() -> None:
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level
    original_formatters = {
        id(handler): handler.formatter for handler in original_handlers
    }
    stream = io.StringIO()
    sentinel = logging.StreamHandler(stream)
    sentinel.setFormatter(logging.Formatter("sentinel %(message)s"))
    root_logger.handlers = [sentinel]
    root_logger.setLevel(logging.WARNING)

    try:
        configure_logging()

        with log_context(request_method="GET", request_path="/health"):
            logging.getLogger("tests.logging").warning(
                "api_request_completed",
                extra={"status_code": 200},
            )

        assert root_logger.handlers == [sentinel]
        assert root_logger.level == logging.WARNING
        assert isinstance(sentinel.formatter, structlog.stdlib.ProcessorFormatter)

        rendered = stream.getvalue().strip()
        payload = json.loads(rendered)
        assert payload["event"] == "api_request_completed"
        assert payload["request_method"] == "GET"
        assert payload["request_path"] == "/health"
        assert payload["status_code"] == 200
    finally:
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_level)
        for handler in original_handlers:
            handler.setFormatter(original_formatters[id(handler)])
