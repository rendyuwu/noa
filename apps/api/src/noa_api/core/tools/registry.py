from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from noa_api.core.tools.demo_tools import get_current_date, get_current_time
from noa_api.core.tools.workflow_todo import update_workflow_todo
from noa_api.proxmox.tools.cloudinit_tools import (
    proxmox_preflight_vm_cloudinit_password_reset,
    proxmox_reset_vm_cloudinit_password,
)
from noa_api.proxmox.tools.nic_tools import (
    proxmox_disable_vm_nic,
    proxmox_enable_vm_nic,
    proxmox_preflight_vm_nic_toggle,
)
from noa_api.proxmox.tools.pool_tools import (
    proxmox_get_user_by_email,
    proxmox_move_vms_between_pools,
    proxmox_preflight_move_vms_between_pools,
)
from noa_api.proxmox.tools.read_tools import (
    proxmox_list_servers,
    proxmox_validate_server,
)
from noa_api.proxmox.tools.vm_read_tools import (
    proxmox_get_vm_config,
    proxmox_get_vm_pending,
    proxmox_get_vm_status_current,
)
from noa_api.storage.postgres.lifecycle import ToolRisk
from noa_api.whm.tools.account_change_tools import (
    whm_change_primary_domain,
    whm_change_contact_email,
    whm_suspend_account,
    whm_unsuspend_account,
)
from noa_api.whm.tools.firewall_tools import (
    whm_firewall_allowlist_add_ttl,
    whm_firewall_allowlist_remove,
    whm_firewall_denylist_add_ttl,
    whm_firewall_unblock,
    whm_preflight_firewall_entries,
)
from noa_api.whm.tools.preflight_tools import (
    whm_preflight_account,
    whm_preflight_primary_domain_change,
)
from noa_api.whm.tools.read_tools import (
    whm_mail_log_failed_auth_suspects,
    whm_check_binary_exists,
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
    workflow_family: str | None = None


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


def _integer_array_param(
    description: str,
    *,
    min_items: int = 1,
    unique_items: bool = False,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "array",
        "description": description,
        "items": {"type": "integer", "minimum": 1},
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


def _result_null_schema() -> ToolResultSchema:
    return {"enum": [None]}


def _result_nullable_schema(schema: ToolResultSchema) -> ToolResultSchema:
    return _result_any_of(schema, _result_null_schema())


def _result_json_value_schema() -> ToolResultSchema:
    return _result_any_of(
        {"type": "object"},
        {"type": "array"},
        _result_string_schema(),
        _result_null_schema(),
    )


def _result_integer_schema() -> ToolResultSchema:
    return {"type": "integer"}


def _result_json_object_schema() -> ToolResultSchema:
    return _result_object_schema(properties={}, required=[], additional_properties=True)


def _result_json_array_schema() -> ToolResultSchema:
    return _result_array_schema(items=_result_json_object_schema())


def _result_upstream_response_schema(
    *, data_schema: ToolResultSchema | None = None
) -> ToolResultSchema:
    return _result_object_schema(
        properties={
            **_RESULT_SUCCESS_OK_SCHEMA,
            "message": _result_string_schema(),
            "data": data_schema or _result_json_value_schema(),
        },
        required=["ok", "message", "data"],
    )


def _result_vm_data_schema() -> ToolResultSchema:
    return _result_any_of(
        _result_json_object_schema(),
        _result_json_array_schema(),
        _result_string_schema(),
        _result_null_schema(),
    )


def _result_pool_response_schema() -> ToolResultSchema:
    return _result_upstream_response_schema(data_schema=_result_json_object_schema())


_SERVER_REF_PARAM = _string_param(
    "Server reference that resolves to exactly one configured WHM server. Use a server name or UUID and ask the user to choose if the tool returns choices.",
    format_name="server-ref",
)

_PROXMOX_SERVER_REF_PARAM = _string_param(
    "Server reference that resolves to exactly one configured Proxmox server. Use a server name, UUID, or base URL host and ask the user to choose if the tool returns choices.",
    format_name="proxmox-server-ref",
)

_PROXMOX_NODE_PARAM = _string_param(
    "Exact Proxmox node name that hosts the QEMU VM.",
    pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
)

_PROXMOX_VMID_PARAM = _integer_param(
    "Numeric Proxmox QEMU VM ID.",
    minimum=1,
)

_PROXMOX_NET_PARAM = _string_param(
    "Exact Proxmox NIC key such as net0 or net1.",
    pattern=r"^net\d+$",
)

_PROXMOX_DIGEST_PARAM = _string_param(
    "Exact Proxmox config digest returned by proxmox_preflight_vm_nic_toggle.",
)

_PROXMOX_EMAIL_PARAM = _string_param(
    "Exact email address used to identify the Proxmox user account.",
    format_name="email",
)

_PROXMOX_POOL_PARAM = _string_param(
    "Exact Proxmox pool name.",
)

_PROXMOX_VMID_LIST_PARAM = _integer_array_param(
    "One or more exact Proxmox QEMU VM IDs. Preserve the user-provided VMIDs and do not invent values.",
    unique_items=True,
)

_PROXMOX_CLOUDINIT_PASSWORD_PARAM = _string_param(
    "New Proxmox cloud-init password to set for the VM.",
)

_USERNAME_PARAM = _string_param(
    "Exact WHM username. Trim whitespace and prefer identifiers confirmed by whm_search_accounts or whm_preflight_account.",
    format_name="whm-username",
)

_DOMAIN_PARAM = _string_param(
    "Fully-qualified domain name to use as the cPanel account primary domain.",
    pattern=r"^(?=.{1,253}$)(?!-)(?:[A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,63}\.?$",
)

_BINARY_NAME_PARAM = _string_param(
    "Exact remote binary name to look up over SSH, such as `imunify360-agent` or `python3`.",
    pattern=r"^[A-Za-z0-9._+-]{1,128}$",
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

_PROXMOX_SERVER_CHOICE_SCHEMA = _result_object_schema(
    properties={
        "id": _result_string_schema(),
        "name": _result_string_schema(),
        "base_url": _result_string_schema(),
    },
    required=["id", "name", "base_url"],
)

_PROXMOX_NET_RESULT_SCHEMA = _result_object_schema(
    properties={
        "key": _result_string_schema(),
        "value": _result_string_schema(),
        "link_down": _result_boolean_schema(),
        "link_state": _result_string_schema(enum=["up", "down"]),
        "model": _result_nullable_schema(_result_string_schema()),
        "mac_address": _result_nullable_schema(_result_string_schema()),
        "bridge": _result_nullable_schema(_result_string_schema()),
    },
    required=[
        "key",
        "value",
        "link_down",
        "link_state",
        "model",
        "mac_address",
        "bridge",
    ],
)

_PROXMOX_RESULT_ERROR_SCHEMA = _result_object_schema(
    properties={
        "ok": _result_boolean_schema(value=False),
        "error_code": _result_string_schema(),
        "message": _result_string_schema(),
        "choices": _result_array_schema(items=_PROXMOX_SERVER_CHOICE_SCHEMA),
        "nets": _result_array_schema(items=_PROXMOX_NET_RESULT_SCHEMA),
        "server_id": _result_string_schema(),
        "node": _result_string_schema(),
        "vmid": {"type": "integer"},
        "digest": _result_string_schema(),
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

_PROXMOX_SERVER_SAFE_RESULT_SCHEMA = _result_object_schema(
    properties={
        "id": _result_string_schema(),
        "name": _result_string_schema(),
        "base_url": _result_string_schema(),
        "api_token_id": _result_string_schema(),
        "has_api_token_secret": _result_boolean_schema(),
        "verify_ssl": _result_boolean_schema(),
        "created_at": _result_string_schema(),
        "updated_at": _result_string_schema(),
    },
    required=[
        "id",
        "name",
        "base_url",
        "api_token_id",
        "has_api_token_secret",
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

_PROXMOX_SERVERS_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **_RESULT_SUCCESS_OK_SCHEMA,
            "servers": _result_array_schema(items=_PROXMOX_SERVER_SAFE_RESULT_SCHEMA),
        },
        required=["ok", "servers"],
    ),
    _PROXMOX_RESULT_ERROR_SCHEMA,
)

_PROXMOX_VALIDATE_SERVER_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **_RESULT_SUCCESS_OK_SCHEMA,
            "message": _result_string_schema(),
        },
        required=["ok", "message"],
    ),
    _PROXMOX_RESULT_ERROR_SCHEMA,
)

_PROXMOX_VM_READ_RESULT_SCHEMA = _result_any_of(
    _result_upstream_response_schema(data_schema=_result_vm_data_schema()),
    _PROXMOX_RESULT_ERROR_SCHEMA,
)

_PROXMOX_USER_RESULT_SCHEMA = _result_any_of(
    _result_upstream_response_schema(data_schema=_result_json_object_schema()),
    _PROXMOX_RESULT_ERROR_SCHEMA,
)

_PROXMOX_PREFLIGHT_CLOUDINIT_PASSWORD_RESET_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **_RESULT_SUCCESS_OK_SCHEMA,
            "message": _result_string_schema(),
            "server_id": _result_string_schema(),
            "node": _result_string_schema(),
            "vmid": _result_integer_schema(),
            "config": _result_json_object_schema(),
            "cloudinit": _result_json_object_schema(),
        },
        required=["ok", "message", "server_id", "node", "vmid", "config", "cloudinit"],
    ),
    _PROXMOX_RESULT_ERROR_SCHEMA,
)

