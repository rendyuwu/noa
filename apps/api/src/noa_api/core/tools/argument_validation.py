from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from noa_api.core.tools.registry import ToolDefinition


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ToolArgumentValidationError(Exception):
    def __init__(self, *, details: list[str]) -> None:
        super().__init__("Tool arguments are invalid")
        self.error = "Tool arguments are invalid"
        self.error_code = "invalid_tool_arguments"
        self.details = tuple(details)

    def as_result(self) -> dict[str, object]:
        return {
            "error": self.error,
            "error_code": self.error_code,
            "details": list(self.details),
        }


def validate_tool_arguments(*, tool: ToolDefinition, args: dict[str, object]) -> None:
    problems = _validate_schema(tool.parameters_schema, value=args, path="args")
    if problems:
        raise ToolArgumentValidationError(details=problems[:10])


def _validate_schema(schema: dict[str, Any], *, value: object, path: str) -> list[str]:
    schema_type = schema.get("type")

    if schema_type == "object":
        return _validate_object(schema, value=value, path=path)
    if schema_type == "array":
        return _validate_array(schema, value=value, path=path)
    if schema_type == "string":
        return _validate_string(schema, value=value, path=path)
    if schema_type == "integer":
        return _validate_integer(schema, value=value, path=path)

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

    if schema.get("uniqueItems") is True:
        duplicates = _duplicate_values(value)
        if duplicates:
            rendered = ", ".join(f"'{item}'" for item in duplicates[:3])
            problems.append(
                f"{_format_path(path)} must not contain duplicate values: {rendered}"
            )

    return problems


def _validate_string(schema: dict[str, Any], *, value: object, path: str) -> list[str]:
    if not isinstance(value, str):
        return [f"{_format_path(path)} must be a string"]

    problems: list[str] = []
    normalized = value.strip()
    min_length = schema.get("minLength")
    if isinstance(min_length, int) and len(normalized) < min_length:
        if min_length == 1:
            problems.append(f"{_format_path(path)} must not be blank")
        else:
            problems.append(
                f"{_format_path(path)} must be at least {min_length} character(s) long"
            )

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
        problems.append(
            f"{_format_path(path)} must be one of {', '.join(map(str, enum_values))}"
        )

    if schema.get("format") == "email" and not _EMAIL_RE.match(normalized):
        problems.append(f"{_format_path(path)} must be a valid email address")

    return problems


def _validate_integer(schema: dict[str, Any], *, value: object, path: str) -> list[str]:
    if isinstance(value, bool) or not isinstance(value, int):
        return [f"{_format_path(path)} must be an integer"]

    problems: list[str] = []
    minimum = schema.get("minimum")
    if isinstance(minimum, int) and value < minimum:
        problems.append(
            f"{_format_path(path)} must be greater than or equal to {minimum}"
        )
    maximum = schema.get("maximum")
    if isinstance(maximum, int) and value > maximum:
        problems.append(f"{_format_path(path)} must be less than or equal to {maximum}")
    return problems


def _format_path(path: str) -> str:
    if path.startswith("args."):
        return path[len("args.") :]
    if path == "args":
        return "arguments"
    return path


def _path_suffix(path: str, key: str) -> str:
    formatted = _format_path(path)
    if formatted == "arguments":
        return key
    return f"{formatted}.{key}"


def _duplicate_values(values: list[object]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        normalized = _normalize_unique_value(value)
        if normalized is None:
            continue
        if normalized in seen and normalized not in duplicates:
            duplicates.append(normalized)
            continue
        seen.add(normalized)
    return duplicates


def _normalize_unique_value(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return None
