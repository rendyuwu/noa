from __future__ import annotations

from collections.abc import Mapping

import httpx


def _normalized_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _render_error_detail(value: object) -> str | None:
    if isinstance(value, str):
        return _normalized_text(value)
    if isinstance(value, list):
        parts = [_render_error_detail(item) for item in value]
        joined = "; ".join(part for part in parts if part is not None)
        return joined or None
    if isinstance(value, dict):
        parts: list[str] = []
        for key, item in value.items():
            item_text = _render_error_detail(item)
            if item_text is None:
                continue
            parts.append(f"{key}: {item_text}")
        joined = "; ".join(parts)
        return joined or None
    return _normalized_text(str(value)) if value is not None else None


def _is_digest_error(*, message: str | None, errors: object | None = None) -> bool:
    message_text = (message or "").lower()
    if "digest" in message_text and (
        "mismatch" in message_text
        or "modified" in message_text
        or "changed" in message_text
    ):
        return True

    if isinstance(errors, dict):
        for key, value in errors.items():
            if not isinstance(key, str):
                continue
            if key.lower() == "digest":
                return True
            value_text = _render_error_detail(value)
            if value_text is None:
                continue
            if _is_digest_error(message=value_text, errors=None):
                return True
    return False


def _payload_error(
    payload: Mapping[str, object], *, status_code: int
) -> dict[str, object] | None:
    error_sources: list[tuple[object | None, str | None]] = []
    error_sources.append(
        (payload.get("errors"), _normalized_text(payload.get("message")))
    )

    data = payload.get("data")
    if isinstance(data, dict):
        error_sources.append(
            (data.get("errors"), _normalized_text(data.get("message")))
        )

    for errors, message in error_sources:
        detail = _render_error_detail(errors)
        if detail is None and message is None:
            continue
        rendered_message = detail or message or "Proxmox API error"
        error_code = (
            "digest_mismatch"
            if _is_digest_error(message=rendered_message, errors=errors)
            else "proxmox_api_error"
        )
        return {
            "ok": False,
            "error_code": error_code,
            "message": rendered_message,
        }

    if status_code >= 400:
        message = _normalized_text(payload.get("message"))
        if message is not None:
            error_code = (
                "digest_mismatch"
                if _is_digest_error(message=message, errors=None)
                else "http_error"
            )
            return {
                "ok": False,
                "error_code": error_code,
                "message": message,
            }

    return None


class ProxmoxClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_token_id: str,
        api_token_secret: str,
        verify_ssl: bool,
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_token_id = api_token_id
        self._api_token_secret = api_token_secret
        self._verify_ssl = verify_ssl
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": (
                f"PVEAPIToken={self._api_token_id}={self._api_token_secret}"
            ),
            "Accept": "application/json",
        }

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        form_data: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        url = f"{self._base_url}/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(
                verify=self._verify_ssl,
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.request(
                    method,
                    url,
                    headers=self._headers(),
                    data=dict(form_data or {}),
                )
        except httpx.TimeoutException:
            return {
                "ok": False,
                "error_code": "timeout",
                "message": "Request timed out",
            }
        except httpx.RequestError as exc:
            return {
                "ok": False,
                "error_code": "request_failed",
                "message": f"Request failed: {exc}",
            }

        if response.status_code in {401, 403}:
            return {
                "ok": False,
                "error_code": "auth_failed",
                "message": "Proxmox authentication failed",
            }

        try:
            payload = response.json()
        except ValueError:
            if response.status_code >= 400:
                return {
                    "ok": False,
                    "error_code": "http_error",
                    "message": f"Proxmox returned HTTP {response.status_code}",
                }
            return {
                "ok": False,
                "error_code": "invalid_response",
                "message": "Proxmox returned a non-JSON response",
            }

        if not isinstance(payload, dict):
            return {
                "ok": False,
                "error_code": "invalid_response",
                "message": "Proxmox returned an unexpected response shape",
            }

        payload_error = _payload_error(payload, status_code=response.status_code)
        if payload_error is not None:
            return payload_error

        if response.status_code >= 400:
            return {
                "ok": False,
                "error_code": "http_error",
                "message": f"Proxmox returned HTTP {response.status_code}",
            }

        return {
            "ok": True,
            "message": "ok",
            "data": payload.get("data"),
        }

    async def get_version(self) -> dict[str, object]:
        return await self._request_json("GET", "/api2/json/version")

    async def get_qemu_config(self, node: str, vmid: int) -> dict[str, object]:
        result = await self._request_json(
            "GET",
            f"/api2/json/nodes/{node}/qemu/{vmid}/config",
        )
        if result.get("ok") is not True:
            return result

        data = result.get("data")
        if not isinstance(data, dict):
            return {
                "ok": False,
                "error_code": "invalid_response",
                "message": "Proxmox returned an unexpected QEMU config payload",
            }

        digest = _normalized_text(data.get("digest"))
        if digest is None:
            return {
                "ok": False,
                "error_code": "invalid_response",
                "message": "Proxmox QEMU config payload is missing a digest",
            }

        return {
            "ok": True,
            "message": "ok",
            "config": data,
            "digest": digest,
        }

    async def update_qemu_config(
        self,
        node: str,
        vmid: int,
        *,
        digest: str,
        net_key: str,
        net_value: str,
    ) -> dict[str, object]:
        result = await self._request_json(
            "POST",
            f"/api2/json/nodes/{node}/qemu/{vmid}/config",
            form_data={
                "digest": digest,
                net_key: net_value,
            },
        )
        if result.get("ok") is not True:
            return result

        upid = _normalized_text(result.get("data"))
        if upid is None:
            return {
                "ok": False,
                "error_code": "invalid_response",
                "message": "Proxmox returned an unexpected task identifier",
            }

        return {
            "ok": True,
            "message": "ok",
            "upid": upid,
        }

    async def get_task_status(self, node: str, upid: str) -> dict[str, object]:
        result = await self._request_json(
            "GET",
            f"/api2/json/nodes/{node}/tasks/{upid}/status",
        )
        if result.get("ok") is not True:
            return result

        data = result.get("data")
        if not isinstance(data, dict):
            return {
                "ok": False,
                "error_code": "invalid_response",
                "message": "Proxmox returned an unexpected task status payload",
            }

        return {
            "ok": True,
            "message": "ok",
            "upid": upid,
            "task_status": _normalized_text(data.get("status")),
            "task_exit_status": _normalized_text(data.get("exitstatus")),
            "data": data,
        }
