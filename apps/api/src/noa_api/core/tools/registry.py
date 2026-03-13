from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from noa_api.core.tools.demo_tools import (
    get_current_date,
    get_current_time,
    set_demo_flag,
)
from noa_api.core.tools.whm.preflight_tools import (
    whm_preflight_account,
    whm_preflight_csf_entries,
)
from noa_api.core.tools.whm.account_change_tools import (
    whm_change_contact_email,
    whm_suspend_account,
    whm_unsuspend_account,
)
from noa_api.core.tools.whm.csf_change_tools import (
    whm_csf_allowlist_add_ttl,
    whm_csf_allowlist_remove,
    whm_csf_denylist_add_ttl,
    whm_csf_unblock,
)
from noa_api.core.tools.whm.read_tools import (
    whm_list_accounts,
    whm_list_servers,
    whm_search_accounts,
    whm_validate_server,
)
from noa_api.core.tools.workflow_todo import update_workflow_todo
from noa_api.storage.postgres.lifecycle import ToolRisk

ToolExecutor = Callable[..., Awaitable[dict[str, Any]]]
ToolParametersSchema = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    risk: ToolRisk
    parameters_schema: ToolParametersSchema
    execute: ToolExecutor


_MVP_TOOLS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="get_current_time",
        description="Get the server current time.",
        risk=ToolRisk.READ,
        parameters_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        execute=get_current_time,
    ),
    ToolDefinition(
        name="get_current_date",
        description="Get the server current date.",
        risk=ToolRisk.READ,
        parameters_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        execute=get_current_date,
    ),
    ToolDefinition(
        name="set_demo_flag",
        description="Set a demo marker flag in persistence.",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {"key": {"type": "string"}, "value": {}},
            "required": ["key", "value"],
            "additionalProperties": False,
        },
        execute=set_demo_flag,
    ),
    ToolDefinition(
        name="update_workflow_todo",
        description="Update the workflow TODO checklist shown in chat.",
        risk=ToolRisk.READ,
        parameters_schema={
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": [
                                    "pending",
                                    "in_progress",
                                    "completed",
                                    "cancelled",
                                ],
                            },
                            "priority": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                            },
                        },
                        "required": ["content", "status", "priority"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["todos"],
            "additionalProperties": False,
        },
        execute=update_workflow_todo,
    ),
    ToolDefinition(
        name="whm_list_servers",
        description="List configured WHM servers (safe fields only).",
        risk=ToolRisk.READ,
        parameters_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        execute=whm_list_servers,
    ),
    ToolDefinition(
        name="whm_validate_server",
        description="Validate WHM server credentials by calling applist.",
        risk=ToolRisk.READ,
        parameters_schema={
            "type": "object",
            "properties": {"server_ref": {"type": "string", "minLength": 1}},
            "required": ["server_ref"],
            "additionalProperties": False,
        },
        execute=whm_validate_server,
    ),
    ToolDefinition(
        name="whm_list_accounts",
        description="List WHM accounts for a server.",
        risk=ToolRisk.READ,
        parameters_schema={
            "type": "object",
            "properties": {"server_ref": {"type": "string", "minLength": 1}},
            "required": ["server_ref"],
            "additionalProperties": False,
        },
        execute=whm_list_accounts,
    ),
    ToolDefinition(
        name="whm_search_accounts",
        description="Search WHM accounts by username or domain.",
        risk=ToolRisk.READ,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string", "minLength": 1},
                "query": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["server_ref", "query"],
            "additionalProperties": False,
        },
        execute=whm_search_accounts,
    ),
    ToolDefinition(
        name="whm_preflight_account",
        description="Preflight a WHM account state before change operations.",
        risk=ToolRisk.READ,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string", "minLength": 1},
                "username": {"type": "string", "minLength": 1},
            },
            "required": ["server_ref", "username"],
            "additionalProperties": False,
        },
        execute=whm_preflight_account,
    ),
    ToolDefinition(
        name="whm_preflight_csf_entries",
        description="Preflight CSF evidence for a target (ip/cidr/hostname).",
        risk=ToolRisk.READ,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string", "minLength": 1},
                "target": {"type": "string", "minLength": 1},
            },
            "required": ["server_ref", "target"],
            "additionalProperties": False,
        },
        execute=whm_preflight_csf_entries,
    ),
    ToolDefinition(
        name="whm_suspend_account",
        description="Suspend a WHM account (idempotent, requires approval).",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string", "minLength": 1},
                "username": {"type": "string", "minLength": 1},
                "reason": {"type": "string", "minLength": 1},
            },
            "required": ["server_ref", "username", "reason"],
            "additionalProperties": False,
        },
        execute=whm_suspend_account,
    ),
    ToolDefinition(
        name="whm_unsuspend_account",
        description="Unsuspend a WHM account (idempotent, requires approval).",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string", "minLength": 1},
                "username": {"type": "string", "minLength": 1},
                "reason": {"type": "string", "minLength": 1},
            },
            "required": ["server_ref", "username", "reason"],
            "additionalProperties": False,
        },
        execute=whm_unsuspend_account,
    ),
    ToolDefinition(
        name="whm_change_contact_email",
        description="Change a WHM account contact email (idempotent, requires approval).",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string", "minLength": 1},
                "username": {"type": "string", "minLength": 1},
                "new_email": {"type": "string", "minLength": 1},
                "reason": {"type": "string", "minLength": 1},
            },
            "required": ["server_ref", "username", "new_email", "reason"],
            "additionalProperties": False,
        },
        execute=whm_change_contact_email,
    ),
    ToolDefinition(
        name="whm_csf_unblock",
        description="Unblock IPs in CSF if currently blocked (requires approval).",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string", "minLength": 1},
                "targets": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                },
                "reason": {"type": "string", "minLength": 1},
            },
            "required": ["server_ref", "targets", "reason"],
            "additionalProperties": False,
        },
        execute=whm_csf_unblock,
    ),
    ToolDefinition(
        name="whm_csf_allowlist_remove",
        description="Remove IPs from CSF allowlist if present (requires approval).",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string", "minLength": 1},
                "targets": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                },
                "reason": {"type": "string", "minLength": 1},
            },
            "required": ["server_ref", "targets", "reason"],
            "additionalProperties": False,
        },
        execute=whm_csf_allowlist_remove,
    ),
    ToolDefinition(
        name="whm_csf_allowlist_add_ttl",
        description="Temporarily allowlist IPs in CSF for a duration (requires approval).",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string", "minLength": 1},
                "targets": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                },
                "duration_minutes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 525600,
                },
                "reason": {"type": "string", "minLength": 1},
            },
            "required": ["server_ref", "targets", "duration_minutes", "reason"],
            "additionalProperties": False,
        },
        execute=whm_csf_allowlist_add_ttl,
    ),
    ToolDefinition(
        name="whm_csf_denylist_add_ttl",
        description="Temporarily denylist IPs in CSF for a duration (requires approval).",
        risk=ToolRisk.CHANGE,
        parameters_schema={
            "type": "object",
            "properties": {
                "server_ref": {"type": "string", "minLength": 1},
                "targets": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                },
                "duration_minutes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 525600,
                },
                "reason": {"type": "string", "minLength": 1},
            },
            "required": ["server_ref", "targets", "duration_minutes", "reason"],
            "additionalProperties": False,
        },
        execute=whm_csf_denylist_add_ttl,
    ),
)
_MVP_TOOL_INDEX = {tool.name: tool for tool in _MVP_TOOLS}


def get_tool_registry() -> tuple[ToolDefinition, ...]:
    return _MVP_TOOLS


def get_tool_definition(tool_name: str) -> ToolDefinition | None:
    return _MVP_TOOL_INDEX.get(tool_name)


def get_tool_names() -> tuple[str, ...]:
    return tuple(tool.name for tool in _MVP_TOOLS)
