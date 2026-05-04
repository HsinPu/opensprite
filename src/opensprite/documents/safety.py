"""Safety checks for durable memory documents."""

from __future__ import annotations

import re


class DurableMemorySafetyError(ValueError):
    """Raised when durable memory content looks unsafe to persist."""


_INVISIBLE_CHARS = {
    "\u200b",
    "\u200c",
    "\u200d",
    "\u2060",
    "\ufeff",
    "\u202a",
    "\u202b",
    "\u202c",
    "\u202d",
    "\u202e",
}

_THREAT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"ignore\s+(previous|all|above|prior)\s+instructions", "prompt_injection"),
    (r"disregard\s+(your|all|any)\s+(instructions|rules|guidelines)", "disregard_rules"),
    (r"system\s+prompt\s+override", "system_prompt_override"),
    (r"you\s+are\s+now\s+", "role_hijack"),
    (r"do\s+not\s+tell\s+the\s+user", "deception_hide"),
    (r"curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)", "exfil_curl"),
    (r"wget\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)", "exfil_wget"),
    (r"cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)", "read_secrets"),
    (r"authorized_keys", "ssh_backdoor"),
)


def scan_durable_memory_text(content: str) -> str | None:
    """Return a block reason when content is unsafe to persist, otherwise None."""
    text = str(content or "")
    for char in _INVISIBLE_CHARS:
        if char in text:
            return f"content contains invisible unicode character U+{ord(char):04X}"

    for pattern, reason in _THREAT_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return f"content matches durable-memory threat pattern '{reason}'"

    return None


def validate_durable_memory_text(content: str) -> None:
    """Raise when durable memory content should not be written."""
    reason = scan_durable_memory_text(content)
    if reason:
        raise DurableMemorySafetyError(
            f"Blocked unsafe durable memory write: {reason}. "
            "Durable memory is injected into future prompts and must not contain injection, "
            "exfiltration, backdoor, or invisible-control payloads."
        )
