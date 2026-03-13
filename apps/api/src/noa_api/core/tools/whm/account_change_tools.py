from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.whm.server_ref import resolve_whm_server_ref
from noa_api.integrations.whm.client import WHMClient
from noa_api.storage.postgres.whm_servers import SQLWHMServerRepository


def _resolution_error(result: Any) -> dict[str, object]:
    return {
        "ok": False,
        "error_code": str(getattr(result, "error_code", None) or "unknown"),
        "message": str(getattr(result, "message", "")),
        "choices": list(getattr(result, "choices", []) or []),
    }


def _is_suspended(account: dict[str, object]) -> bool:
    value = account.get("suspended")
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def _account_email(account: dict[str, object]) -> str | None:
    value = account.get("email")
    if isinstance(value, str) and value.strip():
        return value.strip()
    contact = account.get("contactemail")
    if isinstance(contact, str) and contact.strip():
        return contact.strip()
    return None


async def _get_account(
    client: Any, *, username: str
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    result = await client.list_accounts()
    if result.get("ok") is not True:
        return None, {
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
        if account.get("user") == normalized_username:
            return account, None
    return None, {
        "ok": False,
        "error_code": "account_not_found",
        "message": f"No WHM account found for username '{normalized_username}'",
    }


async def whm_suspend_account(
    *,
    session: AsyncSession,
    server_ref: str,
    username: str,
    reason: str,
) -> dict[str, object]:
    if not reason.strip():
        return {
            "ok": False,
            "error_code": "reason_required",
            "message": "Reason is required",
        }

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

    account, error = await _get_account(client, username=username)
    if error is not None:
        return error
    assert account is not None

    if _is_suspended(account):
        return {
            "ok": True,
            "status": "no-op",
            "message": "Account is already suspended",
        }

    mutation = await client.suspend_account(
        username=username.strip(), reason=reason.strip()
    )
    if mutation.get("ok") is not True:
        return {
            "ok": False,
            "error_code": str(mutation.get("error_code") or "unknown"),
            "message": str(mutation.get("message") or "WHM suspend failed"),
        }

    post_account, post_error = await _get_account(client, username=username)
    if post_error is not None or post_account is None:
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Unable to verify account state after suspend",
        }
    if not _is_suspended(post_account):
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Account did not become suspended",
        }

    return {"ok": True, "status": "changed", "message": "Account suspended"}


async def whm_unsuspend_account(
    *,
    session: AsyncSession,
    server_ref: str,
    username: str,
    reason: str,
) -> dict[str, object]:
    if not reason.strip():
        return {
            "ok": False,
            "error_code": "reason_required",
            "message": "Reason is required",
        }

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

    account, error = await _get_account(client, username=username)
    if error is not None:
        return error
    assert account is not None

    if not _is_suspended(account):
        return {
            "ok": True,
            "status": "no-op",
            "message": "Account is not suspended",
        }

    mutation = await client.unsuspend_account(username=username.strip())
    if mutation.get("ok") is not True:
        return {
            "ok": False,
            "error_code": str(mutation.get("error_code") or "unknown"),
            "message": str(mutation.get("message") or "WHM unsuspend failed"),
        }

    post_account, post_error = await _get_account(client, username=username)
    if post_error is not None or post_account is None:
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Unable to verify account state after unsuspend",
        }
    if _is_suspended(post_account):
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Account did not become unsuspended",
        }

    return {"ok": True, "status": "changed", "message": "Account unsuspended"}


async def whm_change_contact_email(
    *,
    session: AsyncSession,
    server_ref: str,
    username: str,
    new_email: str,
    reason: str,
) -> dict[str, object]:
    if not reason.strip():
        return {
            "ok": False,
            "error_code": "reason_required",
            "message": "Reason is required",
        }

    normalized_email = new_email.strip()
    if not normalized_email:
        return {
            "ok": False,
            "error_code": "email_required",
            "message": "New email is required",
        }

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

    account, error = await _get_account(client, username=username)
    if error is not None:
        return error
    assert account is not None

    current_email = _account_email(account)
    if current_email is not None and current_email.lower() == normalized_email.lower():
        return {
            "ok": True,
            "status": "no-op",
            "message": "Contact email already matches",
        }

    mutation = await client.change_contact_email(
        username=username.strip(),
        email=normalized_email,
    )
    if mutation.get("ok") is not True:
        return {
            "ok": False,
            "error_code": str(mutation.get("error_code") or "unknown"),
            "message": str(
                mutation.get("message") or "WHM change contact email failed"
            ),
        }

    post_account, post_error = await _get_account(client, username=username)
    if post_error is not None or post_account is None:
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Unable to verify contact email after change",
        }
    post_email = _account_email(post_account)
    if post_email is None or post_email.lower() != normalized_email.lower():
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Contact email did not update",
        }

    return {
        "ok": True,
        "status": "changed",
        "message": "Contact email updated",
    }
