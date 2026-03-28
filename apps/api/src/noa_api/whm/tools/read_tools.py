from __future__ import annotations

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.remote_exec.ssh import SSHExecutionError, ssh_exec
from noa_api.core.secrets.crypto import maybe_decrypt_text
from noa_api.storage.postgres.whm_servers import SQLWHMServerRepository
from noa_api.whm.integrations.client import WHMClient
from noa_api.whm.integrations.ssh import resolve_whm_ssh_config
from noa_api.whm.server_ref import resolve_whm_server_ref
from noa_api.whm.tools.result_shapes import normalize_whm_account_summary

_BINARY_NAME_RE = re.compile(r"^[A-Za-z0-9._+-]{1,128}$")


def _resolution_error(result: Any) -> dict[str, object]:
    return {
        "ok": False,
        "error_code": str(getattr(result, "error_code", None) or "unknown"),
        "message": str(getattr(result, "message", "")),
        "choices": list(getattr(result, "choices", []) or []),
    }


def _client_for_server(server: Any) -> WHMClient:
    return WHMClient(
        base_url=str(getattr(server, "base_url")),
        api_username=str(getattr(server, "api_username")),
        api_token=maybe_decrypt_text(str(getattr(server, "api_token"))),
        verify_ssl=bool(getattr(server, "verify_ssl")),
    )


async def whm_list_servers(*, session: AsyncSession) -> dict[str, object]:
    repo = SQLWHMServerRepository(session)
    servers = await repo.list_servers()
    return {
        "ok": True,
        "servers": [server.to_safe_dict() for server in servers],
    }


async def whm_validate_server(
    *, session: AsyncSession, server_ref: str
) -> dict[str, object]:
    repo: Any = SQLWHMServerRepository(session)
    resolution = await resolve_whm_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)

    server = resolution.server
    assert server is not None

    client = _client_for_server(server)
    result = await client.applist()
    if result.get("ok") is True:
        return {"ok": True, "message": "ok"}
    return {
        "ok": False,
        "error_code": str(result.get("error_code") or "unknown"),
        "message": str(result.get("message") or "WHM validation failed"),
    }


async def whm_list_accounts(
    *, session: AsyncSession, server_ref: str
) -> dict[str, object]:
    repo: Any = SQLWHMServerRepository(session)
    resolution = await resolve_whm_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)

    server = resolution.server
    assert server is not None
    client = _client_for_server(server)
    result = await client.list_accounts()
    if result.get("ok") is not True:
        return {
            "ok": False,
            "error_code": str(result.get("error_code") or "unknown"),
            "message": str(result.get("message") or "WHM list accounts failed"),
        }
    accounts = result.get("accounts")
    return {
        "ok": True,
        "accounts": _normalize_account_list(accounts),
    }


async def whm_search_accounts(
    *,
    session: AsyncSession,
    server_ref: str,
    query: str,
    limit: int = 20,
) -> dict[str, object]:
    normalized_query = query.strip().lower()
    if not normalized_query:
        return {
            "ok": False,
            "error_code": "query_required",
            "message": "Query is required",
        }
    if limit <= 0:
        return {
            "ok": False,
            "error_code": "limit_invalid",
            "message": "Limit must be a positive integer",
        }

    listed = await whm_list_accounts(session=session, server_ref=server_ref)
    if listed.get("ok") is not True:
        return listed

    raw_accounts = listed.get("accounts")
    accounts = raw_accounts if isinstance(raw_accounts, list) else []

    matches: list[dict[str, Any]] = []
    for account in accounts:
        if not isinstance(account, dict):
            continue
        user = account.get("user")
        domain = account.get("domain")
        haystack = " ".join(
            [
                str(user) if user is not None else "",
                str(domain) if domain is not None else "",
            ]
        ).lower()
        if normalized_query in haystack:
            matches.append(account)
        if limit > 0 and len(matches) >= limit:
            break

    return {"ok": True, "accounts": matches, "query": query}


async def whm_check_binary_exists(
    *, session: AsyncSession, server_ref: str, binary_name: str
) -> dict[str, object]:
    normalized_binary_name = binary_name.strip()
    if not _BINARY_NAME_RE.fullmatch(normalized_binary_name):
        return {
            "ok": False,
            "error_code": "invalid_binary_name",
            "message": "Binary name must contain only letters, numbers, dot, underscore, plus, or dash",
        }

    repo: Any = SQLWHMServerRepository(session)
    resolution = await resolve_whm_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)

    server = resolution.server
    assert server is not None

    try:
        ssh_config = resolve_whm_ssh_config(
            server,
            require_host_key_fingerprint=True,
        )
        result = await ssh_exec(
            ssh_config,
            command=f"command -v {normalized_binary_name}",
        )
    except SSHExecutionError as exc:
        return {
            "ok": False,
            "error_code": exc.code,
            "message": exc.message,
        }

    if result.exit_code != 0:
        return {
            "ok": True,
            "binary_name": normalized_binary_name,
            "found": False,
            "path": None,
        }

    path = result.stdout.strip().splitlines()[0] if result.stdout.strip() else None
    return {
        "ok": True,
        "binary_name": normalized_binary_name,
        "found": path is not None,
        "path": path,
    }


def _normalize_account_list(accounts: object) -> list[dict[str, object]]:
    if not isinstance(accounts, list):
        return []

    normalized_accounts: list[dict[str, object]] = []
    for account in accounts:
        normalized = normalize_whm_account_summary(account)
        if normalized is not None:
            normalized_accounts.append(normalized)
    return normalized_accounts