_PROXMOX_CLOUDINIT_PASSWORD_RESET_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **_RESULT_SUCCESS_OK_SCHEMA,
            "message": _result_string_schema(),
            "status": _result_string_schema(enum=["changed"]),
            "server_id": _result_string_schema(),
            "node": _result_string_schema(),
            "vmid": _result_integer_schema(),
            "set_password_task": _result_json_object_schema(),
            "regenerate_cloudinit": _result_json_object_schema(),
            "cloudinit": _result_json_object_schema(),
            "cloudinit_dump_user": _result_json_object_schema(),
            "verified": _result_boolean_schema(value=True),
        },
        required=[
            "ok",
            "message",
            "status",
            "server_id",
            "node",
            "vmid",
            "set_password_task",
            "regenerate_cloudinit",
            "cloudinit",
            "cloudinit_dump_user",
            "verified",
        ],
    ),
    _PROXMOX_RESULT_ERROR_SCHEMA,
)

_PROXMOX_PREFLIGHT_POOL_MOVE_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **_RESULT_SUCCESS_OK_SCHEMA,
            "message": _result_string_schema(),
            "server_id": _result_string_schema(),
            "source_pool": _result_json_object_schema(),
            "destination_pool": _result_json_object_schema(),
            "target_user": _result_json_object_schema(),
            "destination_permission": _result_json_object_schema(),
            "requested_vmids": _result_array_schema(items=_result_integer_schema()),
            "normalized_userid": _result_string_schema(),
        },
        required=[
            "ok",
            "message",
            "server_id",
            "source_pool",
            "destination_pool",
            "target_user",
            "destination_permission",
            "requested_vmids",
            "normalized_userid",
        ],
    ),
    _PROXMOX_RESULT_ERROR_SCHEMA,
)

