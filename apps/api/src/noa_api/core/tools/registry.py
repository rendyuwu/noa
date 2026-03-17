from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from noa_api.core.tools.demo_tools import (
    get_current_date,
    get_current_time,
    set_demo_flag,
)
from noa_api.core.tools.workflow_todo import update_workflow_todo
from noa_api.storage.postgres.lifecycle import ToolRisk
from noa_api.whm.tools.account_change_tools import (
    whm_change_contact_email,
    whm_suspend_account,
    whm_unsuspend_account,
)
from noa_api.whm.tools.csf_change_tools import (
    whm_csf_allowlist_add_ttl,
    whm_csf_allowlist_remove,
    whm_csf_denylist_add_ttl,
    whm_csf_unblock,
)
from noa_api.whm.tools.preflight_tools import (
    whm_preflight_account,
    whm_preflight_csf_entries,
)
from noa_api.whm.tools.read_tools import (
    whm_list_accounts,
    whm_list_servers,
    whm_search_accounts,
    whm_validate_server,
)

ToolExecutor = Callable[..., Awaitable[dict[str, Any]]]
ToolParametersSchema = dict[str, Any]
ToolResultSchema = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    risk: ToolRisk
    parameters_schema: ToolParametersSchema
    execute: ToolExecutor
    prompt_hints: tuple[str, ...] = ()
    result_schema: ToolResultSchema | None = None


def _object_schema(
    *, properties: dict[str, Any], required: list[str]
) -> ToolParametersSchema:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _string_param(
    description: str,
    *,
    min_length: int = 1,
    format_name: str | None = None,
    pattern: str | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "string",
        "description": description,
    }
    if min_length > 0:
        schema["minLength"] = min_length
    if format_name is not None:
        schema["format"] = format_name
    if pattern is not None:
        schema["pattern"] = pattern
    return schema


def _integer_param(
    description: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    default: int | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "integer",
        "description": description,
    }
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    if default is not None:
        schema["default"] = default
    return schema


def _string_array_param(
    description: str,
    *,
    item_description: str | None = None,
    min_items: int = 1,
    unique_items: bool = False,
    item_format_name: str | None = None,
    item_pattern: str | None = None,
) -> dict[str, Any]:
    items = _string_param(
        item_description or "Non-empty string value",
        format_name=item_format_name,
        pattern=item_pattern,
    )
    schema = {
        "type": "array",
        "description": description,
        "items": items,
        "minItems": min_items,
    }
    if unique_items:
        schema["uniqueItems"] = True
    return schema


def _result_object_schema(
    *,
    properties: dict[str, Any],
    required: list[str],
    additional_properties: bool = False,
) -> ToolResultSchema:
    schema: ToolResultSchema = {
        "type": "object",
        "properties": properties,
        "required": required,
    }
    if not additional_properties:
        schema["additionalProperties"] = False
    return schema


def _result_array_schema(
    *, items: dict[str, Any], min_items: int | None = None
) -> ToolResultSchema:
    schema: ToolResultSchema = {
        "type": "array",
        "items": items,
    }
    if min_items is not None:
        schema["minItems"] = min_items
    return schema


def _result_string_schema(*, enum: list[str] | None = None) -> ToolResultSchema:
    schema: ToolResultSchema = {"type": "string"}
    if enum is not None:
        schema["enum"] = enum
    return schema


def _result_boolean_schema(*, value: bool | None = None) -> ToolResultSchema:
    schema: ToolResultSchema = {"type": "boolean"}
    if value is not None:
        schema["enum"] = [value]
    return schema


def _result_any_of(*variants: ToolResultSchema) -> ToolResultSchema:
    return {"anyOf": list(variants)}


_SERVER_REF_PARAM = _string_param(
    "Server reference that resolves to exactly one configured WHM server. Use a server name or UUID and ask the user to choose if the tool returns choices.",
    format_name="server-ref",
)

_USERNAME_PARAM = _string_param(
    "Exact WHM username. Trim whitespace and prefer identifiers confirmed by whm_search_accounts or whm_preflight_account.",
    format_name="whm-username",
)

_REASON_PARAM = _string_param(
    "Human-readable reason for the requested operational change. Required and must come from the user or verified context.",
)

_CSF_TARGET_PARAM = _string_param(
    "Single CSF target to inspect, such as an IP, CIDR, or hostname. Trim whitespace and do not invent values.",
    format_name="csf-target",
)

