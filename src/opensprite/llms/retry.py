"""Provider-agnostic retry delay helpers for transient LLM failures."""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any


_TRANSIENT_ERROR_TYPE_NAMES = frozenset(
    {
        "TimeoutError",
        "ReadTimeout",
        "WriteTimeout",
        "ConnectTimeout",
        "PoolTimeout",
        "ConnectError",
        "ReadError",
        "WriteError",
        "RemoteProtocolError",
        "APIConnectionError",
        "APITimeoutError",
    }
)
_TRANSIENT_ERROR_TEXT_MARKERS = (
    "timed out",
    "timeout",
    "connection reset",
    "connection aborted",
    "connection refused",
    "temporary failure",
    "temporarily unavailable",
    "remote protocol error",
    "stream closed",
    "transport closed",
    "server disconnected",
    "name resolution",
    "dns",
    "high traffic detected",
    "overloaded_error",
)
_DEFAULT_RETRY_BASE_MS = 1000
_DEFAULT_RETRY_MAX_MS = 30_000
_DEFAULT_RETRY_JITTER_RATIO = 0.5


@dataclass(frozen=True)
class RetryDelay:
    retryable: bool
    retry_after_ms: int | None = None
    next_retry_at: float | None = None
    reason: str = ""


def _headers_from_error(error: BaseException) -> dict[str, Any]:
    headers = getattr(error, "headers", None)
    response = getattr(error, "response", None)
    if headers is None and response is not None:
        headers = getattr(response, "headers", None)
    if not headers:
        return {}
    try:
        return {str(key).lower(): value for key, value in dict(headers).items()}
    except Exception:
        return {}


def _status_code_from_error(error: BaseException) -> int | None:
    for source in (error, getattr(error, "response", None)):
        if source is None:
            continue
        value = getattr(source, "status_code", None) or getattr(source, "status", None)
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _parse_seconds(value: Any) -> int | None:
    try:
        seconds = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return max(0, int(seconds * 1000))


def _parse_http_date_ms(value: Any, *, now: float) -> int | None:
    try:
        parsed = parsedate_to_datetime(str(value).strip())
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, int((parsed.timestamp() - now) * 1000))


def _parse_text_delay_ms(text: str) -> int | None:
    normalized = str(text or "")
    patterns = (
        r"retry(?:ing)?\s+(?:after|in)\s+(\d+(?:\.\d+)?)\s*(ms|milliseconds?|s|sec|seconds?)",
        r"try\s+again\s+in\s+(\d+(?:\.\d+)?)\s*(ms|milliseconds?|s|sec|seconds?)",
        r"rate\s+limit[^\d]*(\d+(?:\.\d+)?)\s*(ms|milliseconds?|s|sec|seconds?)",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if not match:
            continue
        value = float(match.group(1))
        unit = match.group(2).lower()
        return max(0, int(value if unit.startswith("ms") or unit.startswith("millisecond") else value * 1000))
    return None


def _looks_like_transient_transport(error: BaseException) -> bool:
    if type(error).__name__ in _TRANSIENT_ERROR_TYPE_NAMES:
        return True
    lowered = str(error or "").lower()
    return any(marker in lowered for marker in _TRANSIENT_ERROR_TEXT_MARKERS)


def _jittered_default_delay_ms(attempt: int) -> int:
    exponent = max(0, int(attempt) - 1)
    delay = min(_DEFAULT_RETRY_BASE_MS * (2 ** exponent), _DEFAULT_RETRY_MAX_MS)
    jitter = random.random() * _DEFAULT_RETRY_JITTER_RATIO * delay
    return int(delay + jitter)


def retry_delay_from_error(error: BaseException, *, now: float | None = None, attempt: int = 1) -> RetryDelay:
    """Return retry metadata for rate-limit and transient provider failures."""
    current_time = time.time() if now is None else float(now)
    headers = _headers_from_error(error)
    reason = str(error or "")

    retry_after_ms = None
    if "retry-after-ms" in headers:
        try:
            retry_after_ms = max(0, int(float(str(headers["retry-after-ms"]).strip())))
        except (TypeError, ValueError):
            retry_after_ms = None
    if retry_after_ms is None and "retry-after" in headers:
        retry_after_ms = _parse_seconds(headers["retry-after"])
        if retry_after_ms is None:
            retry_after_ms = _parse_http_date_ms(headers["retry-after"], now=current_time)
    if retry_after_ms is None:
        retry_after_ms = _parse_text_delay_ms(reason)

    status_code = _status_code_from_error(error)
    retryable = (
        retry_after_ms is not None
        or status_code == 429
        or (status_code is not None and 500 <= status_code <= 599)
        or _looks_like_transient_transport(error)
    )
    if retry_after_ms is None and retryable:
        retry_after_ms = _jittered_default_delay_ms(attempt)
    if not retryable:
        return RetryDelay(retryable=False, reason=reason)
    return RetryDelay(
        retryable=True,
        retry_after_ms=retry_after_ms,
        next_retry_at=current_time + ((retry_after_ms or 0) / 1000),
        reason=reason,
    )