_PROXMOX_POOL_MOVE_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **_RESULT_SUCCESS_OK_SCHEMA,
            "message": _result_string_schema(),
            "status": _result_string_schema(enum=["changed"]),
            "server_id": _result_string_schema(),
            "source_pool_before": _result_json_object_schema(),
            "destination_pool_before": _result_json_object_schema(),
            "add_to_destination": _result_json_object_schema(),
            "remove_from_source": _result_any_of(
                _result_json_object_schema(), _result_null_schema()
            ),
            "source_pool_after": _result_json_object_schema(),
            "destination_pool_after": _result_json_object_schema(),
            "results": _result_array_schema(
                items=_result_object_schema(
                    properties={
                        "vmid": _result_integer_schema(),
                        "status": _result_string_schema(enum=["changed"]),
                    },
                    required=["vmid", "status"],
                )
            ),
            "verified": _result_boolean_schema(value=True),
        },
        required=[
            "ok",
            "message",
            "status",
            "server_id",
            "source_pool_before",
            "destination_pool_before",
            "add_to_destination",
            "remove_from_source",
            "source_pool_after",
            "destination_pool_after",
            "results",
            "verified",
        ],
    ),
    _PROXMOX_RESULT_ERROR_SCHEMA,
)

_PROXMOX_PREFLIGHT_NIC_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **_RESULT_SUCCESS_OK_SCHEMA,
            "server_id": _result_string_schema(),
            "node": _result_string_schema(),
            "vmid": {"type": "integer"},
            "digest": _result_string_schema(),
            "net": _result_string_schema(),
            "before_net": _result_string_schema(),
            "link_state": _result_string_schema(enum=["up", "down"]),
            "auto_selected_net": _result_boolean_schema(),
            "nets": _result_array_schema(items=_PROXMOX_NET_RESULT_SCHEMA),
        },
        required=[
            "ok",
            "server_id",
            "node",
            "vmid",
            "digest",
            "net",
            "before_net",
            "link_state",
            "auto_selected_net",
            "nets",
        ],
    ),
    _PROXMOX_RESULT_ERROR_SCHEMA,
)

_PROXMOX_NIC_CHANGE_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **_RESULT_SUCCESS_OK_SCHEMA,
            "server_id": _result_string_schema(),
            "node": _result_string_schema(),
            "vmid": {"type": "integer"},
            "net": _result_string_schema(),
            "digest": _result_string_schema(),
            "status": _result_string_schema(enum=["changed", "no-op"]),
            "message": _result_string_schema(),
            "before_net": _result_string_schema(),
            "after_net": _result_string_schema(),
            "link_state": _result_string_schema(enum=["up", "down"]),
            "verified": _result_boolean_schema(value=True),
            "upid": _result_nullable_schema(_result_string_schema()),
            "task_status": _result_nullable_schema(_result_string_schema()),
            "task_exit_status": _result_nullable_schema(_result_string_schema()),
        },
        required=[
            "ok",
            "server_id",
            "node",
            "vmid",
            "net",
            "digest",
            "status",
            "message",
            "before_net",
            "after_net",
            "link_state",
            "verified",
            "upid",
            "task_status",
            "task_exit_status",
        ],
    ),
    _PROXMOX_RESULT_ERROR_SCHEMA,
)

