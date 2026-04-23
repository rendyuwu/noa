from __future__ import annotations

from typing import Any

from noa_api.core.secrets.crypto import maybe_decrypt_text
from noa_api.core.secrets.redaction import redact_sensitive_data
from noa_api.proxmox.integrations.client import ProxmoxClient


def normalized_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def resolution_error(result: Any) -> dict[str, object]:
    return {
        "ok": False,
        "error_code": str(getattr(result, "error_code", None) or "unknown"),
        "message": str(getattr(result, "message", "")),
        "choices": list(getattr(result, "choices", []) or []),
    }


def client_for_server(server: Any) -> ProxmoxClient:
    return ProxmoxClient(
        base_url=str(getattr(server, "base_url")),
        api_token_id=str(getattr(server, "api_token_id")),
        api_token_secret=maybe_decrypt_text(str(getattr(server, "api_token_secret"))),
        verify_ssl=bool(getattr(server, "verify_ssl")),
    )


def upstream_error(
    result: dict[str, object], *, fallback_message: str
) -> dict[str, object]:
    return {
        "ok": False,
        "error_code": str(result.get("error_code") or "unknown"),
        "message": str(result.get("message") or fallback_message),
    }


def sanitize_proxmox_payload(payload: object) -> object:
    """Sanitize Proxmox payloads, redacting cipassword in both formats."""
    if isinstance(payload, list):
        return [sanitize_proxmox_payload(item) for item in payload]
    if isinstance(payload, dict):
        lowered_key = payload.get("key")
        if isinstance(lowered_key, str) and lowered_key.strip().lower() == "cipassword":
            return {
                str(key): (
                    "[redacted]"
                    if str(key).strip().lower() == "value"
                    else sanitize_proxmox_payload(item)
                )
                for key, item in payload.items()
            }
        return {
            str(key): sanitize_proxmox_payload(item)
            if isinstance(item, (dict, list))
            else ("[redacted]" if str(key).strip().lower() == "cipassword" else item)
            for key, item in payload.items()
        }
    return redact_sensitive_data(payload)


def validate_vmid(vmid: object) -> dict[str, object] | None:
    """Return an error dict if vmid is invalid, None if valid."""
    if isinstance(vmid, bool) or not isinstance(vmid, int) or vmid < 1:
        return {
            "ok": False,
            "error_code": "invalid_request",
            "message": "vmid must be a positive integer",
        }
    return None
