"""Shared server name and base URL validation for WHM + Proxmox admin routes (V49, V50)."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9._:-]+$")


def validate_server_name(value: str, *, label: str = "server") -> str:
    """Validate server name matches ``[A-Za-z0-9._:-]+``.

    *label* is inserted into the error message (e.g. ``"WHM"`` or ``"Proxmox"``).
    """
    if not _SERVER_NAME_RE.fullmatch(value):
        raise ValueError(f"String should be a valid {label} server name")
    return value


def normalize_https_base_url(value: str, *, label: str = "server") -> str:
    """Validate and normalize an HTTPS base URL.

    Returns ``https://<hostname>[:<port>]`` with path/query/fragment stripped.
    *label* is inserted into the error message.
    """
    error_msg = f"String should be a valid HTTPS {label} base URL"
    parsed = urlsplit(value)
    if parsed.scheme != "https":
        raise ValueError(error_msg)
    if not parsed.hostname:
        raise ValueError(error_msg)
    if parsed.username or parsed.password:
        raise ValueError(error_msg)
    if parsed.path not in {"", "/"}:
        raise ValueError(error_msg)
    if parsed.query or parsed.fragment:
        raise ValueError(error_msg)

    hostname = parsed.hostname
    assert hostname is not None
    try:
        port_value = parsed.port
    except ValueError as exc:
        raise ValueError(error_msg) from exc
    port = f":{port_value}" if port_value is not None else ""
    return f"https://{hostname}{port}"