_CHECK_BINARY_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **_RESULT_SUCCESS_OK_SCHEMA,
            "binary_name": _result_string_schema(),
            "found": _result_boolean_schema(),
            "path": _result_nullable_schema(_result_string_schema()),
        },
        required=["ok", "binary_name", "found", "path"],
    ),
    _WHM_RESULT_ERROR_SCHEMA,
)


_MAIL_AUTH_SUSPECT_ITEM_SCHEMA = _result_object_schema(
    properties={
        "email": _result_string_schema(),
        "count": {"type": "integer"},
    },
    required=["email", "count"],
)


_MAIL_AUTH_SUSPECTS_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **_RESULT_SUCCESS_OK_SCHEMA,
            "service": _result_string_schema(enum=["smtpauth", "imapd", "pop3d"]),
            "month": _result_string_schema(
                enum=[
                    "Jan",
                    "Feb",
                    "Mar",
                    "Apr",
                    "May",
                    "Jun",
                    "Jul",
                    "Aug",
                    "Sep",
                    "Oct",
                    "Nov",
                    "Dec",
                ]
            ),
            "day": {"type": "integer"},
            "ip": _result_string_schema(),
            "top_n": {"type": "integer"},
            "suspects": _result_array_schema(items=_MAIL_AUTH_SUSPECT_ITEM_SCHEMA),
            "raw_output": _result_string_schema(),
            "stderr": _result_string_schema(),
            "duration_ms": {"type": "integer"},
        },
        required=[
            "ok",
            "service",
            "month",
            "day",
            "ip",
            "top_n",
            "suspects",
            "raw_output",
            "stderr",
            "duration_ms",
        ],
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

_DOMAIN_INVENTORY_RESULT_SCHEMA = _result_object_schema(
    properties={
        "main_domain": _result_nullable_schema(_result_string_schema()),
        "addon_domains": _result_array_schema(items=_result_string_schema()),
        "parked_domains": _result_array_schema(items=_result_string_schema()),
        "sub_domains": _result_array_schema(items=_result_string_schema()),
    },
    required=["main_domain", "addon_domains", "parked_domains", "sub_domains"],
)

_PRIMARY_DOMAIN_PREFLIGHT_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **_RESULT_SUCCESS_OK_SCHEMA,
            "server_id": _result_string_schema(),
            "account": _ACCOUNT_RESULT_SCHEMA,
            "requested_domain": _result_string_schema(),
            "domain_owner": _result_nullable_schema(_result_string_schema()),
            "requested_domain_location": _result_string_schema(
                enum=["absent", "primary"]
            ),
            "safe_to_change": _result_boolean_schema(value=True),
            "domain_inventory": _DOMAIN_INVENTORY_RESULT_SCHEMA,
        },
        required=[
            "ok",
            "server_id",
            "account",
            "requested_domain",
            "domain_owner",
            "requested_domain_location",
            "safe_to_change",
            "domain_inventory",
        ],
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

# Firewall (unified CSF + Imunify) result schemas
_AVAILABLE_TOOLS_SCHEMA = _result_object_schema(
    properties={
        "csf": _result_boolean_schema(),
        "imunify": _result_boolean_schema(),
    },
    required=["csf", "imunify"],
)

_PREFLIGHT_FIREWALL_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **_RESULT_SUCCESS_OK_SCHEMA,
            "server_id": _result_string_schema(),
            "target": _result_string_schema(),
            "available_tools": _AVAILABLE_TOOLS_SCHEMA,
            "combined_verdict": _result_string_schema(),
            "matches": _result_array_schema(items=_result_string_schema()),
            "csf": {"type": "object"},
            "imunify": {"type": "object"},
        },
        required=[
            "ok",
            "server_id",
            "target",
            "available_tools",
            "combined_verdict",
            "matches",
        ],
        additional_properties=True,
    ),
    _WHM_RESULT_ERROR_SCHEMA,
)

_FIREWALL_RESULT_ITEM_SCHEMA = _result_object_schema(
    properties={
        "target": _result_string_schema(),
        "ok": _result_boolean_schema(),
        "status": _result_string_schema(enum=["changed", "no-op", "error"]),
        "available_tools": _AVAILABLE_TOOLS_SCHEMA,
        "csf": _result_nullable_schema({"type": "object"}),
        "imunify": _result_nullable_schema({"type": "object"}),
    },
    required=["target", "ok", "status", "available_tools"],
    additional_properties=True,
)

