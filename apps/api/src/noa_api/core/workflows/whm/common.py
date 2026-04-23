from __future__ import annotations

from noa_api.core.workflows.types import (
    WorkflowEvidenceItem,
    WorkflowTemplatePhase,
    normalized_string_list,
    normalized_text,
)


def _format_argument_value(value: object) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    if value is None:
        return "none"
    if isinstance(value, list):
        return ", ".join(_format_argument_value(item) for item in value[:5])
    return str(value)


def _extract_before_state(
    preflight_results: list[dict[str, object]],
) -> list[dict[str, str]]:
    before_state: list[dict[str, str]] = []
    for item in preflight_results:
        tool_name = item.get("toolName")
        result = item.get("result")
        if not isinstance(tool_name, str) or not isinstance(result, dict):
            continue
        if tool_name == "whm_preflight_account":
            account = result.get("account")
            if isinstance(account, dict):
                for key, label in (
                    ("user", "Username"),
                    ("domain", "Domain"),
                    ("contactemail", "Contact email"),
                    ("suspended", "Suspended"),
                    ("suspendreason", "Suspend reason"),
                    ("plan", "Plan"),
                ):
                    value = account.get(key)
                    if value in (None, ""):
                        continue
                    before_state.append(
                        {"label": label, "value": _format_argument_value(value)}
                    )
    return before_state


def _clean_items(items: list[WorkflowEvidenceItem]) -> list[WorkflowEvidenceItem]:
    return [item for item in items if item.label.strip() and item.value.strip()]


def _result_ok(result: dict[str, object] | None) -> bool | None:
    if not isinstance(result, dict):
        return None
    value = result.get("ok")
    return value if isinstance(value, bool) else None


def _result_status(result: dict[str, object] | None) -> str | None:
    if not isinstance(result, dict):
        return None
    return normalized_text(result.get("status"))


def _result_message(result: dict[str, object] | None) -> str | None:
    if not isinstance(result, dict):
        return None
    return normalized_text(result.get("message"))


def _result_error_code(result: dict[str, object] | None) -> str | None:
    if not isinstance(result, dict):
        return None
    return normalized_text(result.get("error_code"))


def _result_items(result: dict[str, object] | None) -> list[dict[str, object]]:
    if not isinstance(result, dict):
        return []
    items = result.get("results")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _account_subject(args: dict[str, object]) -> str:
    username = normalized_text(args.get("username")) or "the account"
    server_ref = normalized_text(args.get("server_ref"))
    if server_ref is None:
        return f"'{username}'"
    return f"'{username}' on '{server_ref}'"


def _action_label(tool_name: str) -> str:
    if tool_name == "whm_unsuspend_account":
        return "unsuspend"
    return "suspend"


def _targets_with_status(
    result_items: list[dict[str, object]],
    status: str,
) -> list[str]:
    return [
        target
        for item in result_items
        if normalized_text(item.get("status")) == status
        for target in [normalized_text(item.get("target"))]
        if target is not None
    ]


def _account_state(account: dict[str, object] | None) -> str | None:
    if not isinstance(account, dict):
        return None
    value = account.get("suspended")
    if isinstance(value, bool):
        return "suspended" if value else "active"
    if isinstance(value, int):
        return "suspended" if value == 1 else "active"
    if isinstance(value, str):
        return (
            "suspended"
            if value.strip().lower() in {"1", "true", "yes", "y"}
            else "active"
        )
    return None


def _account_email(account: dict[str, object] | None) -> str | None:
    if not isinstance(account, dict):
        return None
    contact = normalized_text(account.get("contactemail"))
    if contact is not None:
        return contact
    return normalized_text(account.get("email"))


def _account_domain(account: dict[str, object] | None) -> str | None:
    if not isinstance(account, dict):
        return None
    domain = normalized_text(account.get("domain"))
    if domain is None:
        return None
    return domain.rstrip(".").lower()


def _domain_inventory(
    preflight_result: dict[str, object] | None,
) -> dict[str, object] | None:
    if not isinstance(preflight_result, dict):
        return None
    inventory = preflight_result.get("domain_inventory")
    if isinstance(inventory, dict):
        return inventory
    return None


def _requested_domain_location(
    preflight_result: dict[str, object] | None,
) -> str | None:
    if not isinstance(preflight_result, dict):
        return None
    return normalized_text(preflight_result.get("requested_domain_location"))


def _domain_owner(preflight_result: dict[str, object] | None) -> str | None:
    if not isinstance(preflight_result, dict):
        return None
    return normalized_text(preflight_result.get("domain_owner"))


def _dns_zone_exists(postflight_result: dict[str, object] | None) -> bool | None:
    if not isinstance(postflight_result, dict):
        return None
    value = postflight_result.get("dns_zone_exists")
    return value if isinstance(value, bool) else None


def _default_step_statuses(
    *, reason: str | None, phase: WorkflowTemplatePhase
) -> dict[str, str]:
    statuses = {
        "reason": "completed" if reason is not None else "pending",
        "approval": "pending",
        "execute": "pending",
        "postflight": "pending",
    }
    if phase == "waiting_on_user":
        statuses["reason"] = "waiting_on_user"
    elif phase == "waiting_on_approval":
        statuses["approval"] = "waiting_on_approval"
    elif phase == "executing":
        statuses["approval"] = "completed"
        statuses["execute"] = "in_progress"
    elif phase == "completed":
        statuses["approval"] = "completed"
        statuses["execute"] = "completed"
        statuses["postflight"] = "completed"
    elif phase == "denied":
        statuses["approval"] = "cancelled"
        statuses["execute"] = "cancelled"
        statuses["postflight"] = "cancelled"
    elif phase == "failed":
        statuses["approval"] = "completed"
        statuses["execute"] = "cancelled"
        statuses["postflight"] = "cancelled"
    if reason is None and phase in {"completed", "denied", "failed"}:
        statuses["reason"] = "cancelled"
    return statuses


def _render_domain_list(value: object) -> str:
    domains = normalized_string_list(value)
    return ", ".join(domains) if domains else "none"


def _join_with_and(values: list[str]) -> str:
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return ", ".join(values[:-1]) + f", and {values[-1]}"


def _approval_sentence_summary(items: list[str]) -> str | None:
    summaries = [
        text.removesuffix(".")
        for item in items
        for text in [normalized_text(item)]
        if text is not None
    ]
    if not summaries:
        return None
    return "; ".join(summaries) + "."
