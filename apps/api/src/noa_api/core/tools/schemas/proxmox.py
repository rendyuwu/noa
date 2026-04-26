"""Proxmox-specific result schema constants."""

from __future__ import annotations

from noa_api.core.tools.schemas.common import RESULT_SUCCESS_OK_SCHEMA
from noa_api.core.tools.schema_builders import (
    _integer_array_param,
    _integer_param,
    _result_any_of,
    _result_array_schema,
    _result_boolean_schema,
    _result_integer_schema,
    _result_json_object_schema,
    _result_null_schema,
    _result_nullable_schema,
    _result_object_schema,
    _result_string_schema,
    _result_upstream_response_schema,
    _result_vm_data_schema,
    _string_param,
)

# ---------------------------------------------------------------------------
# Proxmox parameter schemas
# ---------------------------------------------------------------------------

PROXMOX_SERVER_REF_PARAM = _string_param(
    "Server reference that resolves to exactly one configured Proxmox server. Use a server name, UUID, or base URL host and ask the user to choose if the tool returns choices.",
    format_name="proxmox-server-ref",
)

PROXMOX_NODE_PARAM = _string_param(
    "Exact Proxmox node name that hosts the QEMU VM.",
    pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
)

PROXMOX_VMID_PARAM = _integer_param(
    "Numeric Proxmox QEMU VM ID.",
    minimum=1,
)

PROXMOX_NET_PARAM = _string_param(
    "Exact Proxmox NIC key such as net0 or net1.",
    pattern=r"^net\d+$",
)

PROXMOX_DIGEST_PARAM = _string_param(
    "Exact Proxmox config digest returned by proxmox_preflight_vm_nic_toggle.",
)

PROXMOX_EMAIL_PARAM = _string_param(
    "Exact email address used to identify the Proxmox user account.",
    format_name="email",
)

PROXMOX_OLD_EMAIL_PARAM = _string_param(
    "Exact email address of the current (old) PIC who owns the source pool.",
    format_name="email",
)

PROXMOX_NEW_EMAIL_PARAM = _string_param(
    "Exact email address of the new PIC who owns the destination pool.",
    format_name="email",
)

PROXMOX_POOL_PARAM = _string_param(
    "Exact Proxmox pool name.",
)

PROXMOX_VMID_LIST_PARAM = _integer_array_param(
    "One or more exact Proxmox QEMU VM IDs. Preserve the user-provided VMIDs and do not invent values.",
    unique_items=True,
)

PROXMOX_CLOUDINIT_PASSWORD_PARAM = _string_param(
    "New Proxmox cloud-init password to set for the VM.",
)

# ---------------------------------------------------------------------------
# Proxmox server choice / error
# ---------------------------------------------------------------------------

PROXMOX_SERVER_CHOICE_SCHEMA = _result_object_schema(
    properties={
        "id": _result_string_schema(),
        "name": _result_string_schema(),
        "base_url": _result_string_schema(),
    },
    required=["id", "name", "base_url"],
)

PROXMOX_NET_RESULT_SCHEMA = _result_object_schema(
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

PROXMOX_RESULT_ERROR_SCHEMA = _result_object_schema(
    properties={
        "ok": _result_boolean_schema(value=False),
        "error_code": _result_string_schema(),
        "message": _result_string_schema(),
        "choices": _result_array_schema(items=PROXMOX_SERVER_CHOICE_SCHEMA),
        "nets": _result_array_schema(items=PROXMOX_NET_RESULT_SCHEMA),
        "server_id": _result_string_schema(),
        "node": _result_string_schema(),
        "vmid": {"type": "integer"},
        "digest": _result_string_schema(),
    },
    required=["ok", "error_code", "message"],
)

PROXMOX_SERVER_SAFE_RESULT_SCHEMA = _result_object_schema(
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

# ---------------------------------------------------------------------------
# Proxmox result schemas
# ---------------------------------------------------------------------------

PROXMOX_SERVERS_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **RESULT_SUCCESS_OK_SCHEMA,
            "servers": _result_array_schema(items=PROXMOX_SERVER_SAFE_RESULT_SCHEMA),
        },
        required=["ok", "servers"],
    ),
    PROXMOX_RESULT_ERROR_SCHEMA,
)