_FIREWALL_BATCH_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            "ok": _result_boolean_schema(),
            "available_tools": _AVAILABLE_TOOLS_SCHEMA,
            "results": _result_array_schema(items=_FIREWALL_RESULT_ITEM_SCHEMA),
        },
        required=["ok", "available_tools", "results"],
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
        name="update_workflow_todo",
        description="Internal workflow checklist surface used by backend-managed operational workflows.",
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
            "This checklist is managed by backend workflow orchestration, not direct model tool calls.",
            "Do not use it for simple READ questions or to narrate intermediate thinking.",
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
        name="whm_check_binary_exists",
        description="Check whether a specific binary exists over SSH on one resolved WHM server.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _SERVER_REF_PARAM,
                "binary_name": _BINARY_NAME_PARAM,
            },
            required=["server_ref", "binary_name"],
        ),
        execute=whm_check_binary_exists,
        prompt_hints=(
            "Use this after the WHM server has SSH credentials and a validated host key fingerprint.",
            "Successful results return `found` plus the resolved binary `path` when present.",
        ),
        result_schema=_CHECK_BINARY_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="whm_mail_log_failed_auth_suspects",
        description=(
            "Parse an LFD auth-block log line (smtpauth/imapd/pop3d) and search mail logs (typically /var/log/maillog*; also /var/log/exim_mainlog* for smtpauth) for failed authentication attempts from the parsed IP on that date, returning the top suspect mailbox usernames."
        ),
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _SERVER_REF_PARAM,
                "lfd_log_line": _string_param(
                    "Exact LFD/CSF log line (source of truth), e.g. `lfd: (pop3d) Failed POP3 login from 1.2.3.4 ... - Tue Mar 31 12:23:11 2026`. The tool hard-guards to smtpauth, imapd, and pop3d."
                ),
                "top_n": _integer_param(
                    "Maximum number of suspect usernames to return.",
                    minimum=1,
                    maximum=200,
                    default=50,
                ),
                "include_raw_output": {
                    "type": "boolean",
                    "description": "Include the copy/paste-ready aggregated output lines in `raw_output`. Defaults to false to reduce stored sensitive output.",
                    "default": False,
                },
            },
            required=["server_ref", "lfd_log_line"],
        ),
        execute=whm_mail_log_failed_auth_suspects,
        prompt_hints=(
            "Use only for follow-up on firewall blocks caused by failed SMTP AUTH (smtpauth), IMAP (imapd), or POP3 (pop3d) logins.",
            "Provide the exact LFD log line so the tool can parse month/day/IP reliably.",
            "This tool may take up to ~2 minutes on large mail logs; wait for the result.",
            "The output includes mailbox identifiers and should be treated as sensitive operational data.",
            "Return `raw_output` to the user in a copy/paste-friendly code block.",
        ),
        result_schema=_MAIL_AUTH_SUSPECTS_RESULT_SCHEMA,
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
        name="whm_preflight_primary_domain_change",
        description="Fetch the current WHM account state and verify that a requested primary domain is safe to use before changing it.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _SERVER_REF_PARAM,
                "username": _USERNAME_PARAM,
                "new_domain": _DOMAIN_PARAM,
            },
            required=["server_ref", "username", "new_domain"],
        ),
        execute=whm_preflight_primary_domain_change,
        prompt_hints=(
            "Run this before `whm_change_primary_domain` and summarize the current primary domain plus any existing addon-domain conflicts.",
            "Successful results return `account`, `requested_domain`, `requested_domain_location`, `domain_owner`, and `domain_inventory`.",
        ),
        result_schema=_PRIMARY_DOMAIN_PREFLIGHT_RESULT_SCHEMA,
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
        workflow_family="whm-account-lifecycle",
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
        workflow_family="whm-account-lifecycle",
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
        workflow_family="whm-account-contact-email",
    ),
    ToolDefinition(
        name="whm_change_primary_domain",
        description="Change the WHM primary domain for one exact account after a domain-specific preflight.",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _SERVER_REF_PARAM,
                "username": _USERNAME_PARAM,
                "new_domain": _DOMAIN_PARAM,
                "reason": _REASON_PARAM,
            },
            required=["server_ref", "username", "new_domain", "reason"],
        ),
        execute=whm_change_primary_domain,
        prompt_hints=(
            "Run `whm_preflight_primary_domain_change` first and summarize the current primary domain plus any addon-domain conflicts before proposing this tool.",
            "Idempotent result contract: returns `status` `no-op` if the primary domain already matches, or `status` `changed` only after postflight verifies the new primary domain and DNS zone.",
            "Failures return `ok: false` with `error_code` and `message`.",
        ),
        result_schema=_ACCOUNT_CHANGE_RESULT_SCHEMA,
        workflow_family="whm-account-primary-domain",
    ),
    # -------------------------------------------------------------------------
    # Unified Firewall Tools (CSF + Imunify)
    # -------------------------------------------------------------------------
    ToolDefinition(
        name="whm_preflight_firewall_entries",
        description="Inspect the current firewall state (CSF and/or Imunify) for one target before a firewall change. This is the preferred preflight tool for IP operations.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _SERVER_REF_PARAM,
                "target": _CSF_TARGET_PARAM,
            },
            required=["server_ref", "target"],
        ),
        execute=whm_preflight_firewall_entries,
        prompt_hints=(
            "Use this as the primary tool for checking IP status before firewall changes.",
            "Automatically detects which firewall tools (CSF, Imunify, or both) are available.",
            "Returns `combined_verdict` plus individual `csf` and `imunify` results when available.",
            "After a successful run, summarize the combined verdict and available tools before proposing changes.",
        ),
        result_schema=_PREFLIGHT_FIREWALL_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="whm_firewall_unblock",
        description="Remove firewall block entries from CSF and/or Imunify (based on availability) for one or more targets.",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _SERVER_REF_PARAM,
                "targets": _CSF_TARGETS_PARAM,
                "reason": _REASON_PARAM,
            },
            required=["server_ref", "targets", "reason"],
        ),
        execute=whm_firewall_unblock,
        prompt_hints=(
            "Run `whm_preflight_firewall_entries` once per target before proposing this tool.",
            "Automatically operates on all available firewall tools (CSF, Imunify, or both).",
            "Batch result contract: returns `results` per target with `status` `changed`, `no-op`, or `error`.",
            "If CSF indicates the block reason is an LFD auth failure (smtpauth/imapd/pop3d), the tool will also return `failed_auth_suspects` for that target.",
        ),
        result_schema=_FIREWALL_BATCH_RESULT_SCHEMA,
        workflow_family="whm-firewall-batch-change",
    ),
    ToolDefinition(
        name="whm_firewall_allowlist_add_ttl",
        description="Temporarily add one or more IPv4 targets to the firewall allowlist (CSF and/or Imunify based on availability).",
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
        execute=whm_firewall_allowlist_add_ttl,
        prompt_hints=(
            "Run `whm_preflight_firewall_entries` once per target before proposing this tool.",
            "Automatically operates on all available firewall tools (CSF, Imunify, or both).",
            "Batch result contract: returns `results` per target with `status` `changed`, `no-op`, or `error`.",
        ),
        result_schema=_FIREWALL_BATCH_RESULT_SCHEMA,
        workflow_family="whm-firewall-batch-change",
    ),
    ToolDefinition(
        name="whm_firewall_allowlist_remove",
        description="Remove one or more targets from the firewall allowlist (CSF and/or Imunify based on availability).",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _SERVER_REF_PARAM,
                "targets": _CSF_TARGETS_PARAM,
                "reason": _REASON_PARAM,
            },
            required=["server_ref", "targets", "reason"],
        ),
        execute=whm_firewall_allowlist_remove,
        prompt_hints=(
            "Run `whm_preflight_firewall_entries` once per target before proposing this tool.",
            "Automatically operates on all available firewall tools (CSF, Imunify, or both).",
            "Batch result contract: returns `results` per target with `status` `changed`, `no-op`, or `error`.",
        ),
        result_schema=_FIREWALL_BATCH_RESULT_SCHEMA,
        workflow_family="whm-firewall-batch-change",
    ),
    ToolDefinition(
        name="whm_firewall_denylist_add_ttl",
        description="Temporarily add one or more IPv4 targets to the firewall denylist/blacklist (CSF and/or Imunify based on availability).",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _SERVER_REF_PARAM,
                "targets": _string_array_param(
                    "One or more IPv4 addresses to deny/block temporarily. This TTL tool does not accept CIDRs, hostnames, or IPv6 targets.",
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
        execute=whm_firewall_denylist_add_ttl,
        prompt_hints=(
            "Run `whm_preflight_firewall_entries` once per target before proposing this tool.",
            "Automatically operates on all available firewall tools (CSF, Imunify, or both).",
            "Batch result contract: returns `results` per target with `status` `changed`, `no-op`, or `error`.",
        ),
        result_schema=_FIREWALL_BATCH_RESULT_SCHEMA,
        workflow_family="whm-firewall-batch-change",
    ),
    ToolDefinition(
        name="proxmox_list_servers",
        description="List configured Proxmox servers using safe fields only.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(properties={}, required=[]),
        execute=proxmox_list_servers,
        prompt_hints=(
            "Use this when the user has not supplied a server_ref or when you need Proxmox server choices for disambiguation.",
            "Successful results return `servers` and never include the API token secret.",
        ),
        result_schema=_PROXMOX_SERVERS_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="proxmox_get_vm_status_current",
        description="Read the current Proxmox VM runtime status for one exact VM.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _PROXMOX_SERVER_REF_PARAM,
                "node": _PROXMOX_NODE_PARAM,
                "vmid": _PROXMOX_VMID_PARAM,
            },
            required=["server_ref", "node", "vmid"],
        ),
        execute=proxmox_get_vm_status_current,
        prompt_hints=(
            "Use this to inspect the current VM state before proposing a Proxmox change.",
            "Successful results return `data` with the upstream VM status payload.",
        ),
        result_schema=_PROXMOX_VM_READ_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="proxmox_get_vm_config",
        description="Read the Proxmox VM configuration and hardware layout for one exact VM.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _PROXMOX_SERVER_REF_PARAM,
                "node": _PROXMOX_NODE_PARAM,
                "vmid": _PROXMOX_VMID_PARAM,
            },
            required=["server_ref", "node", "vmid"],
        ),
        execute=proxmox_get_vm_config,
        prompt_hints=(
            "Use this when you need the VM config or the digest before a follow-up change.",
            "Successful results return `data` with the upstream QEMU config payload.",
        ),
        result_schema=_PROXMOX_VM_READ_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="proxmox_get_vm_pending",
        description="Read the pending Proxmox VM configuration changes for one exact VM.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _PROXMOX_SERVER_REF_PARAM,
                "node": _PROXMOX_NODE_PARAM,
                "vmid": _PROXMOX_VMID_PARAM,
            },
            required=["server_ref", "node", "vmid"],
        ),
        execute=proxmox_get_vm_pending,
        prompt_hints=(
            "Use this to inspect queued VM changes before deciding whether a change is still needed.",
            "Successful results return `data` with the upstream pending-change payload.",
        ),
        result_schema=_PROXMOX_VM_READ_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="proxmox_get_user_by_email",
        description="Resolve a Proxmox user account from an exact email address.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _PROXMOX_SERVER_REF_PARAM,
                "email": _PROXMOX_EMAIL_PARAM,
            },
            required=["server_ref", "email"],
        ),
        execute=proxmox_get_user_by_email,
        prompt_hints=(
            "Use this to confirm the exact Proxmox user identity before any pool membership change.",
            "Successful results return `data` with the upstream user payload.",
        ),
        result_schema=_PROXMOX_USER_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="proxmox_preflight_vm_cloudinit_password_reset",
        description="Inspect Proxmox VM configuration and cloud-init state before resetting the cloud-init password.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _PROXMOX_SERVER_REF_PARAM,
                "node": _PROXMOX_NODE_PARAM,
                "vmid": _PROXMOX_VMID_PARAM,
            },
            required=["server_ref", "node", "vmid"],
        ),
        execute=proxmox_preflight_vm_cloudinit_password_reset,
        prompt_hints=(
            "Run this before `proxmox_reset_vm_cloudinit_password` and summarize the VM configuration plus cloud-init state first.",
            "Successful results return `config` and `cloudinit` evidence for the exact VM.",
        ),
        result_schema=_PROXMOX_PREFLIGHT_CLOUDINIT_PASSWORD_RESET_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="proxmox_reset_vm_cloudinit_password",
        description="Reset the Proxmox VM cloud-init password after the exact VM has been preflighted.",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _PROXMOX_SERVER_REF_PARAM,
                "node": _PROXMOX_NODE_PARAM,
                "vmid": _PROXMOX_VMID_PARAM,
                "new_password": _PROXMOX_CLOUDINIT_PASSWORD_PARAM,
                "reason": _REASON_PARAM,
            },
            required=["server_ref", "node", "vmid", "new_password", "reason"],
        ),
        execute=proxmox_reset_vm_cloudinit_password,
        prompt_hints=(
            "Run `proxmox_preflight_vm_cloudinit_password_reset` first and reuse the same server_ref, node, and vmid.",
            "Idempotent result contract: returns `status` `changed` only after postflight confirms the cloud-init password reset.",
        ),
        result_schema=_PROXMOX_CLOUDINIT_PASSWORD_RESET_RESULT_SCHEMA,
        workflow_family="proxmox-vm-cloudinit-password-reset",
    ),
    ToolDefinition(
        name="proxmox_preflight_move_vms_between_pools",
        description="Inspect source and destination Proxmox pools before moving exact VMIDs between them.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _PROXMOX_SERVER_REF_PARAM,
                "source_pool": _PROXMOX_POOL_PARAM,
                "destination_pool": _PROXMOX_POOL_PARAM,
                "vmids": _PROXMOX_VMID_LIST_PARAM,
                "email": _PROXMOX_EMAIL_PARAM,
            },
            required=[
                "server_ref",
                "source_pool",
                "destination_pool",
                "vmids",
                "email",
            ],
        ),
        execute=proxmox_preflight_move_vms_between_pools,
        prompt_hints=(
            "Run this before `proxmox_move_vms_between_pools` and confirm the exact source pool, destination pool, VMIDs, and Proxmox user identity first.",
            "Successful results return the source pool, destination pool, target user, and requested VMIDs.",
        ),
        result_schema=_PROXMOX_PREFLIGHT_POOL_MOVE_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="proxmox_move_vms_between_pools",
        description="Move exact Proxmox VMIDs from one pool to another after preflight verification.",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _PROXMOX_SERVER_REF_PARAM,
                "source_pool": _PROXMOX_POOL_PARAM,
                "destination_pool": _PROXMOX_POOL_PARAM,
                "vmids": _PROXMOX_VMID_LIST_PARAM,
                "email": _PROXMOX_EMAIL_PARAM,
                "reason": _REASON_PARAM,
            },
            required=[
                "server_ref",
                "source_pool",
                "destination_pool",
                "vmids",
                "email",
                "reason",
            ],
        ),
        execute=proxmox_move_vms_between_pools,
        prompt_hints=(
            "Run `proxmox_preflight_move_vms_between_pools` first and reuse the same server_ref, source_pool, destination_pool, vmids, and email.",
            "Idempotent result contract: returns `status` `changed` only after postflight confirms the VMIDs moved into the destination pool and out of the source pool.",
        ),
        result_schema=_PROXMOX_POOL_MOVE_RESULT_SCHEMA,
        workflow_family="proxmox-pool-membership-move",
    ),
    ToolDefinition(
        name="proxmox_validate_server",
        description="Validate a Proxmox server reference by calling a lightweight API check.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={"server_ref": _PROXMOX_SERVER_REF_PARAM},
            required=["server_ref"],
        ),
        execute=proxmox_validate_server,
        prompt_hints=(
            "Use this for Proxmox connectivity or credential validation.",
            "Success returns `ok: true` and `message: ok`; failures return `error_code`, `message`, and possibly `choices`.",
        ),
        result_schema=_PROXMOX_VALIDATE_SERVER_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="proxmox_preflight_vm_nic_toggle",
        description="Read the current Proxmox VM NIC state and strict digest before enabling or disabling one QEMU NIC.",
        risk=ToolRisk.READ,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _PROXMOX_SERVER_REF_PARAM,
                "node": _PROXMOX_NODE_PARAM,
                "vmid": _PROXMOX_VMID_PARAM,
                "net": {
                    **_PROXMOX_NET_PARAM,
                    "description": "Optional Proxmox NIC key such as net0. If omitted and the VM has exactly one NIC, the tool auto-selects it.",
                },
            },
            required=["server_ref", "node", "vmid"],
        ),
        execute=proxmox_preflight_vm_nic_toggle,
        prompt_hints=(
            "Run this before `proxmox_disable_vm_nic` or `proxmox_enable_vm_nic` and summarize the current NIC link state plus digest.",
            "If multiple NICs exist and `net` is omitted, the tool returns `net_selection_required` and a `nets` list for user choice.",
        ),
        result_schema=_PROXMOX_PREFLIGHT_NIC_RESULT_SCHEMA,
    ),
    ToolDefinition(
        name="proxmox_disable_vm_nic",
        description="Disable one Proxmox QEMU VM NIC by setting link_down after matching preflight evidence and digest.",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _PROXMOX_SERVER_REF_PARAM,
                "node": _PROXMOX_NODE_PARAM,
                "vmid": _PROXMOX_VMID_PARAM,
                "net": _PROXMOX_NET_PARAM,
                "digest": _PROXMOX_DIGEST_PARAM,
                "reason": _REASON_PARAM,
            },
            required=["server_ref", "node", "vmid", "net", "digest", "reason"],
        ),
        execute=proxmox_disable_vm_nic,
        prompt_hints=(
            "Run `proxmox_preflight_vm_nic_toggle` first and use the same server_ref, node, vmid, net, and digest.",
            "Idempotent result contract: returns `status` `no-op` if the NIC is already disabled, or `status` `changed` after task polling and verification confirm `link_down=1`.",
        ),
        result_schema=_PROXMOX_NIC_CHANGE_RESULT_SCHEMA,
        workflow_family="proxmox-vm-nic-connectivity",
    ),
    ToolDefinition(
        name="proxmox_enable_vm_nic",
        description="Enable one Proxmox QEMU VM NIC by removing link_down after matching preflight evidence and digest.",
        risk=ToolRisk.CHANGE,
        parameters_schema=_object_schema(
            properties={
                "server_ref": _PROXMOX_SERVER_REF_PARAM,
                "node": _PROXMOX_NODE_PARAM,
                "vmid": _PROXMOX_VMID_PARAM,
                "net": _PROXMOX_NET_PARAM,
                "digest": _PROXMOX_DIGEST_PARAM,
                "reason": _REASON_PARAM,
            },
            required=["server_ref", "node", "vmid", "net", "digest", "reason"],
        ),
        execute=proxmox_enable_vm_nic,
        prompt_hints=(
            "Run `proxmox_preflight_vm_nic_toggle` first and use the same server_ref, node, vmid, net, and digest.",
            "Idempotent result contract: returns `status` `no-op` if the NIC is already enabled, or `status` `changed` after task polling and verification confirm `link_down` is absent.",
        ),
        result_schema=_PROXMOX_NIC_CHANGE_RESULT_SCHEMA,
        workflow_family="proxmox-vm-nic-connectivity",
    ),
)
_MVP_TOOL_INDEX = {tool.name: tool for tool in _MVP_TOOLS}


def get_tool_registry() -> tuple[ToolDefinition, ...]:
    return _MVP_TOOLS


def get_tool_definition(tool_name: str) -> ToolDefinition | None:
    return _MVP_TOOL_INDEX.get(tool_name)


def get_tool_names() -> tuple[str, ...]:
    return tuple(tool.name for tool in _MVP_TOOLS)
