"""Shared web access-blocking and challenge detection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WebBlockingRule:
    statuses: frozenset[int]
    text_markers: tuple[str, ...]


WEB_BLOCKING_RULE = WebBlockingRule(
    statuses=frozenset({401, 403, 407, 408, 409, 429, 451, 503}),
    text_markers=(
        "captcha",
        "cloudflare",
        "access denied",
        "forbidden",
        "enable javascript",
        "verify you are human",
        "prove you are human",
        "unusual traffic",
        "too many requests",
    ),
)


def looks_blocked_or_challenge(*, title: str, content: str, status: Any) -> bool:
    """Return whether fetched web content looks like an access block or anti-bot challenge."""
    rule = WEB_BLOCKING_RULE
    if _coerce_status(status) in rule.statuses:
        return True
    normalized = f"{title}\n{content}".lower()
    return any(marker in normalized for marker in rule.text_markers)


def _coerce_status(status: Any) -> int | None:
    try:
        return int(status)
    except (TypeError, ValueError):
        return None
