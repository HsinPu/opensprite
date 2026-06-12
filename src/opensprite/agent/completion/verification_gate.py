"""Verification evidence and follow-up helpers for completion gating."""

from __future__ import annotations

from typing import Any

from ...tools.evidence import (
    SKIPPED_VERIFICATION_STATUS,
    VERIFICATION_STATUS_METADATA_FIELD,
    is_verification_result_artifact_kind,
    is_verification_tool_name,
)
from ..execution import ExecutionResult
from ..task.capabilities import VERIFICATION_REQUIREMENT_KIND
from .path_rules import (
    WEB_APP_ROOT_PATH,
    common_verification_path,
    is_python_file_path,
    is_python_test_path,
    is_web_app_path,
    normalized_touched_paths,
    path_requires_delegated_review,
    strip_repo_snapshot_prefix,
)

_SKIPPED_VERIFICATION_STATUS = SKIPPED_VERIFICATION_STATUS
_VERIFICATION_STATUS_METADATA_FIELD = VERIFICATION_STATUS_METADATA_FIELD


def _verification_skipped_with_reported_gap(execution_result: ExecutionResult) -> bool:
    if not execution_result.verification_attempted:
        return False
    if not _requires_delegated_review(execution_result.touched_paths) and not execution_result.had_tool_error:
        return True
    if not _has_skipped_verification_artifact(execution_result):
        return False
    return True


def _has_skipped_verification_artifact(execution_result: ExecutionResult) -> bool:
    for artifact in execution_result.task_artifacts:
        if not is_verification_result_artifact_kind(artifact.kind) or not artifact.ok:
            continue
        if _verification_status_is_skipped(artifact.metadata):
            return True
    for evidence in execution_result.tool_evidence:
        if not is_verification_tool_name(evidence.name) or not evidence.ok:
            continue
        if _verification_status_is_skipped(evidence.metadata):
            return True
    return False


def _verification_status_is_skipped(metadata: Any) -> bool:
    if not isinstance(metadata, dict):
        return False
    return str(metadata.get(_VERIFICATION_STATUS_METADATA_FIELD) or "").strip().lower() == _SKIPPED_VERIFICATION_STATUS


def _requires_delegated_review(touched_paths: tuple[str, ...]) -> bool:
    paths = normalized_touched_paths(touched_paths)
    if not paths:
        return True
    return any(path_requires_delegated_review(path) for path in paths)


def _contract_requires_verification(task_contract: Any) -> bool:
    if any(
        str(getattr(requirement, "kind", "") or "").strip() == VERIFICATION_REQUIREMENT_KIND
        for requirement in getattr(task_contract, "requirements", ()) or ()
    ):
        return True
    return any(
        is_verification_tool_name(tool_name)
        for tool_name in getattr(task_contract, "required_tools", ()) or ()
    )


def _verification_follow_up_fields(
    action: str,
    path: str | None,
    *,
    pytest_args: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "action": action,
        "path": path or ".",
        "pytest_args": pytest_args,
    }


def _verification_follow_up(execution_result: ExecutionResult) -> dict[str, Any]:
    touched_paths = normalized_touched_paths(execution_result.touched_paths)
    decision_paths = tuple(strip_repo_snapshot_prefix(path) for path in touched_paths)
    test_paths = tuple(path for path in decision_paths if is_python_test_path(path))
    has_web_touched = any(is_web_app_path(path) for path in decision_paths)
    has_python_touched = any(is_python_file_path(path) for path in decision_paths)
    if touched_paths and not has_web_touched and not has_python_touched:
        return _verification_follow_up_fields("auto", common_verification_path(touched_paths))
    if has_web_touched:
        return _verification_follow_up_fields("web_build", WEB_APP_ROOT_PATH)
    if test_paths:
        return _verification_follow_up_fields("pytest", ".", pytest_args=test_paths)
    if has_python_touched:
        return _verification_follow_up_fields("python_compile", common_verification_path(touched_paths))
    return _verification_follow_up_fields("auto", common_verification_path(touched_paths))
