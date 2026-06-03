"""Fallback formatting helpers for the execution loop."""

from __future__ import annotations


def format_repeated_invalid_tool_call_content(template: str | None, result: str) -> str:
    cleaned_template = str(template or "").strip()
    cleaned_result = str(result or "").strip()
    if not cleaned_template:
        return cleaned_result
    try:
        return cleaned_template.format(result=result)
    except (KeyError, IndexError, ValueError):
        return f"{cleaned_template}\n\n{result}"
