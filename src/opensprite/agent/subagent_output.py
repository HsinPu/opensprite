"""Structured output parsing for read-only or research subagents."""

from __future__ import annotations

import json
import re
from typing import Any

from ..utils.json_safe import json_safe_value

STRUCTURED_SUBAGENT_SCHEMA_VERSION = 1
READONLY_SUBAGENT_RESULT_CONTRACT = "readonly_subagent_result"
ALLOWED_STRUCTURED_SUBAGENT_STATUSES = frozenset({"ok", "needs_input", "inconclusive"})
MAX_STRUCTURED_SUBAGENT_SUMMARY_CHARS = 280
MAX_STRUCTURED_SUBAGENT_TEXT_CHARS = 500
MAX_STRUCTURED_SUBAGENT_SECTIONS = 8
MAX_STRUCTURED_SUBAGENT_ITEMS_PER_SECTION = 12
MAX_STRUCTURED_SUBAGENT_QUESTIONS = 8
MAX_STRUCTURED_SUBAGENT_RESIDUAL_RISKS = 8
MAX_STRUCTURED_SUBAGENT_SOURCES = 12
_JSON_FENCE_RE = re.compile(r"```json\s*(?P<body>.*?)\s*```", re.IGNORECASE | re.DOTALL)


def parse_structured_subagent_output(
    text: str,
    *,
    prompt_type: str,
) -> tuple[str, dict[str, Any] | None, str | None]:
    """Return visible text plus optional structured payload parsed from a trailing JSON block."""
    raw_text = str(text or "")
    visible_text, raw_json = _split_trailing_json_block(raw_text)
    if raw_json is None:
        return visible_text, None, None

    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        return _fallback_visible_text(visible_text, raw_text), None, f"invalid_json: {exc.msg}"

    normalized, error = _normalize_structured_payload(payload, prompt_type=prompt_type, fallback_text=visible_text)
    if normalized is None:
        return _fallback_visible_text(visible_text, raw_text), None, error
    return _fallback_visible_text(visible_text, raw_text) or normalized["summary"], normalized, None


def build_structured_subagent_contract_instructions(prompt_type: str) -> str:
    """Return shared prompt instructions for the structured readonly subagent contract."""
    normalized_prompt_type = str(prompt_type or "subagent").strip() or "subagent"
    return (
        "## Structured Output Contract\n\n"
        "After your normal human-readable answer, append one final fenced `json` block and do not output anything after it. "
        "Keep the human-readable answer useful on its own because the JSON block is optional machine-readable metadata.\n\n"
        "Rules:\n"
        f"- The JSON block must be one object with `schema_version: 1`, `contract: \"{READONLY_SUBAGENT_RESULT_CONTRACT}\"`, and `prompt_type: \"{normalized_prompt_type}\"`.\n"
        "- `status` must be one of `ok`, `needs_input`, or `inconclusive`.\n"
        "- `summary` must be one concise conclusion sentence.\n"
        "- Put main structured content in `sections`, using stable keys and one of these `type` values when applicable: `finding_list`, `bullet_list`, `outline`, `api_surface`, `pattern_matches`, `fact_check`.\n"
        "- Use `questions` only for concrete missing-input questions.\n"
        "- Use `residual_risks` only for unverified assumptions, blind spots, or remaining uncertainty.\n"
        "- Use `sources` only for concrete evidence you actually inspected.\n"
        "- Do not wrap the whole answer in JSON. Only the final fenced block should be JSON.\n\n"
        "Template:\n"
        "```json\n"
        "{\n"
        '  "schema_version": 1,\n'
        f'  "contract": "{READONLY_SUBAGENT_RESULT_CONTRACT}",\n'
        f'  "prompt_type": "{normalized_prompt_type}",\n'
        '  "status": "ok",\n'
        '  "summary": "...",\n'
        '  "sections": [\n'
        '    {\n'
        '      "key": "main",\n'
        '      "title": "Main Results",\n'
        '      "type": "bullet_list",\n'
        '      "items": ["..."]\n'
        '    }\n'
        '  ],\n'
        '  "questions": [],\n'
        '  "residual_risks": [],\n'
        '  "sources": []\n'
        "}\n"
        "```"
    )


def _split_trailing_json_block(text: str) -> tuple[str, str | None]:
    last_match = None
    for match in _JSON_FENCE_RE.finditer(str(text or "")):
        last_match = match
    if last_match is None:
        return str(text or "").strip(), None
    visible = (str(text or "")[: last_match.start()] + str(text or "")[last_match.end():]).strip()
    return visible, last_match.group("body").strip()


def _fallback_visible_text(visible_text: str, raw_text: str) -> str:
    text = str(visible_text or "").strip() or str(raw_text or "").strip()
    return _bounded_text(text, MAX_STRUCTURED_SUBAGENT_TEXT_CHARS)


