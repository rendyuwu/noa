from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from noa_api.core.tools.registry import ToolDefinition


class ToolResultValidationError(Exception):
    def __init__(self, *, details: list[str]) -> None:
        super().__init__("Tool returned an invalid result")
        self.error = "Tool returned an invalid result"
        self.error_code = "invalid_tool_result"
        self.details = tuple(details)


def validate_tool_result(*, tool: ToolDefinition, result: dict[str, object]) -> None:
    if tool.result_schema is None:
        return

    problems = _validate_schema(tool.result_schema, value=result, path="result")
    if problems:
        raise ToolResultValidationError(details=problems[:10])


def _validate_schema(schema: dict[str, Any], *, value: object, path: str) -> list[str]:
    variants = schema.get("anyOf")
    if isinstance(variants, list):
        variant_errors = [
            _validate_schema(variant, value=value, path=path)
            for variant in variants
            if isinstance(variant, dict)
        ]
        if any(not errors for errors in variant_errors):
            return []
        return (
            min(variant_errors, key=len)
            if variant_errors
            else [f"{_format_path(path)} is invalid"]
        )

    schema_type = schema.get("type")
    if schema_type == "object":
        return _validate_object(schema, value=value, path=path)
    if schema_type == "array":
        return _validate_array(schema, value=value, path=path)
    if schema_type == "string":
        return _validate_string(schema, value=value, path=path)
    if schema_type == "integer":
        return _validate_integer(schema, value=value, path=path)
    if schema_type == "boolean":
        return _validate_boolean(schema, value=value, path=path)

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
        return [
            f"{_format_path(path)} must be one of {', '.join(map(str, enum_values))}"
        ]
    return []


def _validate_object(schema: dict[str, Any], *, value: object, path: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{_format_path(path)} must be an object"]

    problems: list[str] = []
    properties = schema.get("properties")
    property_schemas = properties if isinstance(properties, dict) else {}
    required = schema.get("required")
    required_keys = required if isinstance(required, list) else []

    for key in required_keys:
        if isinstance(key, str) and key not in value:
            problems.append(f"Missing required field '{_path_suffix(path, key)}'")

    if schema.get("additionalProperties") is False:
        for key in value:
            if isinstance(key, str) and key not in property_schemas:
                problems.append(f"Unexpected field '{_path_suffix(path, key)}'")

    for key, property_schema in property_schemas.items():
        if (
            not isinstance(key, str)
            or key not in value
            or not isinstance(property_schema, dict)
        ):
            continue
        problems.extend(
            _validate_schema(property_schema, value=value[key], path=f"{path}.{key}")
        )

    return problems


def _validate_array(schema: dict[str, Any], *, value: object, path: str) -> list[str]:
    if not isinstance(value, list):
        return [f"{_format_path(path)} must be an array"]

    problems: list[str] = []
    min_items = schema.get("minItems")
    if isinstance(min_items, int) and len(value) < min_items:
        problems.append(
            f"{_format_path(path)} must contain at least {min_items} item(s)"
        )

    item_schema = schema.get("items")
    if isinstance(item_schema, dict):
        for index, item in enumerate(value):
            problems.extend(
                _validate_schema(item_schema, value=item, path=f"{path}[{index}]")
            )

    return problems


def _validate_string(schema: dict[str, Any], *, value: object, path: str) -> list[str]:
    if not isinstance(value, str):
        return [f"{_format_path(path)} must be a string"]

    problems: list[str] = []
    min_length = schema.get("minLength")
    if isinstance(min_length, int) and len(value) < min_length:
        problems.append(
            f"{_format_path(path)} must be at least {min_length} character(s) long"
        )

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
        problems.append(
            f"{_format_path(path)} must be one of {', '.join(map(str, enum_values))}"
        )

    return problems


def _validate_integer(schema: dict[str, Any], *, value: object, path: str) -> list[str]:
    if isinstance(value, bool) or not isinstance(value, int):
        return [f"{_format_path(path)} must be an integer"]

    problems: list[str] = []
    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
        problems.append(
            f"{_format_path(path)} must be one of {', '.join(map(str, enum_values))}"
        )
    return problems


def _validate_boolean(schema: dict[str, Any], *, value: object, path: str) -> list[str]:
    if not isinstance(value, bool):
        return [f"{_format_path(path)} must be a boolean"]

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
        return [
            f"{_format_path(path)} must be one of {', '.join(map(str, enum_values))}"
        ]
    return []


def _format_path(path: str) -> str:
    if path.startswith("result."):
        return path[len("result.") :]
    if path == "result":
        return "result"
    return path


def _path_suffix(path: str, key: str) -> str:
    formatted = _format_path(path)
    if formatted == "result":
        return key
    return f"{formatted}.{key}"
