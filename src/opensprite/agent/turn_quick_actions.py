"""Shared quick-action markers for turn metadata."""

from __future__ import annotations

from typing import Any

QUICK_ACTION_METADATA_KEY = "quick_action"
RESUME_FOLLOW_UP_QUICK_ACTION = "resume_follow_up"
RUN_VERIFICATION_QUICK_ACTION = "run_verification"


def metadata_requests_follow_up_resume(metadata: dict[str, Any]) -> bool:
    return _quick_action(metadata) == RESUME_FOLLOW_UP_QUICK_ACTION


def metadata_requests_direct_verification(metadata: dict[str, Any]) -> bool:
    return _quick_action(metadata) == RUN_VERIFICATION_QUICK_ACTION


def _quick_action(metadata: dict[str, Any]) -> str:
    return str(metadata.get(QUICK_ACTION_METADATA_KEY) or "").strip()