def _normalize_structured_payload(
    payload: Any,
    *,
    prompt_type: str,
    fallback_text: str,
) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(payload, dict):
        return None, "payload_must_be_object"
    if int(payload.get("schema_version") or 0) != STRUCTURED_SUBAGENT_SCHEMA_VERSION:
        return None, "schema_version_mismatch"
    if str(payload.get("contract") or "").strip() != READONLY_SUBAGENT_RESULT_CONTRACT:
        return None, "contract_mismatch"

    payload_prompt_type = str(payload.get("prompt_type") or "").strip()
    if payload_prompt_type and payload_prompt_type != str(prompt_type or "").strip():
        return None, "prompt_type_mismatch"

    truncated = False
    status = str(payload.get("status") or "inconclusive").strip() or "inconclusive"
    if status not in ALLOWED_STRUCTURED_SUBAGENT_STATUSES:
        status = "inconclusive"
        truncated = True

    summary = _bounded_text(str(payload.get("summary") or "").strip() or _first_nonempty_line(fallback_text), MAX_STRUCTURED_SUBAGENT_SUMMARY_CHARS)
    if summary != str(payload.get("summary") or "").strip():
        truncated = truncated or bool(str(payload.get("summary") or "").strip())

    sections, sections_truncated = _normalize_sections(payload.get("sections"))
    questions, questions_truncated = _normalize_string_list(payload.get("questions"), limit=MAX_STRUCTURED_SUBAGENT_QUESTIONS)
    residual_risks, residual_risks_truncated = _normalize_string_list(
        payload.get("residual_risks") or payload.get("residualRisks"),
        limit=MAX_STRUCTURED_SUBAGENT_RESIDUAL_RISKS,
    )
    sources, sources_truncated = _normalize_sources(payload.get("sources"))
    truncated = truncated or sections_truncated or questions_truncated or residual_risks_truncated or sources_truncated

    item_count = sum(len(section.get("items", [])) for section in sections)
    finding_count = sum(len(section.get("items", [])) for section in sections if section.get("type") == "finding_list")
    return {
        "schema_version": STRUCTURED_SUBAGENT_SCHEMA_VERSION,
        "contract": READONLY_SUBAGENT_RESULT_CONTRACT,
        "prompt_type": str(prompt_type or "").strip() or None,
        "status": status,
        "summary": summary,
        "sections": sections,
        "section_count": len(sections),
        "item_count": item_count,
        "finding_count": finding_count,
        "questions": questions,
        "question_count": len(questions),
        "residual_risks": residual_risks,
        "residual_risk_count": len(residual_risks),
        "sources": sources,
        "source_count": len(sources),
        "truncated": truncated,
    }, None


def _normalize_sections(value: Any) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(value, list):
        return [], False
    truncated = len(value) > MAX_STRUCTURED_SUBAGENT_SECTIONS
    sections: list[dict[str, Any]] = []
    for index, section in enumerate(value[:MAX_STRUCTURED_SUBAGENT_SECTIONS], start=1):
        if not isinstance(section, dict):
            truncated = True
            continue
        key = _bounded_text(str(section.get("key") or f"section_{index}"), 64)
        title = _bounded_text(str(section.get("title") or key), 120)
        section_type = _bounded_text(str(section.get("type") or "bullet_list"), 64)
        items_value = section.get("items")
        items: list[Any] = []
        if isinstance(items_value, list):
            truncated = truncated or len(items_value) > MAX_STRUCTURED_SUBAGENT_ITEMS_PER_SECTION
            for item in items_value[:MAX_STRUCTURED_SUBAGENT_ITEMS_PER_SECTION]:
                normalized = _bounded_json_value(item)
                if normalized in (None, "", [], {}):
                    continue
                items.append(normalized)
        elif items_value not in (None, ""):
            truncated = True
        sections.append(
            {
                "key": key,
                "title": title,
                "type": section_type,
                "items": items,
            }
        )
    return sections, truncated


def _normalize_string_list(value: Any, *, limit: int) -> tuple[list[str], bool]:
    if not isinstance(value, list):
        return [], False
    truncated = len(value) > limit
    items = [
        _bounded_text(str(item or "").strip(), MAX_STRUCTURED_SUBAGENT_TEXT_CHARS)
        for item in value[:limit]
        if str(item or "").strip()
    ]
    return items, truncated


def _normalize_sources(value: Any) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(value, list):
        return [], False
    truncated = len(value) > MAX_STRUCTURED_SUBAGENT_SOURCES
    items: list[dict[str, Any]] = []
    for source in value[:MAX_STRUCTURED_SUBAGENT_SOURCES]:
        if not isinstance(source, dict):
            truncated = True
            continue
        normalized = {
            "kind": _bounded_text(str(source.get("kind") or "unknown"), 32),
            "path": _bounded_text(str(source.get("path") or ""), 240),
            "title": _bounded_text(str(source.get("title") or ""), 160),
            "url": _bounded_text(str(source.get("url") or ""), 240),
            "start_line": _non_negative_int(source.get("start_line") or source.get("startLine")),
            "end_line": _non_negative_int(source.get("end_line") or source.get("endLine")),
        }
        items.append({key: value for key, value in normalized.items() if value not in (None, "", 0)})
    return items, truncated


def _bounded_json_value(value: Any) -> Any:
    safe = json_safe_value(value)
    if isinstance(safe, str):
        return _bounded_text(safe, MAX_STRUCTURED_SUBAGENT_TEXT_CHARS)
    if isinstance(safe, list):
        return [_bounded_json_value(item) for item in safe[:MAX_STRUCTURED_SUBAGENT_ITEMS_PER_SECTION]]
    if isinstance(safe, dict):
        limited: dict[str, Any] = {}
        for index, (key, item) in enumerate(safe.items()):
            if index >= 12:
                break
            limited[_bounded_text(str(key), 64)] = _bounded_json_value(item)
        return limited
    return safe


def _bounded_text(text: str, max_chars: int) -> str:
    value = str(text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "..."


def _first_nonempty_line(text: str) -> str:
    for line in str(text or "").splitlines():
        candidate = str(line or "").strip()
        if candidate:
            return candidate
    return ""


def _non_negative_int(value: Any) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, number)
