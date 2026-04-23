"""Shared schema constants used across tool families."""

from __future__ import annotations

from noa_api.core.tools.schema_builders import (
    _result_any_of,
    _result_array_schema,
    _result_boolean_schema,
    _result_integer_schema,
    _result_nullable_schema,
    _result_object_schema,
    _result_string_schema,
    _string_array_param,
    _string_param,
)

# ---------------------------------------------------------------------------
# Shared parameter schemas
# ---------------------------------------------------------------------------

SERVER_REF_PARAM = _string_param(
    "Server reference that resolves to exactly one configured WHM server. Use a server name or UUID and ask the user to choose if the tool returns choices.",
    format_name="server-ref",
)

USERNAME_PARAM = _string_param(
    "Exact WHM username. Trim whitespace and prefer identifiers confirmed by whm_search_accounts or whm_preflight_account.",
    format_name="whm-username",
)

DOMAIN_PARAM = _string_param(
    "Fully-qualified domain name to use as the cPanel account primary domain.",
    pattern=r"^(?=.{1,253}$)(?!-)(?:[A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,63}\.?$",
)

BINARY_NAME_PARAM = _string_param(
    "Exact remote binary name to look up over SSH, such as `imunify360-agent` or `python3`.",
    pattern=r"^[A-Za-z0-9._+-]{1,128}$",
)

REASON_PARAM = _string_param(
    "Human-readable reason for the requested operational change. Required and must come from the user or verified context.",
)

CSF_TARGET_PARAM = _string_param(
    "Single CSF target to inspect, such as an IP, CIDR, or hostname. Trim whitespace and do not invent values.",
    format_name="csf-target",
)

CSF_TARGETS_PARAM = _string_array_param(
    "One or more exact CSF targets to change. Preserve the user-provided values and include one result entry per target.",
    item_description="Exact IP, CIDR, or hostname target",
    item_format_name="csf-target",
    unique_items=True,
)

TODO_ITEM_SCHEMA = {
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

# ---------------------------------------------------------------------------
# Shared result schemas
# ---------------------------------------------------------------------------

RESULT_ERROR_SCHEMA = _result_object_schema(
    properties={
        "ok": _result_boolean_schema(value=False),
        "error_code": _result_string_schema(),
        "message": _result_string_schema(),
    },
    required=["ok", "error_code", "message"],
)

RESULT_SUCCESS_OK_SCHEMA = {"ok": _result_boolean_schema(value=True)}

SERVER_SAFE_RESULT_SCHEMA = _result_object_schema(
    properties={
        "id": _result_string_schema(),
        "name": _result_string_schema(),
        "base_url": _result_string_schema(),
        "api_username": _result_string_schema(),
        "ssh_username": _result_nullable_schema(_result_string_schema()),
        "ssh_port": _result_nullable_schema(_result_integer_schema()),
        "ssh_host_key_fingerprint": _result_nullable_schema(_result_string_schema()),
        "has_ssh_password": _result_boolean_schema(),
        "has_ssh_private_key": _result_boolean_schema(),
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

ACCOUNT_RESULT_SCHEMA = _result_object_schema(
    properties={
        "user": _result_string_schema(),
        "domain": _result_string_schema(),
        "email": _result_string_schema(),
        "contactemail": _result_string_schema(),
        "suspended": _result_boolean_schema(),
    },
    required=["user"],
)

WORKFLOW_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **RESULT_SUCCESS_OK_SCHEMA,
            "todos": _result_array_schema(items=TODO_ITEM_SCHEMA),
        },
        required=["ok", "todos"],
    ),
    RESULT_ERROR_SCHEMA,
)
