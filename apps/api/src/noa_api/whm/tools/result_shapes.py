from __future__ import annotations


def normalize_whm_account_summary(account: object) -> dict[str, object] | None:
    if not isinstance(account, dict):
        return None

    user = _normalize_optional_string(account.get("user"))
    if user is None:
        return None

    normalized: dict[str, object] = {"user": user}

    domain = _normalize_optional_string(account.get("domain"))
    if domain is not None:
        normalized["domain"] = domain

    email = _normalize_optional_string(account.get("email"))
    if email is not None:
        normalized["email"] = email

    contact_email = _normalize_optional_string(account.get("contactemail"))
    if contact_email is not None:
        normalized["contactemail"] = contact_email

    suspended = _normalize_suspended(account.get("suspended"))
    if suspended is not None:
        normalized["suspended"] = suspended

    return normalized


def _normalize_optional_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_suspended(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y"}:
            return True
        if normalized in {"0", "false", "no", "n"}:
            return False
    return None