PROXMOX_VALIDATE_SERVER_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **RESULT_SUCCESS_OK_SCHEMA,
            "message": _result_string_schema(),
        },
        required=["ok", "message"],
    ),
    PROXMOX_RESULT_ERROR_SCHEMA,
)

PROXMOX_VM_READ_RESULT_SCHEMA = _result_any_of(
    _result_upstream_response_schema(data_schema=_result_vm_data_schema()),
    PROXMOX_RESULT_ERROR_SCHEMA,
)

PROXMOX_USER_RESULT_SCHEMA = _result_any_of(
    _result_upstream_response_schema(data_schema=_result_json_object_schema()),
    PROXMOX_RESULT_ERROR_SCHEMA,
)

PROXMOX_PREFLIGHT_CLOUDINIT_PASSWORD_RESET_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **RESULT_SUCCESS_OK_SCHEMA,
            "message": _result_string_schema(),
            "server_id": _result_string_schema(),
            "node": _result_string_schema(),
            "vmid": _result_integer_schema(),
            "config": _result_json_object_schema(),
            "cloudinit": _result_json_object_schema(),
        },
        required=["ok", "message", "server_id", "node", "vmid", "config", "cloudinit"],
    ),
    PROXMOX_RESULT_ERROR_SCHEMA,
)

PROXMOX_CLOUDINIT_PASSWORD_RESET_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **RESULT_SUCCESS_OK_SCHEMA,
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
    PROXMOX_RESULT_ERROR_SCHEMA,
)

PROXMOX_PREFLIGHT_POOL_MOVE_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **RESULT_SUCCESS_OK_SCHEMA,
            "message": _result_string_schema(),
            "server_id": _result_string_schema(),
            "source_pool": _result_json_object_schema(),
            "destination_pool": _result_json_object_schema(),
            "old_user": _result_json_object_schema(),
            "new_user": _result_json_object_schema(),
            "source_permission": _result_json_object_schema(),
            "destination_permission": _result_json_object_schema(),
            "requested_vmids": _result_array_schema(items=_result_integer_schema()),
            "normalized_old_userid": _result_string_schema(),
            "normalized_new_userid": _result_string_schema(),
        },
        required=[
            "ok",
            "message",
            "server_id",
            "source_pool",
            "destination_pool",
            "old_user",
            "new_user",
            "source_permission",
            "destination_permission",
            "requested_vmids",
            "normalized_old_userid",
            "normalized_new_userid",
        ],
    ),
    PROXMOX_RESULT_ERROR_SCHEMA,
)

PROXMOX_POOL_MOVE_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **RESULT_SUCCESS_OK_SCHEMA,
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
    PROXMOX_RESULT_ERROR_SCHEMA,
)

PROXMOX_PREFLIGHT_NIC_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **RESULT_SUCCESS_OK_SCHEMA,
            "server_id": _result_string_schema(),
            "node": _result_string_schema(),
            "vmid": {"type": "integer"},
            "digest": _result_string_schema(),
            "net": _result_string_schema(),
            "before_net": _result_string_schema(),
            "link_state": _result_string_schema(enum=["up", "down"]),
            "auto_selected_net": _result_boolean_schema(),
            "nets": _result_array_schema(items=PROXMOX_NET_RESULT_SCHEMA),
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
    PROXMOX_RESULT_ERROR_SCHEMA,
)

PROXMOX_NIC_CHANGE_RESULT_SCHEMA = _result_any_of(
    _result_object_schema(
        properties={
            **RESULT_SUCCESS_OK_SCHEMA,
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
    PROXMOX_RESULT_ERROR_SCHEMA,
)