_CSF_TARGETS_PARAM = _string_array_param(
    "One or more exact CSF targets to change. Preserve the user-provided values and include one result entry per target.",
    item_description="Exact IP, CIDR, or hostname target",
    item_format_name="csf-target",
    unique_items=True,
)

_TODO_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "content": _string_param(
            "Short task description shown to the user in the workflow checklist."
        ),
        "status": {
            "type": "string",
            "description": "Workflow state. Use pending, in_progress, waiting_on_user, waiting_on_approval, completed, or cancelled. Only one todo item may be in_progress.",
            "enum": [
                "pending",
                "in_progress",
                "waiting_on_user",
                "waiting_on_approval",
                "completed",
                "cancelled",
            ],
        },
        "priority": {
            "type": "string",
            "description": "Relative importance for the task. Use high, medium, or low.",
            "enum": ["high", "medium", "low"],
        },
    },
    "required": ["content", "status", "priority"],
    "additionalProperties": False,
}

_RESULT_ERROR_SCHEMA = _result_object_schema(
    properties={
        "ok": _result_boolean_schema(value=False),
        "error_code": _result_string_schema(),
        "message": _result_string_schema(),
    },
    required=["ok", "error_code", "message"],
)

_WHM_SERVER_CHOICE_SCHEMA = _result_object_schema(
    properties={
        "id": _result_string_schema(),
        "name": _result_string_schema(),
        "base_url": _result_string_schema(),
    },
    required=["id", "name", "base_url"],
)

_WHM_RESULT_ERROR_SCHEMA = _result_object_schema(
    properties={
        "ok": _result_boolean_schema(value=False),
        "error_code": _result_string_schema(),
        "message": _result_string_schema(),
        "choices": _result_array_schema(items=_WHM_SERVER_CHOICE_SCHEMA),
    },
    required=["ok", "error_code", "message"],
)

_RESULT_SUCCESS_OK_SCHEMA = {"ok": _result_boolean_schema(value=True)}

_SERVER_SAFE_RESULT_SCHEMA = _result_object_schema(
    properties={
        "id": _result_string_schema(),
        "name": _result_string_schema(),
        "base_url": _result_string_schema(),
        "api_username": _result_string_schema(),
        "verify_ssl": _result_boolean_schema(),
        "created_at": _result_string_schema(),
        "updated_at": _result_string_schema(),
    },
    required=[
        "id",
        "name",
        "base_url",
        "api_username",
        "verify_ssl",
        "created_at",
        "updated_at",
    ],
    additional_properties=False,
)

_ACCOUNT_RESULT_SCHEMA = _result_object_schema(
    properties={
        "user": _result_string_schema(),
        "domain": _result_string_schema(),
        "email": _result_string_schema(),
        "contactemail": _result_string_schema(),
        "suspended": _result_boolean_schema(),
    },
    required=["user"],
)

_WORKFLOW_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **_RESULT_SUCCESS_OK_SCHEMA,
            "todos": _result_array_schema(items=_TODO_ITEM_SCHEMA),
        },
        required=["ok", "todos"],
    ),
    _RESULT_ERROR_SCHEMA,
)

_DEMO_FLAG_RESULT_SCHEMA = _result_object_schema(
    properties={
        **_RESULT_SUCCESS_OK_SCHEMA,
        "flag": {"type": "object"},
    },
    required=["ok", "flag"],
)

_SERVERS_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **_RESULT_SUCCESS_OK_SCHEMA,
            "servers": _result_array_schema(items=_SERVER_SAFE_RESULT_SCHEMA),
        },
        required=["ok", "servers"],
    ),
    _WHM_RESULT_ERROR_SCHEMA,
)

_ACCOUNTS_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **_RESULT_SUCCESS_OK_SCHEMA,
            "accounts": _result_array_schema(items=_ACCOUNT_RESULT_SCHEMA),
        },
        required=["ok", "accounts"],
    ),
    _WHM_RESULT_ERROR_SCHEMA,
)

_SEARCH_ACCOUNTS_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **_RESULT_SUCCESS_OK_SCHEMA,
            "accounts": _result_array_schema(items=_ACCOUNT_RESULT_SCHEMA),
            "query": _result_string_schema(),
        },
        required=["ok", "accounts", "query"],
    ),
    _WHM_RESULT_ERROR_SCHEMA,
)

_VALIDATE_SERVER_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **_RESULT_SUCCESS_OK_SCHEMA,
            "message": _result_string_schema(),
        },
        required=["ok", "message"],
    ),
    _WHM_RESULT_ERROR_SCHEMA,
)

