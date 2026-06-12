"""Small value normalization helpers for completion checks."""

from __future__ import annotations

from typing import Any


DEFAULT_TRUE_VALUES = frozenset({"1", "true", "yes", "y"})
QUALITY_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def coerce_text(value: Any, *, max_chars: int | None = None) -> str:
    text = str(value or "").strip()
    return truncate(text, max_chars=max_chars) if max_chars is not None else text


def truncate(text: str, *, max_chars: int) -> str:
    value = str(text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rstrip() + "..."


def coerce_bool(value: Any, *, truthy_values: frozenset[str] = DEFAULT_TRUE_VALUES) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in truthy_values


def coerce_int(value: object, *, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def coerce_non_negative_int(value: Any) -> int:
    return max(0, coerce_int(value, default=0))


def coerce_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


def string_list(value: Any, *, max_items: int, max_chars: int) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    out: list[str] = []
    for item in values:
        text = coerce_text(item, max_chars=max_chars)
        if text:
            out.append(text)
        if len(out) >= max_items:
            break
    return out
