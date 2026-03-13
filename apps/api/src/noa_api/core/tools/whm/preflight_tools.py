from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.whm.server_ref import resolve_whm_server_ref
from noa_api.integrations.whm.client import WHMClient
from noa_api.integrations.whm.csf import parse_csf_grep_html
from noa_api.storage.postgres.whm_servers import SQLWHMServerRepository


def _resolution_error(result: Any) -> dict[str, object]:
    return {
        "ok": False,
        "error_code": str(getattr(result, "error_code", None) or "unknown"),
        "message": str(getattr(result, "message", "")),
        "choices": list(getattr(result, "choices", []) or []),
    }


async def whm_preflight_account(
    *,
    session: AsyncSession,
    server_ref: str,
    username: str,
) -> dict[str, object]:
    repo = SQLWHMServerRepository(session)
    resolution = await resolve_whm_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)

    server = resolution.server
    assert server is not None

    client = WHMClient(
        base_url=str(getattr(server, "base_url")),
        api_username=str(getattr(server, "api_username")),
        api_token=str(getattr(server, "api_token")),
        verify_ssl=bool(getattr(server, "verify_ssl")),
    )
    result = await client.list_accounts()
    if result.get("ok") is not True:
        return {
            "ok": False,
            "error_code": str(result.get("error_code") or "unknown"),
            "message": str(result.get("message") or "WHM list accounts failed"),
        }

    accounts = result.get("accounts")
    account_list = accounts if isinstance(accounts, list) else []
    normalized_username = username.strip()
    for account in account_list:
        if not isinstance(account, dict):
            continue
        user_value = account.get("user")
        if isinstance(user_value, str) and user_value == normalized_username:
            return {"ok": True, "account": account}

    return {
        "ok": False,
        "error_code": "account_not_found",
        "message": f"No WHM account found for username '{normalized_username}'",
    }


async def whm_preflight_csf_entries(
    *,
    session: AsyncSession,
    server_ref: str,
    target: str,
) -> dict[str, object]:
    repo = SQLWHMServerRepository(session)
    resolution = await resolve_whm_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)

    server = resolution.server
    assert server is not None
    normalized_target = target.strip()
    if not normalized_target:
        return {
            "ok": False,
            "error_code": "target_required",
            "message": "Target is required",
        }

    client = WHMClient(
        base_url=str(getattr(server, "base_url")),
        api_username=str(getattr(server, "api_username")),
        api_token=str(getattr(server, "api_token")),
        verify_ssl=bool(getattr(server, "verify_ssl")),
    )
    grep_result = await client.csf_grep(target=normalized_target)
    if grep_result.get("ok") is not True:
        return {
            "ok": False,
            "error_code": str(grep_result.get("error_code") or "unknown"),
            "message": str(grep_result.get("message") or "CSF grep failed"),
        }

    html_value = grep_result.get("html")
    if not isinstance(html_value, str):
        return {
            "ok": False,
            "error_code": "invalid_response",
            "message": "CSF grep returned an invalid response",
        }

    parsed = parse_csf_grep_html(html_value, target=normalized_target)
    return {
        "ok": True,
        "target": normalized_target,
        "verdict": parsed.verdict,
        "matches": parsed.matches,
    }