_PREFLIGHT_ACCOUNT_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **_RESULT_SUCCESS_OK_SCHEMA,
            "server_id": _result_string_schema(),
            "account": _ACCOUNT_RESULT_SCHEMA,
        },
        required=["ok", "server_id", "account"],
    ),
    _WHM_RESULT_ERROR_SCHEMA,
)

_PREFLIGHT_CSF_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **_RESULT_SUCCESS_OK_SCHEMA,
            "server_id": _result_string_schema(),
            "target": _result_string_schema(),
            "verdict": _result_string_schema(),
            "matches": _result_array_schema(items=_result_string_schema()),
        },
        required=["ok", "server_id", "target", "verdict", "matches"],
    ),
    _WHM_RESULT_ERROR_SCHEMA,
)

_ACCOUNT_CHANGE_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **_RESULT_SUCCESS_OK_SCHEMA,
            "status": _result_string_schema(enum=["changed", "no-op"]),
            "message": _result_string_schema(),
        },
        required=["ok", "status", "message"],
    ),
    _WHM_RESULT_ERROR_SCHEMA,
)

_CSF_RESULT_ITEM_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            "target": _result_string_schema(),
            "ok": _result_boolean_schema(value=True),
            "status": _result_string_schema(enum=["changed", "no-op"]),
            "verdict": _result_string_schema(),
            "matches": _result_array_schema(items=_result_string_schema()),
        },
        required=["target", "ok", "status", "verdict", "matches"],
    ),
    _result_object_schema(
        properties={
            "target": _result_string_schema(),
            "ok": _result_boolean_schema(value=False),
            "status": _result_string_schema(enum=["error"]),
            "error_code": _result_string_schema(),
            "message": _result_string_schema(),
        },
        required=["target", "ok", "status", "error_code", "message"],
    ),
)

_CSF_BATCH_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            "ok": _result_boolean_schema(value=True),
            "results": _result_array_schema(items=_CSF_RESULT_ITEM_SCHEMA),
        },
        required=["ok", "results"],
    ),
    _WHM_RESULT_ERROR_SCHEMA,
)


