"""Shared verification tool and artifact policy helpers."""

from __future__ import annotations


VERIFICATION_TOOL_NAME = "verify"
VERIFICATION_RESULT_ARTIFACT_KIND = "verification_result"
VERIFICATION_STATUS_METADATA_FIELD = "verification_status"
VERIFICATION_NAME_METADATA_FIELD = "verification_name"
SKIPPED_VERIFICATION_STATUS = "skipped"
REQUIRED_VERIFICATION_FAILED_REASON = "required verification did not pass"
REQUIRED_VERIFICATION_NOT_RECORDED_REASON = "required verification was not recorded"
VERIFICATION_OUTCOME_OR_GAP_MISSING_REASON = "verification outcome or gap was not reported"


def is_verification_tool_name(tool_name: str | None) -> bool:
    """Return whether a tool name represents the verification tool."""
    return str(tool_name or "").strip() == VERIFICATION_TOOL_NAME


def is_verification_result_artifact_kind(kind: str | None) -> bool:
    """Return whether an artifact kind represents verification output."""
    return str(kind or "").strip() == VERIFICATION_RESULT_ARTIFACT_KIND


def required_verification_completion_reason(*, verification_attempted: bool) -> str:
    """Return the completion-gate reason for an unmet required verification."""
    return REQUIRED_VERIFICATION_FAILED_REASON if verification_attempted else REQUIRED_VERIFICATION_NOT_RECORDED_REASON
