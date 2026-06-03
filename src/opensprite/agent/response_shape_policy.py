"""Shared response-shape checks for deterministic quality gates."""

from __future__ import annotations

import re


ITEMIZED_RESPONSE_LINE_RE = re.compile(r"^(?:[-*]|\d+[.)]|\|)")


def normalized_response_text(response_text: str | None) -> str:
    return re.sub(r"\s+", " ", str(response_text or "").strip())


def response_item_count(response_text: str | None) -> int:
    lines = [line.strip() for line in str(response_text or "").splitlines() if line.strip()]
    return sum(1 for line in lines if ITEMIZED_RESPONSE_LINE_RE.match(line))


def response_has_minimum_text_length(response_text: str | None, min_chars: int) -> bool:
    return len(normalized_response_text(response_text)) >= max(1, int(min_chars or 1))
