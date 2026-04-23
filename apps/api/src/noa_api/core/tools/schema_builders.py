from __future__ import annotations

from typing import Any

from noa_api.core.tools.types import ToolParametersSchema, ToolResultSchema


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
    from noa_api.core.tools.schemas.common import RESULT_SUCCESS_OK_SCHEMA

    return _result_object_schema(
        properties={
            **RESULT_SUCCESS_OK_SCHEMA,
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
