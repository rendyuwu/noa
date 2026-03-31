from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.secrets.crypto import maybe_decrypt_text
from noa_api.storage.postgres.whm_servers import SQLWHMServerRepository
from noa_api.whm.integrations.client import WHMClient
from noa_api.whm.server_ref import resolve_whm_server_ref
from noa_api.whm.tools.preflight_tools import collect_primary_domain_change_state


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


def _account_domain(account: dict[str, object]) -> str | None:
    value = account.get("domain")
    if not isinstance(value, str):
        return None
    normalized = value.strip().rstrip(".").lower()
    return normalized or None


def _client_for_server(server: Any) -> WHMClient:
    return WHMClient(
        base_url=str(getattr(server, "base_url")),
        api_username=str(getattr(server, "api_username")),
        api_token=maybe_decrypt_text(str(getattr(server, "api_token"))),
        verify_ssl=bool(getattr(server, "verify_ssl")),
    )


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
    client = _client_for_server(server)

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
    client = _client_for_server(server)

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
    client = _client_for_server(server)

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


async def whm_change_primary_domain(
    *,
    session: AsyncSession,
    server_ref: str,
    username: str,
    new_domain: str,
    reason: str,
) -> dict[str, object]:
    if not reason.strip():
        return {
            "ok": False,
            "error_code": "reason_required",
            "message": "Reason is required",
        }

    normalized_domain = new_domain.strip().rstrip(".").lower()
    if not normalized_domain:
        return {
            "ok": False,
            "error_code": "domain_required",
            "message": "New domain is required",
        }

    preflight = await collect_primary_domain_change_state(
        session=session,
        server_ref=server_ref,
        username=username,
        new_domain=normalized_domain,
        check_dns_zone=False,
    )
    if preflight.get("ok") is not True:
        return preflight

    account = preflight.get("account")
    if not isinstance(account, dict):
        return {
            "ok": False,
            "error_code": "invalid_response",
            "message": "WHM returned an unexpected account payload",
        }

    current_domain = _account_domain(account)
    if current_domain == normalized_domain:
        return {
            "ok": True,
            "status": "no-op",
            "message": "Primary domain already matches",
        }

    repo = SQLWHMServerRepository(session)
    resolution = await resolve_whm_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)

    server = resolution.server
    assert server is not None
    client = _client_for_server(server)

    mutation = await client.change_primary_domain(
        username=username.strip(),
        domain=normalized_domain,
    )
    if mutation.get("ok") is not True:
        return {
            "ok": False,
            "error_code": str(mutation.get("error_code") or "unknown"),
            "message": str(
                mutation.get("message") or "WHM change primary domain failed"
            ),
        }

    postflight = await collect_primary_domain_change_state(
        session=session,
        server_ref=server_ref,
        username=username,
        new_domain=normalized_domain,
        check_dns_zone=True,
    )
    if postflight.get("ok") is not True:
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Unable to verify primary domain after change",
        }

    post_account = postflight.get("account")
    if not isinstance(post_account, dict):
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Unable to verify primary domain after change",
        }

    post_domain = _account_domain(post_account)
    dns_zone_exists = postflight.get("dns_zone_exists") is True
    if post_domain != normalized_domain:
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Primary domain did not update",
        }
    if not dns_zone_exists:
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": f"DNS zone for '{normalized_domain}' was not found after the change",
        }

    return {
        "ok": True,
        "status": "changed",
        "message": "Primary domain updated",
    }
