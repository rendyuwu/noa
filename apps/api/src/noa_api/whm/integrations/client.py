from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import httpx


PrimitiveQueryValue = str | int | float | bool
QueryValue = PrimitiveQueryValue | Sequence[PrimitiveQueryValue]


def _coerce_query_params(params: Mapping[str, object]) -> dict[str, QueryValue]:
    normalized: dict[str, QueryValue] = {}
    for key, value in params.items():
        if isinstance(value, (str, int, float, bool)):
            normalized[key] = value
            continue
        if isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            normalized[key] = [
                item if isinstance(item, (str, int, float, bool)) else str(item)
                for item in value
            ]
            continue
        normalized[key] = str(value)
    return normalized


class WHMClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_username: str,
        api_token: str,
        verify_ssl: bool,
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_username = api_username
        self._api_token = api_token
        self._verify_ssl = verify_ssl
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"whm {self._api_username}:{self._api_token}",
            "Accept": "application/json",
        }

    async def _get_json_api(
        self, command: str, *, params: Mapping[str, object] | None = None
    ) -> dict[str, object]:
        merged_params: dict[str, object] = {"api.version": 1}
        if params is not None:
            merged_params.update(dict(params))

        url = f"{self._base_url}/json-api/{command}"
        try:
            async with httpx.AsyncClient(
                verify=self._verify_ssl,
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    url,
                    params=_coerce_query_params(merged_params),
                    headers=self._headers(),
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
                "message": "WHM authentication failed",
            }

        if response.status_code >= 400:
            return {
                "ok": False,
                "error_code": "http_error",
                "message": f"WHM returned HTTP {response.status_code}",
            }

        try:
            payload = response.json()
        except ValueError:
            return {
                "ok": False,
                "error_code": "invalid_response",
                "message": "WHM returned a non-JSON response",
            }

        if not isinstance(payload, dict):
            return {
                "ok": False,
                "error_code": "invalid_response",
                "message": "WHM returned an unexpected response shape",
            }

        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            return {
                "ok": False,
                "error_code": "invalid_response",
                "message": "WHM response missing metadata",
            }

        result = metadata.get("result")
        if result != 1:
            reason = metadata.get("reason")
            message = (
                str(reason)
                if isinstance(reason, str) and reason.strip()
                else "WHM API error"
            )
            return {
                "ok": False,
                "error_code": "whm_api_error",
                "message": message,
            }

        return {
            "ok": True,
            "message": "ok",
            "data": payload.get("data"),
            "metadata": metadata,
        }

    async def _get_text(
        self, path: str, *, params: Mapping[str, object] | None = None
    ) -> dict[str, object]:
        url = f"{self._base_url}/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(
                verify=self._verify_ssl,
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    url,
                    params=_coerce_query_params(dict(params or {})),
                    headers=self._headers(),
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
                "message": "WHM authentication failed",
            }

        if response.status_code >= 400:
            return {
                "ok": False,
                "error_code": "http_error",
                "message": f"WHM returned HTTP {response.status_code}",
            }

        return {"ok": True, "message": "ok", "html": response.text}

    async def applist(self) -> dict[str, object]:
        return await self._get_json_api("applist")

    async def list_accounts(self) -> dict[str, object]:
        result = await self._get_json_api("listaccts")
        if result.get("ok") is not True:
            return result
        data = result.get("data")
        accounts: list[dict[str, Any]] = []
        if isinstance(data, dict):
            acct = data.get("acct")
            if isinstance(acct, list):
                accounts = [a for a in acct if isinstance(a, dict)]
        return {"ok": True, "message": "ok", "accounts": accounts}

    async def suspend_account(self, *, username: str, reason: str) -> dict[str, object]:
        result = await self._get_json_api(
            "suspendacct", params={"user": username, "reason": reason}
        )
        if result.get("ok") is not True:
            return result
        return {"ok": True, "message": "ok"}

    async def unsuspend_account(self, *, username: str) -> dict[str, object]:
        result = await self._get_json_api("unsuspendacct", params={"user": username})
        if result.get("ok") is not True:
            return result
        return {"ok": True, "message": "ok"}

    async def change_contact_email(
        self, *, username: str, email: str
    ) -> dict[str, object]:
        result = await self._get_json_api(
            "modifyacct",
            params={"user": username, "contactemail": email},
        )
        if result.get("ok") is not True:
            return result
        return {"ok": True, "message": "ok"}

    async def csf_grep(self, *, target: str) -> dict[str, object]:
        return await self._get_text(
            "cgi/addon_csf.cgi",
            params={"action": "grepip", "ip": target},
        )

    async def csf_request_action(
        self, *, action: str, params: Mapping[str, object]
    ) -> dict[str, object]:
        merged: dict[str, object] = {"action": action}
        merged.update(dict(params))
        return await self._get_text("cgi/addon_csf.cgi", params=merged)
