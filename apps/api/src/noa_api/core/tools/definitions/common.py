"""Common tool definitions: time, date, workflow todo."""

from __future__ import annotations

from noa_api.core.tools.demo_tools import get_current_date, get_current_time
from noa_api.core.tools.schema_builders import _object_schema
from noa_api.core.tools.schemas.common import TODO_ITEM_SCHEMA, WORKFLOW_RESULT_SCHEMA
from noa_api.core.tools.types import ToolDefinition
from noa_api.core.tools.workflow_todo import update_workflow_todo
from noa_api.storage.postgres.lifecycle import ToolRisk

COMMON_TOOLS: tuple[ToolDefinition, ...] = (
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
                    "items": TODO_ITEM_SCHEMA,
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
        result_schema=WORKFLOW_RESULT_SCHEMA,
    ),
)
