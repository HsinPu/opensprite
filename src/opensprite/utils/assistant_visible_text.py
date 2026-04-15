"""Helpers for stripping provider-internal assistant scaffolding."""

from __future__ import annotations

import re


FENCED_CODE_RE = re.compile(r"(^|\n)(```|~~~)[^\n]*\n[\s\S]*?(?:\n\2|$)")
INLINE_CODE_RE = re.compile(r"`+[^`]+`+")
QUICK_INTERNAL_TAG_RE = re.compile(r"<\s*/?\s*(?:think(?:ing)?|system-reminder)\b", re.IGNORECASE)
THINKING_TAG_RE = re.compile(r"<\s*(/?)\s*(?:think(?:ing)?)\b[^<>]*>", re.IGNORECASE)
SYSTEM_REMINDER_TAG_RE = re.compile(r"<\s*(/?)\s*system-reminder\b[^<>]*>", re.IGNORECASE)


def _find_code_regions(text: str) -> list[tuple[int, int]]:
    """Return fenced and inline code regions that should be preserved verbatim."""
    regions: list[tuple[int, int]] = []

    for match in FENCED_CODE_RE.finditer(text):
        prefix = match.group(1)
        start = match.start() + len(prefix)
        end = match.end()
        regions.append((start, end))

    for match in INLINE_CODE_RE.finditer(text):
        start = match.start()
        end = match.end()
        inside_fenced = any(start >= region_start and end <= region_end for region_start, region_end in regions)
        if not inside_fenced:
            regions.append((start, end))

    regions.sort()
    return regions


def _is_inside_code(position: int, code_regions: list[tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in code_regions)


def _strip_tag_blocks(text: str, tag_re: re.Pattern[str]) -> str:
    """Strip matched XML-ish blocks outside code regions.

    This mirrors the openclaw approach: scan tags, preserve code fences/literals,
    and treat unclosed opening tags as hidden content to be removed.
    """
    if not text:
        return text

    code_regions = _find_code_regions(text)
    result: list[str] = []
    last_index = 0
    in_block = False

    for match in tag_re.finditer(text):
        index = match.start()
        is_close = match.group(1) == "/"

        if _is_inside_code(index, code_regions):
            continue

        if not in_block:
            result.append(text[last_index:index])
            if not is_close:
                in_block = True
        elif is_close:
            in_block = False

        last_index = match.end()

    if not in_block:
        result.append(text[last_index:])

    return "".join(result)


def strip_assistant_internal_scaffolding(text: str) -> str:
    """Remove internal assistant control blocks from visible text."""
    if not text or not QUICK_INTERNAL_TAG_RE.search(text):
        return text or ""

    cleaned = _strip_tag_blocks(text, THINKING_TAG_RE)
    cleaned = _strip_tag_blocks(cleaned, SYSTEM_REMINDER_TAG_RE)
    return cleaned


def sanitize_assistant_visible_text(text: str) -> str:
    """Return user-visible assistant text after stripping internal blocks."""
    return strip_assistant_internal_scaffolding(text).strip()
