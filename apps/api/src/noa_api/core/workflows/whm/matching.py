from __future__ import annotations

from typing import cast

from noa_api.core.tool_error_sanitizer import SanitizedToolError
from noa_api.core.workflows.types import (
    collect_recent_preflight_evidence,
    normalized_string_list,
    normalized_text,
)


def _server_identity_matches(
    *,
    item_args: dict[str, object],
    result: dict[str, object],
    requested_server_ref: str,
    requested_server_id: str | None,
) -> bool:
    result_server_id = normalized_text(result.get("server_id"))
    if requested_server_id is not None and result_server_id is not None:
        return result_server_id == requested_server_id
    return normalized_text(item_args.get("server_ref")) == requested_server_ref


def _matching_account_preflight(
    *, preflight_evidence: list[dict[str, object]], args: dict[str, object]
) -> dict[str, object] | None:
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_username = normalized_text(args.get("username"))
    for item in preflight_evidence:
        if item.get("toolName") != "whm_preflight_account":
            continue
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if result.get("ok") is not True:
            continue
        if normalized_text(item_args.get("server_ref")) != requested_server_ref:
            continue
        account = result.get("account")
        if not isinstance(account, dict):
            continue
        if normalized_text(account.get("user")) != requested_username:
            continue
        return account
    return None


def _matching_primary_domain_preflight(
    *, preflight_evidence: list[dict[str, object]], args: dict[str, object]
) -> dict[str, object] | None:
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_username = normalized_text(args.get("username"))
    requested_domain = normalized_text(args.get("new_domain"))
    for item in preflight_evidence:
        if item.get("toolName") != "whm_preflight_primary_domain_change":
            continue
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if result.get("ok") is not True:
            continue
        if normalized_text(item_args.get("server_ref")) != requested_server_ref:
            continue
        account = result.get("account")
        if not isinstance(account, dict):
            continue
        if normalized_text(account.get("user")) != requested_username:
            continue
        if normalized_text(result.get("requested_domain")) != requested_domain:
            continue
        return result
    return None


def _matching_firewall_preflight_entries(
    *, preflight_evidence: list[dict[str, object]], args: dict[str, object]
) -> list[dict[str, object]]:
    """Extract matching firewall preflight entries for the given args."""
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_targets = set(normalized_string_list(args.get("targets")))
    matches: list[dict[str, object]] = []
    for item in preflight_evidence:
        if item.get("toolName") != "whm_preflight_firewall_entries":
            continue
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if result.get("ok") is not True:
            continue
        if normalized_text(item_args.get("server_ref")) != requested_server_ref:
            continue
        target = normalized_text(result.get("target"))
        if target is None or target not in requested_targets:
            continue
        matches.append(result)
    matches.sort(key=lambda entry: normalized_text(entry.get("target")) or "")
    return matches


def _require_account_preflight(
    *,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
    requested_server_id: str | None,
) -> SanitizedToolError | None:
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_username = normalized_text(args.get("username"))
    if requested_server_ref is None or requested_username is None:
        return None

    evidence = [
        item
        for item in collect_recent_preflight_evidence(working_messages)
        if item.get("toolName") == "whm_preflight_account"
        and isinstance(item.get("result"), dict)
        and cast(dict[str, object], item["result"]).get("ok") is True
    ]
    if not evidence:
        return SanitizedToolError(
            error="Required WHM preflight evidence is missing",
            error_code="preflight_required",
            details=(
                "Run whm_preflight_account with the same server_ref and username before requesting this change.",
            ),
        )

    for item in evidence:
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if not _server_identity_matches(
            item_args=item_args,
            result=result,
            requested_server_ref=requested_server_ref,
            requested_server_id=requested_server_id,
        ):
            continue
        account = result.get("account")
        if not isinstance(account, dict):
            continue
        if normalized_text(account.get("user")) == requested_username:
            return None

    return SanitizedToolError(
        error="Required WHM preflight evidence does not match this change request",
        error_code="preflight_mismatch",
        details=(
            f"No successful whm_preflight_account was found for server_ref '{requested_server_ref}' and username '{requested_username}' in the current turn.",
        ),
    )