_MVP_TOOLS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="get_current_time",
        description="Return the current server time as an ISO-8601 timestamp in the `time` field.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(properties={}, required=[]),
        execute=get_current_time,
        prompt_hints=(
            "Use this only when the user asks for the current time or when time is needed as evidence.",
            "Successful results return `{time}`.",
        ),
    ),
    ToolDefinition(
        name="get_current_date",
        description="Return the current server date as an ISO-8601 date string in the `date` field.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(properties={}, required=[]),
        execute=get_current_date,
        prompt_hints=(
            "Use this only when the user asks for the current date or when date is needed as evidence.",
            "Successful results return `{date}`.",
        ),
    ),
    ToolDefinition(
        name="set_demo_flag",
        description="Persist a demo marker value for internal or development workflows.",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "key": _string_param(
                    "Demo flag name to persist in the audit log, such as a feature or scenario identifier."
                ),
                "value": {
                    "description": "JSON-serializable flag value to persist for the demo marker."
                },
            },
            required=["key", "value"],
        ),
        execute=set_demo_flag,
        prompt_hints=(
            "Use only for explicit demo-flag requests.",
            "Successful results return `ok: true` and echo the saved `flag` payload.",
        ),
        result_schema=_DEMO_FLAG_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="update_workflow_todo",
        description="Create or replace the visible workflow checklist for multi-step or operational work.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "todos": {
                    "type": "array",
                    "description": "Full workflow checklist to display. Keep exactly one item in_progress at a time.",
                    "items": _TODO_ITEM_SCHEMA,
                }
            },
            required=["todos"],
        ),
        execute=update_workflow_todo,
        prompt_hints=(
            "Use this immediately for multi-step or operational requests, then keep it updated until the work is done.",
            "Keep exactly one item in_progress at a time.",
            "Do not use it for trivial Q and A.",
            "Successful results return the saved `todos`; invalid states return `ok: false` with an `error_code`.",
        ),
        result_schema=_WORKFLOW_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="whm_list_servers",
        description="List configured WHM servers using safe fields only.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(properties={}, required=[]),
        execute=whm_list_servers,
        prompt_hints=(
            "Use this when the user has not supplied a server_ref or when you need server choices for disambiguation.",
            "Successful results return `servers` and never include API tokens.",
        ),
        result_schema=_SERVERS_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="whm_validate_server",
        description="Validate a WHM server reference by calling a lightweight WHM API check.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={"server_ref": _SERVER_REF_PARAM},
            required=["server_ref"],
        ),
        execute=whm_validate_server,
        prompt_hints=(
            "Use this for connectivity or credential validation, not for account discovery.",
            "Success returns `ok: true` and `message: ok`; failures return `error_code`, `message`, and possibly `choices` if the server reference is ambiguous.",
        ),
        result_schema=_VALIDATE_SERVER_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="whm_list_accounts",
        description="List WHM accounts for one resolved server.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={"server_ref": _SERVER_REF_PARAM},
            required=["server_ref"],
        ),
        execute=whm_list_accounts,
        prompt_hints=(
            "Use this for account discovery when you need the available usernames or domains on a server.",
            "Successful results return `accounts`; resolution failures return `choices` or `error_code`.",
        ),
        result_schema=_ACCOUNTS_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="whm_search_accounts",
        description="Search WHM accounts by case-insensitive username or domain substring on one server.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _SERVER_REF_PARAM,
                "query": _string_param(
                    "Case-insensitive search text matched against the account username and domain."
                ),
                "limit": _integer_param(
                    "Maximum number of matching accounts to return. Must be a positive integer.",
                    minimum=1,
                    maximum=100,
                    default=20,
                ),
            },
            required=["server_ref", "query"],
        ),
        execute=whm_search_accounts,
        prompt_hints=(
            "Use this to discover the exact WHM username before account changes or preflight calls.",
            "Successful results return matching `accounts` and echo the original `query`.",
        ),
        result_schema=_SEARCH_ACCOUNTS_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="whm_preflight_account",
        description="Fetch the current WHM account state for one exact username before an account change.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _SERVER_REF_PARAM,
                "username": _USERNAME_PARAM,
            },
            required=["server_ref", "username"],
        ),
        execute=whm_preflight_account,
        prompt_hints=(
            "Run this before `whm_suspend_account`, `whm_unsuspend_account`, or `whm_change_contact_email` and summarize the evidence before proposing the change.",
            "Successful results return `account` with fields such as `user`, `domain`, `contactemail`, and `suspended`.",
        ),
        result_schema=_PREFLIGHT_ACCOUNT_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="whm_preflight_csf_entries",
        description="Inspect the current CSF state for one target before a CSF or firewall change.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _SERVER_REF_PARAM,
                "target": _CSF_TARGET_PARAM,
            },
            required=["server_ref", "target"],
        ),
        execute=whm_preflight_csf_entries,
        prompt_hints=(
            "Run this once per target before CSF unblock, allowlist, or denylist changes and summarize the verdict.",
            "Successful results return `target`, `verdict`, and matched CSF entries in `matches`.",
        ),
        result_schema=_PREFLIGHT_CSF_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="whm_suspend_account",
        description="Suspend one WHM account after the exact account has been preflighted.",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _SERVER_REF_PARAM,
                "username": _USERNAME_PARAM,
                "reason": _REASON_PARAM,
            },
            required=["server_ref", "username", "reason"],
        ),
        execute=whm_suspend_account,
        prompt_hints=(
            "Run `whm_preflight_account` first and summarize the current account state before proposing this tool.",
            "Idempotent result contract: returns `status` `no-op` if already suspended, or `status` `changed` only after postflight confirms the suspension.",
            "Failures return `ok: false` with `error_code` and `message`.",
        ),
        result_schema=_ACCOUNT_CHANGE_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="whm_unsuspend_account",
        description="Unsuspend one WHM account after the exact account has been preflighted.",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _SERVER_REF_PARAM,
                "username": _USERNAME_PARAM,
                "reason": _REASON_PARAM,
            },
            required=["server_ref", "username", "reason"],
        ),
        execute=whm_unsuspend_account,
        prompt_hints=(
            "Run `whm_preflight_account` first and summarize the current account state before proposing this tool.",
            "Idempotent result contract: returns `status` `no-op` if the account is already active, or `status` `changed` only after postflight confirms the unsuspend.",
            "Failures return `ok: false` with `error_code` and `message`.",
        ),
        result_schema=_ACCOUNT_CHANGE_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="whm_change_contact_email",
        description="Change the WHM contact email for one exact account after preflight.",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _SERVER_REF_PARAM,
                "username": _USERNAME_PARAM,
                "new_email": _string_param(
                    "New contact email address to set for the WHM account.",
                    format_name="email",
                ),
                "reason": _REASON_PARAM,
            },
            required=["server_ref", "username", "new_email", "reason"],
        ),
        execute=whm_change_contact_email,
        prompt_hints=(
            "Run `whm_preflight_account` first and summarize the existing contact email before proposing this tool.",
            "Idempotent result contract: returns `status` `no-op` if the email already matches, or `status` `changed` only after postflight verifies the new email.",
            "Failures return `ok: false` with `error_code` and `message`.",
        ),
        result_schema=_ACCOUNT_CHANGE_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="whm_csf_unblock",
        description="Remove CSF block entries for one or more targets after target-by-target preflight.",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _SERVER_REF_PARAM,
                "targets": _CSF_TARGETS_PARAM,
                "reason": _REASON_PARAM,
            },
            required=["server_ref", "targets", "reason"],
        ),
        execute=whm_csf_unblock,
        prompt_hints=(
            "Run `whm_preflight_csf_entries` once per target before proposing this tool.",
            "Batch result contract: returns `results` entries per target with `status` `changed`, `no-op`, or `error`, plus `verdict`, `matches`, and `error_code` when relevant.",
        ),
        result_schema=_CSF_BATCH_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="whm_csf_allowlist_remove",
        description="Remove one or more targets from the CSF allowlist after target-by-target preflight.",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _SERVER_REF_PARAM,
                "targets": _CSF_TARGETS_PARAM,
                "reason": _REASON_PARAM,
            },
            required=["server_ref", "targets", "reason"],
        ),
        execute=whm_csf_allowlist_remove,
        prompt_hints=(
            "Run `whm_preflight_csf_entries` once per target before proposing this tool.",
            "Batch result contract: returns `results` entries per target with `status` `changed`, `no-op`, or `error`, plus `verdict`, `matches`, and `error_code` when relevant.",
        ),
        result_schema=_CSF_BATCH_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="whm_csf_allowlist_add_ttl",
        description="Temporarily add one or more IPv4 targets to the CSF allowlist after target-by-target preflight.",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _SERVER_REF_PARAM,
                "targets": _string_array_param(
                    "One or more IPv4 addresses to allowlist temporarily. This TTL tool does not accept CIDRs, hostnames, or IPv6 targets.",
                    item_description="Exact IPv4 address target",
                    item_format_name="ipv4",
                    unique_items=True,
                ),
                "duration_minutes": _integer_param(
                    "Duration already converted to minutes before calling the tool.",
                    minimum=1,
                    maximum=525600,
                ),
                "reason": _REASON_PARAM,
            },
            required=["server_ref", "targets", "duration_minutes", "reason"],
        ),
        execute=whm_csf_allowlist_add_ttl,
        prompt_hints=(
            "Run `whm_preflight_csf_entries` once per target before proposing this tool.",
            "Batch result contract: returns `results` entries per target with `status` `changed`, `no-op`, or `error`, plus `verdict`, `matches`, and `error_code` when relevant.",
        ),
        result_schema=_CSF_BATCH_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="whm_csf_denylist_add_ttl",
        description="Temporarily add one or more IPv4 targets to the CSF denylist after target-by-target preflight.",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _SERVER_REF_PARAM,
                "targets": _string_array_param(
                    "One or more IPv4 addresses to deny temporarily. This TTL tool does not accept CIDRs, hostnames, or IPv6 targets.",
                    item_description="Exact IPv4 address target",
                    item_format_name="ipv4",
                    unique_items=True,
                ),
                "duration_minutes": _integer_param(
                    "Duration already converted to minutes before calling the tool.",
                    minimum=1,
                    maximum=525600,
                ),
                "reason": _REASON_PARAM,
            },
            required=["server_ref", "targets", "duration_minutes", "reason"],
        ),
        execute=whm_csf_denylist_add_ttl,
        prompt_hints=(
            "Run `whm_preflight_csf_entries` once per target before proposing this tool.",
            "Batch result contract: returns `results` entries per target with `status` `changed`, `no-op`, or `error`, plus `verdict`, `matches`, and `error_code` when relevant.",
        ),
        result_schema=_CSF_BATCH_RESULT_SCHEMA,
    ),
)
_MVP_TOOL_INDEX = {tool.name: tool for tool in _MVP_TOOLS}


def get_tool_registry() -> tuple[ToolDefinition, ...]:
    return _MVP_TOOLS


def get_tool_definition(tool_name: str) -> ToolDefinition | None:
    return _MVP_TOOL_INDEX.get(tool_name)


def get_tool_names() -> tuple[str, ...]:
    return tuple(tool.name for tool in _MVP_TOOLS)
