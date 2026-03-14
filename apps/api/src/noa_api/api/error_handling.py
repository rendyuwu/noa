from __future__ import annotations

import logging
from typing import Any, Mapping
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from noa_api.core.request_context import (
    get_request_id,
    reset_request_id,
    set_request_id,
)

REQUEST_ID_HEADER = "X-Request-Id"

logger = logging.getLogger(__name__)


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _resolve_request_id(Headers(scope=scope).get("x-request-id"))
        scope.setdefault("state", {})["request_id"] = request_id
        token = set_request_id(request_id)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            reset_request_id(token)


def install_error_handling(app: FastAPI) -> None:
    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(
        RequestValidationError, request_validation_exception_handler
    )
    app.add_exception_handler(Exception, unhandled_exception_handler)


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    detail, error_code = _unpack_error_detail(exc.detail)
    if error_code is None:
        error_code = _extract_header_error_code(exc.headers)
    return _json_error_response(
        status_code=exc.status_code,
        detail=detail,
        request=request,
        error_code=error_code,
        headers=exc.headers,
    )


async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return _json_error_response(
        status_code=422,
        detail=exc.errors(),
        request=request,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception", exc_info=exc)
    return _json_error_response(
        status_code=500,
        detail="Internal server error",
        request=request,
        error_code="internal_server_error",
    )


def _json_error_response(
    *,
    status_code: int,
    detail: Any,
    request: Request,
    error_code: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = _get_or_create_request_id(request)
    content: dict[str, Any] = {
        "detail": detail,
        "request_id": request_id,
    }
    if error_code is not None:
        content["error_code"] = error_code

    response_headers = dict(headers or {})
    response_headers[REQUEST_ID_HEADER] = request_id
    return JSONResponse(
        status_code=status_code,
        content=content,
        headers=response_headers,
    )


def _get_or_create_request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None) or get_request_id()
    if request_id is None:
        request_id = str(uuid4())
        request.state.request_id = request_id
    return request_id


def _resolve_request_id(inbound_request_id: str | None) -> str:
    if inbound_request_id is not None:
        inbound_request_id = inbound_request_id.strip()
        if inbound_request_id:
            return inbound_request_id
    return str(uuid4())


def _extract_header_error_code(headers: Mapping[str, str] | None) -> str | None:
    if headers is None:
        return None
    return headers.get("x-error-code") or headers.get("X-Error-Code")


def _unpack_error_detail(detail: Any) -> tuple[Any, str | None]:
    if isinstance(detail, dict):
        error_code = detail.get("error_code")
        if "detail" in detail:
            return detail["detail"], error_code if isinstance(error_code, str) else None
    return detail, None
