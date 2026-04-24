from __future__ import annotations

import logging
from typing import Any

import structlog
from structlog.contextvars import merge_contextvars

from noa_api.core.request_context import get_request_id
from noa_api.core.secrets.redaction import redact_sensitive_data


def _redact_event_dict(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Structlog processor: redact values whose key matches sensitive patterns.

    Delegates to the shared ``redact_sensitive_data`` helper so the key-set
    and recursion logic stay in one place.
    """
    _ = logger, method_name
    redacted = redact_sensitive_data(event_dict)
    assert isinstance(redacted, dict)
    return redacted


def configure_logging() -> None:
    shared_processors = [
        merge_contextvars,
        _add_request_context,
        structlog.stdlib.ExtraAdder(),
        _redact_event_dict,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)
    else:
        for handler in root_logger.handlers:
            if not isinstance(handler.formatter, structlog.stdlib.ProcessorFormatter):
                handler.setFormatter(formatter)

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def _add_request_context(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    _ = logger, method_name
    request_id = get_request_id()
    if request_id is not None and "request_id" not in event_dict:
        event_dict["request_id"] = request_id
    return event_dict
