from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.secrets.crypto import maybe_decrypt_text
from noa_api.storage.postgres.whm_servers import SQLWHMServerRepository
from noa_api.whm.integrations.client import WHMClient
from noa_api.whm.server_ref import resolve_whm_server_ref
from noa_api.whm.tools.result_shapes import normalize_whm_account_summary


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


def _normalize_domain(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().rstrip(".").lower()
    return normalized or None


def _normalize_domain_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []

    normalized: list[str] = []
    for item in value:
        domain = _normalize_domain(item)
        if domain is not None:
            normalized.append(domain)
    return normalized


def _location_for_domain(*, domain: str, inventory: dict[str, object]) -> str:
    main_domain = _normalize_domain(inventory.get("main_domain"))
    if main_domain == domain:
        return "primary"
    if domain in _normalize_domain_list(inventory.get("addon_domains")):
        return "addon"
    if domain in _normalize_domain_list(inventory.get("parked_domains")):
        return "parked"
    if domain in _normalize_domain_list(inventory.get("sub_domains")):
        return "subdomain"
    return "absent"


def _matching_account(*, accounts: object, username: str) -> dict[str, object] | None:
    account_list = accounts if isinstance(accounts, list) else []
    normalized_username = username.strip()
    for account in account_list:
        if not isinstance(account, dict):
            continue
        user_value = account.get("user")
        if isinstance(user_value, str) and user_value == normalized_username:
            return account
    return None


async def collect_primary_domain_change_state(
    *,
    session: AsyncSession,
    server_ref: str,
    username: str,
    new_domain: str,
    check_dns_zone: bool = False,
) -> dict[str, object]:
    normalized_username = username.strip()
    requested_domain = _normalize_domain(new_domain)
    if requested_domain is None:
        return {
            "ok": False,
            "error_code": "domain_required",
            "message": "New domain is required",
        }

    repo: Any = SQLWHMServerRepository(session)
    resolution = await resolve_whm_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)

    server = resolution.server
    assert server is not None

    client = _client_for_server(server)
    accounts_result = await client.list_accounts()
    if accounts_result.get("ok") is not True:
        return {
            "ok": False,
            "error_code": str(accounts_result.get("error_code") or "unknown"),
            "message": str(
                accounts_result.get("message") or "WHM list accounts failed"
            ),
        }

    account = _matching_account(
        accounts=accounts_result.get("accounts"),
        username=normalized_username,
    )
    if account is None:
        return {
            "ok": False,
            "error_code": "account_not_found",
            "message": f"No WHM account found for username '{normalized_username}'",
        }

    normalized_account = normalize_whm_account_summary(account)
    if normalized_account is None:
        return {
            "ok": False,
            "error_code": "invalid_response",
            "message": "WHM returned an unexpected account payload",
        }

    owner_result = await client.get_domain_owner(domain=requested_domain)
    if owner_result.get("ok") is not True:
        return {
            "ok": False,
            "error_code": str(owner_result.get("error_code") or "unknown"),
            "message": str(
                owner_result.get("message") or "WHM domain owner lookup failed"
            ),
        }

    domain_owner = owner_result.get("owner")
    domain_owner_name = domain_owner if isinstance(domain_owner, str) else None
    if domain_owner_name is not None and domain_owner_name != normalized_username:
        return {
            "ok": False,
            "error_code": "domain_owned_by_another_account",
            "message": (
                f"Domain '{requested_domain}' is already owned by WHM account '{domain_owner_name}'"
            ),
        }

    domain_result = await client.list_domains_for_account(username=normalized_username)
    if domain_result.get("ok") is not True:
        return {
            "ok": False,
            "error_code": str(domain_result.get("error_code") or "unknown"),
            "message": str(
                domain_result.get("message")
                or "WHM account domain inventory lookup failed"
            ),
        }

    raw_inventory = domain_result.get("domains")
    if not isinstance(raw_inventory, dict):
        return {
            "ok": False,
            "error_code": "invalid_response",
            "message": "WHM returned an unexpected account domain inventory",
        }

    inventory = {
        "main_domain": _normalize_domain(raw_inventory.get("main_domain")),
        "addon_domains": _normalize_domain_list(raw_inventory.get("addon_domains")),
        "parked_domains": _normalize_domain_list(raw_inventory.get("parked_domains")),
        "sub_domains": _normalize_domain_list(raw_inventory.get("sub_domains")),
    }
    location = _location_for_domain(domain=requested_domain, inventory=inventory)

    blocked_messages = {
        "addon": "already exists as an addon domain",
        "parked": "already exists as a parked domain",
        "subdomain": "already exists as a subdomain",
    }
    if location in blocked_messages:
        return {
            "ok": False,
            "error_code": f"domain_is_{location}",
            "message": (
                f"Domain '{requested_domain}' {blocked_messages[location]} on WHM account '{normalized_username}'"
            ),
        }

    result: dict[str, object] = {
        "ok": True,
        "server_id": str(resolution.server_id),
        "account": normalized_account,
        "requested_domain": requested_domain,
        "domain_owner": domain_owner_name,
        "requested_domain_location": location,
        "safe_to_change": location in {"absent", "primary"},
        "domain_inventory": inventory,
    }

    if check_dns_zone:
        zones_result = await client.list_zones()
        if zones_result.get("ok") is not True:
            return {
                "ok": False,
                "error_code": str(zones_result.get("error_code") or "unknown"),
                "message": str(
                    zones_result.get("message") or "WHM DNS zone lookup failed"
                ),
            }

        raw_zones = zones_result.get("zones")
        zones = raw_zones if isinstance(raw_zones, list) else []
        result["dns_zone_exists"] = any(
            isinstance(zone, dict)
            and _normalize_domain(zone.get("domain")) == requested_domain
            for zone in zones
        )

    return result


async def whm_preflight_account(
    *,
    session: AsyncSession,
    server_ref: str,
    username: str,
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

    normalized_username = username.strip()
    account = _matching_account(
        accounts=result.get("accounts"), username=normalized_username
    )
    if account is not None:
        normalized_account = normalize_whm_account_summary(account)
        if normalized_account is not None:
            return {
                "ok": True,
                "server_id": str(resolution.server_id),
                "account": normalized_account,
            }

    return {
        "ok": False,
        "error_code": "account_not_found",
        "message": f"No WHM account found for username '{normalized_username}'",
    }


async def whm_preflight_primary_domain_change(
    *,
    session: AsyncSession,
    server_ref: str,
    username: str,
    new_domain: str,
) -> dict[str, object]:
    return await collect_primary_domain_change_state(
        session=session,
        server_ref=server_ref,
        username=username,
        new_domain=new_domain,
        check_dns_zone=False,
    )
