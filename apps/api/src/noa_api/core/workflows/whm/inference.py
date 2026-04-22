from __future__ import annotations

import re

from noa_api.core.workflows.types import normalized_text


def _latest_user_text(working_messages: list[dict[str, object]]) -> str | None:
    for message in reversed(working_messages):
        if message.get("role") != "user":
            continue
        parts = message.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict) or part.get("type") != "text":
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return None


def _infer_whm_account_lifecycle_tool_name(user_text: str) -> str | None:
    lowered = user_text.lower()
    if "unsuspend" in lowered:
        return "whm_unsuspend_account"
    if "suspend" in lowered:
        return "whm_suspend_account"
    return None


def _select_account_preflight_candidate(
    *, account_candidates: list[dict[str, object]], user_text: str
) -> dict[str, object] | None:
    if len(account_candidates) == 1:
        return account_candidates[0]

    lowered = user_text.lower()
    for candidate in reversed(account_candidates):
        args = candidate.get("args")
        if not isinstance(args, dict):
            continue
        server_ref = normalized_text(args.get("server_ref"))
        username = normalized_text(args.get("username"))
        if server_ref is None or username is None:
            continue
        if server_ref.lower() in lowered and username.lower() in lowered:
            return candidate

    return None


def _select_primary_domain_preflight_candidate(
    *, candidates: list[dict[str, object]], user_text: str, new_domain: str
) -> dict[str, object] | None:
    lowered = user_text.lower()
    normalized_domain = new_domain.lower()

    for candidate in reversed(candidates):
        args = candidate.get("args")
        result = candidate.get("result")
        if not isinstance(args, dict) or not isinstance(result, dict):
            continue
        server_ref = normalized_text(args.get("server_ref"))
        username = normalized_text(args.get("username"))
        requested_domain = normalized_text(result.get("requested_domain"))
        if (
            server_ref is None
            or username is None
            or requested_domain is None
            or requested_domain.lower() != normalized_domain
        ):
            continue
        if server_ref.lower() in lowered and username.lower() in lowered:
            return candidate

    for candidate in reversed(candidates):
        result = candidate.get("result")
        if not isinstance(result, dict):
            continue
        requested_domain = normalized_text(result.get("requested_domain"))
        if (
            requested_domain is not None
            and requested_domain.lower() == normalized_domain
        ):
            return candidate

    return None


def _extract_email(text: str) -> str | None:
    match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    return match.group(0) if match is not None else None


def _extract_domain(text: str) -> str | None:
    for match in re.finditer(
        r"\b(?!(?:[A-Za-z0-9._%+-]+@))([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)\b",
        text,
    ):
        domain = normalized_text(match.group(1))
        if domain is not None:
            return domain.rstrip(".").lower()
    return None