def _require_primary_domain_preflight(
    *,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
    requested_server_id: str | None,
) -> SanitizedToolError | None:
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_username = normalized_text(args.get("username"))
    requested_domain = normalized_text(args.get("new_domain"))
    if (
        requested_server_ref is None
        or requested_username is None
        or requested_domain is None
    ):
        return None

    evidence = [
        item
        for item in collect_recent_preflight_evidence(working_messages)
        if item.get("toolName") == "whm_preflight_primary_domain_change"
        and isinstance(item.get("result"), dict)
        and cast(dict[str, object], item["result"]).get("ok") is True
    ]
    if not evidence:
        return SanitizedToolError(
            error="Required WHM preflight evidence is missing",
            error_code="preflight_required",
            details=(
                "Run whm_preflight_primary_domain_change with the same server_ref, username, and new_domain before requesting this change.",
            ),
        )

    for item in evidence:
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if not _server_identity_matches(
            item_args=item_args,
            result=result,
            requested_server_ref=requested_server_ref,
            requested_server_id=requested_server_id,
        ):
            continue
        account = result.get("account")
        if not isinstance(account, dict):
            continue
        if normalized_text(account.get("user")) != requested_username:
            continue
        if normalized_text(result.get("requested_domain")) != requested_domain:
            continue
        return None

    return SanitizedToolError(
        error="Required WHM preflight evidence does not match this change request",
        error_code="preflight_mismatch",
        details=(
            f"No successful whm_preflight_primary_domain_change was found for server_ref '{requested_server_ref}', username '{requested_username}', and new_domain '{requested_domain}' in the current turn.",
        ),
    )


def _require_firewall_preflight(
    *,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
    requested_server_id: str | None,
) -> SanitizedToolError | None:
    """Require matching firewall preflight evidence before allowing firewall change."""
    requested_server_ref = normalized_text(args.get("server_ref"))
    requested_targets = normalized_string_list(args.get("targets"))
    if requested_server_ref is None or not requested_targets:
        return None

    evidence = [
        item
        for item in collect_recent_preflight_evidence(working_messages)
        if item.get("toolName") == "whm_preflight_firewall_entries"
        and isinstance(item.get("result"), dict)
        and cast(dict[str, object], item["result"]).get("ok") is True
    ]
    if not evidence:
        return SanitizedToolError(
            error="Required firewall preflight evidence is missing",
            error_code="preflight_required",
            details=(
                "Run whm_preflight_firewall_entries for each target with the same server_ref before requesting this change.",
            ),
        )

    matched_targets: set[str] = set()
    for item in evidence:
        item_args = item.get("args")
        result = item.get("result")
        if not isinstance(item_args, dict) or not isinstance(result, dict):
            continue
        if not _server_identity_matches(
            item_args=item_args,
            result=result,
            requested_server_ref=requested_server_ref,
            requested_server_id=requested_server_id,
        ):
            continue
        target = normalized_text(result.get("target"))
        if target is not None:
            matched_targets.add(target)

    missing_targets = [
        target for target in requested_targets if target not in matched_targets
    ]
    if not missing_targets:
        return None

    return SanitizedToolError(
        error="Required firewall preflight evidence does not match this change request",
        error_code="preflight_mismatch",
        details=(
            "Missing successful whm_preflight_firewall_entries results for target(s): "
            + ", ".join(f"'{target}'" for target in missing_targets),
        ),
    )


def _postflight_account(
    postflight_result: dict[str, object] | None,
) -> dict[str, object] | None:
    if (
        not isinstance(postflight_result, dict)
        or postflight_result.get("ok") is not True
    ):
        return None
    account = postflight_result.get("account")
    if isinstance(account, dict):
        return account
    return None


def _postflight_firewall_entries(
    postflight_result: dict[str, object] | None,
) -> list[dict[str, object]]:
    """Extract postflight entries from firewall postflight result."""
    if not isinstance(postflight_result, dict):
        return []
    results = postflight_result.get("results")
    if not isinstance(results, list):
        return []
    return [item for item in results if isinstance(item, dict)]


def _account_preflight_candidates(
    working_messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    return [
        item
        for item in collect_recent_preflight_evidence(working_messages)
        if item.get("toolName") == "whm_preflight_account"
        and isinstance(item.get("args"), dict)
    ]


def _primary_domain_preflight_candidates(
    working_messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    return [
        item
        for item in collect_recent_preflight_evidence(working_messages)
        if item.get("toolName") == "whm_preflight_primary_domain_change"
        and isinstance(item.get("args"), dict)
    ]
