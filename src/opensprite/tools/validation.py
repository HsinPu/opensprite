"""Runtime tool parameter validation helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

NON_EMPTY_STRING_PATTERN = r"\S"


@dataclass(frozen=True)
class ValidationIssue:
    kind: str
    path: str
    message: str


def format_param_preview(params: Any, max_chars: int = 240) -> str:
    """Return a compact JSON-ish preview for diagnostics."""
    try:
        text = json.dumps(params, ensure_ascii=False)
    except Exception:
        text = repr(params)
    text = text.replace("\n", "\\n")
    if len(text) > max_chars:
        return text[: max_chars - 3] + "..."
    return text


def _join_path(path: str, key: str) -> str:
    return key if not path else f"{path}.{key}"


def _value_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _schema_types(schema: dict[str, Any]) -> tuple[str, ...]:
    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        return (schema_type,)
    if isinstance(schema_type, list):
        return tuple(item for item in schema_type if isinstance(item, str))
    if isinstance(schema.get("properties"), dict):
        return ("object",)
    return ()


def _schema_allows_null(schema: dict[str, Any]) -> bool:
    if schema.get("nullable") is True:
        return True
    return "null" in _schema_types(schema)


def _matches_schema_type(schema_type: str, value: Any) -> bool:
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return (isinstance(value, int) or isinstance(value, float)) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "null":
        return value is None
    return True


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _type_error_message(path: str, expected: tuple[str, ...], actual: str) -> str:
    if len(expected) == 1:
        return f"{path} must be {expected[0]}, got {actual}"
    return f"{path} must be one of: {', '.join(expected)}; got {actual}"


def _non_empty_string_message(path: str) -> str:
    return f"{path} must be a non-empty string"


def _validate_string(schema: dict[str, Any], value: str, path: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    min_length = schema.get("minLength")
    if isinstance(min_length, int) and len(value) < min_length:
        issues.append(ValidationIssue("invalid", path, f"{path} must be at least {min_length} characters"))

    max_length = schema.get("maxLength")
    if isinstance(max_length, int) and len(value) > max_length:
        issues.append(ValidationIssue("invalid", path, f"{path} must be at most {max_length} characters"))

    pattern = schema.get("pattern")
    if isinstance(pattern, str):
        if pattern == NON_EMPTY_STRING_PATTERN and not value.strip():
            issues.append(ValidationIssue("invalid", path, _non_empty_string_message(path)))
        else:
            try:
                if re.search(pattern, value) is None:
                    issues.append(ValidationIssue("invalid", path, f"{path} must match pattern /{pattern}/"))
            except re.error:
                pass

    return issues


def _validate_value(
    schema: dict[str, Any],
    value: Any,
    *,
    path: str,
    required: bool,
) -> list[ValidationIssue]:
    if value is None:
        if _schema_allows_null(schema):
            return []
        if required:
            return [ValidationIssue("missing", path, path)]
        schema_types = tuple(schema_type for schema_type in _schema_types(schema) if schema_type != "null")
        if schema_types:
            return [ValidationIssue("invalid", path, _type_error_message(path, schema_types, "null"))]
        return []

    schema_types = _schema_types(schema)
    matching_type = next((schema_type for schema_type in schema_types if _matches_schema_type(schema_type, value)), None)
    if schema_types and matching_type is None:
        actual = _value_type_name(value)
        return [ValidationIssue("invalid", path, _type_error_message(path, schema_types, actual))]

    issues: list[ValidationIssue] = []
    if isinstance(schema.get("enum"), list) and value not in schema["enum"]:
        allowed = ", ".join(str(item) for item in schema["enum"])
        issues.append(ValidationIssue("invalid", path, f"{path} must be one of: {allowed}"))

    if matching_type == "string":
        issues.extend(_validate_string(schema, value, path))
    elif matching_type == "object" and isinstance(value, dict):
        properties = schema.get("properties")
        required_keys = schema.get("required") or []
        if isinstance(properties, dict):
            for key in required_keys:
                if isinstance(key, str) and key not in value:
                    child_path = _join_path(path, key)
                    issues.append(ValidationIssue("missing", child_path, child_path))
            for key, child_schema in properties.items():
                if key not in value or not isinstance(child_schema, dict):
                    continue
                issues.extend(
                    _validate_value(
                        child_schema,
                        value[key],
                        path=_join_path(path, key),
                        required=key in required_keys,
                    )
                )
    elif matching_type == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                issues.extend(
                    _validate_value(
                        item_schema,
                        item,
                        path=f"{path}[{index}]",
                        required=True,
                    )
                )

    return issues


def validate_tool_params(name: str, schema: Any, params: Any) -> str | None:
    """Validate tool params against the supported JSON Schema subset."""
    if not isinstance(params, dict):
        return f"Error: Invalid arguments for {name}: expected a JSON object of named arguments."

    normalized_schema = schema if isinstance(schema, dict) else {"type": "object", "properties": {}}
    issues = _validate_value(normalized_schema, params, path="", required=True)
    if not issues:
        return None

    missing = _dedupe([issue.path for issue in issues if issue.kind == "missing" and issue.path])
    invalid = _dedupe([issue.message for issue in issues if issue.kind == "invalid"])
    parts: list[str] = []
    if missing:
        parts.append(f"missing required argument(s): {', '.join(missing)}")
    parts.extend(invalid)
    return f"Error: Invalid arguments for {name}: {'; '.join(parts)}."
